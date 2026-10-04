"""alienfixd: owns the lighting controllers, exposes a narrow D-Bus API.

- Runs as a confined system service; it is the only process that opens the
  keyboard node, which also carries keystrokes. While the typing ripple is
  on (off by default), it also reads key presses of the built-in keyboard
  (keycodes.py) to place the rings; nothing about them is kept.
- What the machine has (per-key keyboard, zones, which controller drives
  each zone) comes from its model (models.py, data/models/).
- Every mutating call is authorised by polkit, then validated (validate.py),
  then applied by one worker thread per controller. The chassis takes 64 ms
  per command (2 s for the power button), so callers never wait for hardware
  and keyboard frames are never stuck behind chassis work.
- State persists in $STATE_DIRECTORY/state.json and is re-applied at start
  and after resume (logind PrepareForSleep).
"""
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from gi.repository import Gio, GLib

from . import effects, keycodes, models, profile, protocol, validate
from .devices import Hardware
from .state import State

NAME = "io.github.zeecka.AlienFix"
PATH = "/io/github/zeecka/AlienFix"
IFACE = "io.github.zeecka.AlienFix1"
ACTION = "io.github.zeecka.alienfix.control"
ERR = IFACE + ".Error."
DATA = Path(os.environ.get("ALIENFIX_DATA", "/usr/local/share/alienfix"))
READ_ONLY = {"GetState", "GetLayout", "ListChassis"}
ALL_IDS = frozenset(range(profile.MAX_ID + 1))
AUTH_TTL = 30.0
# Keyboard effects on a zone keyboard: what each name becomes on ELC zones.
ZONE_KB_EFFECT = {"static": "static", "pulse": "pulse", "breathing": "pulse",
                  "morph": "morph", "wave": "morph", "mixpulse": "morph"}

log = logging.getLogger("alienfixd")


class Worker(threading.Thread):
    """Applies the latest wanted state for one controller; coalesces requests."""

    def __init__(self, name, apply):
        super().__init__(name=name, daemon=True)
        self.apply = apply
        self.cv = threading.Condition()
        self.pending = {}

    def request(self, **what):
        with self.cv:
            frame = what.pop("frame", None)
            if frame is not None:        # merge partial live frames, never drop one:
                self.pending["frame"] = {**self.pending.get("frame", {}), **frame}
            if what.pop("enter", False):
                self.pending["enter"] = True
            self.pending.update(what)
            self.cv.notify()

    def run(self):
        while True:
            with self.cv:
                while not self.pending:
                    self.cv.wait()
                job, self.pending = self.pending, {}
            try:
                self.apply(job)
            except Exception as e:            # keep serving; the next request retries
                log.error("%s: %s", self.name, e)


class Daemon:
    def __init__(self, bus_type):
        state_dir = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/alienfix"))
        self.profile_path = state_dir / "profile.json"
        self.model_path = state_dir / "model"
        self.templates = profile.load_templates(DATA / "chassis")
        self.shipped = [profile.validate(json.loads(p.read_text()), self.templates)
                        for p in sorted((DATA / "profiles").glob("*.json"))]
        self.models = models.load(DATA / "models")
        self.hw = Hardware()
        self._load_model()
        self._load_profile()
        self.state = State(state_dir / "state.json", self.key_ids, self.zone_ids)
        self.lock = threading.Lock()
        self.live_owner = None
        self.live_watch = 0
        self.auth_cache = {}
        self.kb_worker = Worker("keyboard", self._apply_keyboard)
        self.soft_on = False          # keyboard already in per-key mode for a software effect
        self.anim_wake = threading.Event()
        self.ripples = []             # rings in flight (under self.lock)
        self.evdev_names = keycodes.load_evdev_names()
        self.keys = keycodes.KeyWatcher(self._on_key_press, self.hw.keyboard_usb())
        self.elc_worker = Worker("chassis", self._apply_chassis)
        self.bus_type = bus_type
        self.conn = None

    # ---- profile -----------------------------------------------------------

    @staticmethod
    def dmi_product():
        try:
            return Path("/sys/class/dmi/id/product_name").read_text().strip()
        except OSError:
            return ""

    def _load_model(self):
        """The model the user chose, else the one matching DMI, else a generic one."""
        try:
            forced = self.model_path.read_text().strip()
        except OSError:
            forced = ""
        present = self.hw.presence()
        m = models.pick(self.models, self.dmi_product(), forced, present.get("usb", ()))
        self.model_forced = bool(forced) and m is not None and m["id"] == forced
        if m is None:
            m = models.generic(present, self.hw.wmi_zone_count())
        self.model = m
        self.zones = {z["id"]: z for z in m["zones"]}
        self.zone_ids = list(self.zones)
        self.kb_type = m.get("keyboard", {"type": "none"})["type"]
        if getattr(self, "state", None):
            self.state.set_zones(self.zone_ids)
        log.info("model %s (%s, %s keyboard, %d zones)", m["id"], m["support"], self.kb_type, len(self.zones))

    def _load_profile(self):
        """Per-key keyboards: custom profile if present and valid, else the
        shipped one for this model, else an empty one (the wizard fills it)."""
        self.custom = False
        prof = None
        allowed = [t for t in self.model.get("keyboard", {}).get("chassis", []) if t in self.templates]
        if self.kb_type == "per-key":
            try:
                prof = profile.parse(self.profile_path.read_text(), self.templates)
                self.custom = True
            except FileNotFoundError:
                pass
            except (OSError, validate.Invalid) as e:
                log.warning("ignoring stored profile: %s", e)
            if prof is None:
                match = [p for p in self.shipped if p["chassis"] in allowed]
                prof = match[0] if match else None
        if prof is None:              # no key map (yet): draw the model's first template, no ids
            tid = allowed[0] if allowed else next(iter(self.templates))
            prof = {"version": 1, "name": "No key map yet", "chassis": tid, "method": "blink", "keys": {}}
        self.profile = prof
        self.layout = profile.layout(prof, self.templates[prof["chassis"]])
        self.key_ids = {int(k) for k in self.layout["keys"]}
        geo = profile.key_geometry(self.templates[prof["chassis"]])
        self.key_origin = {name: (g["u"], g["v"]) for name, g in geo.items()}   # keys without LED too
        if getattr(self, "state", None):
            self.state.key_ids = sorted(self.key_ids)
        log.info("profile %r on chassis %s (%d ids, %s)", prof["name"], prof["chassis"],
                 len(self.key_ids), "custom" if self.custom else "shipped")

    def _layout_changed(self):
        self.conn.emit_signal(None, PATH, IFACE, "LayoutChanged", None)
        self._changed(keyboard=True)

    # ---- hardware application (worker threads) -------------------------

    def _apply_keyboard(self, job):
        if self.kb_type != "per-key":
            return
        with self.lock:
            d = self.state.data
            enabled, bright = d["enabled"], d["brightness"]
            mode, effect = d["keyboard"]["mode"], dict(d["keyboard"]["effect"])
            colors = self.state.key_colors()
            live = self.live_owner is not None
            soft = self._soft(d)
        if "frame" in job and live:
            self.hw.keyboard_frame(sorted(job["frame"].items()), enter_custom=job.get("enter", False))
            self._count_frame()
            return
        if live:
            self.soft_on = False
            return
        if "soft" in job and not job.get("full"):
            if soft:
                if not self.soft_on:
                    self.hw.keyboard_soft_start(bright)
                    self.soft_on = True
                self.hw.keyboard_frame(sorted(job["soft"].items()))
            return
        self.soft_on = False
        if not enabled:
            self.hw.keyboard_static({i: (0, 0, 0) for i in colors}, 0)
        elif soft:
            self.anim_wake.set()          # the animator draws; next soft frame re-enters the mode
        elif mode == "effect" and effect["name"] != "static":
            self.hw.keyboard_effect(effect, bright)
        else:
            self.hw.keyboard_static(colors, bright)

    def _apply_chassis(self, job):
        with self.lock:
            d = self.state.data
            pct = d["brightness"] if d["enabled"] else 0
            zones = {z: protocol.scale(c, pct) for z, c in d["zones"].items()}
            fx = {z: {**e, "c1": zones[z], "c2": protocol.scale(e["c2"], pct)}
                  for z, e in d["zone_effects"].items()}
            if not d["enabled"]:
                fx = {z: {**e, "name": "static"} for z, e in fx.items()}
            spec = dict(self.zones)
        if job.get("zones"):
            # index order: the m15 R7 sequence verified on camera, byte for byte
            plain = sorted((z for z in spec.values() if z.get("role") != "power"),
                           key=lambda z: (z["controller"], z.get("index", 0), z.get("mask", 0)))
            by = {c: [z for z in plain if z["controller"] == c] for c in models.CONTROLLERS}
            static = [(z["index"], zones[z["id"]]) for z in by["elc"] if fx[z["id"]]["name"] == "static"]
            animated = [(z["index"], fx[z["id"]]) for z in by["elc"] if fx[z["id"]]["name"] != "static"]
            if static:
                self.hw.zones(static)
            if animated:
                self.hw.zone_effects(animated)
            if by["legacy"]:
                self.hw.legacy_zones([(z["mask"], zones[z["id"]]) for z in by["legacy"]])
            if by["wmi"]:
                self.hw.wmi_zones([(z["index"], zones[z["id"]]) for z in by["wmi"]])
        if job.get("power"):
            for z in spec.values():
                if z.get("role") == "power":
                    self.hw.power(zones[z["id"]], fx[z["id"]], index=z["index"])

    # ---- software effects (daemon-rendered keyboard animation) -------------

    def _soft(self, d):
        """Whether the daemon draws the keyboard frame by frame: a software
        effect, or anything under the typing ripple. Caller holds the lock."""
        return (d["enabled"] and self.live_owner is None and self.kb_type == "per-key"
                and (d["ripple"]["enabled"] or (d["keyboard"]["mode"] == "effect"
                                                and d["keyboard"]["effect"]["name"] in effects.SOFTWARE)))

    def _animate(self):
        """Render the keyboard in software (~25 fps while something moves)."""
        current, render, t0, drawn = None, None, time.monotonic(), False
        while True:
            now = time.monotonic()
            with self.lock:
                d = self.state.data
                active = self._soft(d)
                kb, eff, rp = d["keyboard"], dict(d["keyboard"]["effect"]), dict(d["ripple"])
                colors = self.state.key_colors()
                pos = {int(k): (v["u"], v["v"]) for k, v in self.layout["keys"].items()}
                width = self.layout["chassis"]["width"]
                self.ripples = [r for r in self.ripples if now - r.t0 < r.life]
                ripples = list(self.ripples) if rp["enabled"] else []
            if not active:
                current = None
                self.anim_wake.wait(0.5)
                self.anim_wake.clear()
                continue
            if rp["enabled"] and rp["under"] == "color":
                key = ("color", tuple(rp["background"]))
            elif kb["mode"] == "effect":
                key = ("effect", eff["name"], eff["tempo"], tuple(eff["c1"]), tuple(eff["c2"]))
            else:
                key = ("static", tuple(sorted(colors.items())))
            key += (len(pos),)
            if key != current:
                current, t0, drawn = key, now, False
                render = effects.any_renderer(eff["name"]) if key[0] == "effect" else None
            if render:
                base = render(pos, width, now - t0, eff["tempo"], eff["c1"], eff["c2"])
            elif key[0] == "color":
                base = {i: tuple(rp["background"]) for i in pos}
            else:
                base = colors
            moving = bool(ripples) or (render is not None and (eff["name"] in effects.ANIMATED
                                                                or eff["name"] in effects.LOOKALIKE))
            if moving or not drawn or not self.soft_on:
                self.kb_worker.request(soft=effects.ripple_over(base, ripples, now, pos) if ripples else base)
                drawn = not ripples           # once the last ring is gone, draw a clean frame
            if moving:
                time.sleep(0.04)
            else:
                self.anim_wake.wait(0.5)
                self.anim_wake.clear()

    def _on_key_press(self, code):
        """KeyWatcher thread: a ring from the pressed key. The code is dropped here."""
        origin = self.key_origin.get(self.evdev_names.get(code))
        if origin is None:
            return
        with self.lock:
            rp = self.state.data["ripple"]
            if not rp["enabled"]:
                return
            self.ripples.append(effects.Ripple(*origin, time.monotonic(), rp["color"], rp["speed"]))
            del self.ripples[:-effects.RIPPLE_MAX]
        self.anim_wake.set()

    def _sync_keys(self):
        """Read key presses only while a ripple can be drawn."""
        with self.lock:
            d = self.state.data
            want = d["enabled"] and d["ripple"]["enabled"] and self.kb_type == "per-key"
        if want and not self.keys.running:
            self.keys.start()
        elif not want and self.keys.running:
            self.keys.stop()
            with self.lock:
                self.ripples.clear()

    def _count_frame(self):
        """Count live frames that actually reached the hardware."""
        self._live_frames = getattr(self, "_live_frames", 0) + 1

    def apply_all(self):
        self.kb_worker.request(full=True)
        self.elc_worker.request(zones=True, power=True)

    # ---- D-Bus -----------------------------------------------------------

    def run(self):
        self.kb_worker.start()
        self.elc_worker.start()
        threading.Thread(target=self._animate, name="animator", daemon=True).start()
        self.apply_all()
        self._sync_keys()
        bt = Gio.BusType.SESSION if self.bus_type == "session" else Gio.BusType.SYSTEM
        info = Gio.DBusNodeInfo.new_for_xml((DATA / f"{IFACE}.xml").read_text()).interfaces[0]
        self.conn = Gio.bus_get_sync(bt, None)
        self.conn.register_object(PATH, info, self._on_call, None, None)
        Gio.bus_own_name_on_connection(self.conn, NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        if bt == Gio.BusType.SYSTEM:
            self.conn.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager",
                                       "PrepareForSleep", "/org/freedesktop/login1", None,
                                       Gio.DBusSignalFlags.NONE, self._on_sleep)
        loop = GLib.MainLoop()
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, loop.quit)
        log.info("serving %s on the %s bus, devices %s", NAME, self.bus_type, self.hw.presence())
        loop.run()

    def _on_sleep(self, _c, _s, _p, _i, _sig, params):
        (going_down,) = params.unpack()
        if not going_down:
            log.info("resumed: re-applying state")
            GLib.timeout_add(1500, lambda: (self.apply_all(), False)[1])

    def _on_call(self, conn, sender, path, iface, method, params, inv):
        if method in READ_ONLY:
            self._dispatch(sender, method, params, inv)
            return
        cached = self.auth_cache.get(sender)
        if cached and cached > time.monotonic():
            self._dispatch(sender, method, params, inv)
            return
        if self.bus_type == "session":           # test mode: no polkit on the session bus
            self._dispatch(sender, method, params, inv)
            return
        conn.call("org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
                  "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization",
                  GLib.Variant("((sa{sv})sa{ss}us)",
                               (("system-bus-name", {"name": GLib.Variant("s", sender)}), ACTION, {}, 0, "")),
                  GLib.VariantType("((bba{ss}))"), Gio.DBusCallFlags.NONE, 5000, None,
                  self._on_auth, (sender, method, params, inv))

    def _on_auth(self, conn, res, ctx):
        sender, method, params, inv = ctx
        try:
            ((authorized, _challenge, _details),) = conn.call_finish(res).unpack()
        except GLib.Error as e:
            log.warning("polkit error for %s: %s", sender, e.message)
            authorized = False
        if not authorized:
            log.info("denied %s from %s", method, sender)
            inv.return_dbus_error(ERR + "NotAuthorized", "not allowed: active local session required")
            return
        self.auth_cache[sender] = time.monotonic() + AUTH_TTL
        self._dispatch(sender, method, params, inv)

    def _dispatch(self, sender, method, params, inv):
        try:
            out = getattr(self, "m_" + method)(sender, *params.unpack())
        except validate.Invalid as e:
            inv.return_dbus_error(ERR + "InvalidArgs", str(e))
            return
        except Exception as e:
            log.exception("%s failed", method)
            inv.return_dbus_error(ERR + "Failed", str(e))
            return
        inv.return_value(out)

    def _changed(self, keyboard=False, zones=False, power=False):
        with self.lock:
            self.state.save()
            js = self.state.public(self.live_owner is not None, self.hw.presence(), self.kb_type == "per-key")
        if keyboard:
            self.kb_worker.request(full=True)
            self._sync_keys()
        if zones or power:
            self.elc_worker.request(zones=zones, power=power)
        self.conn.emit_signal(None, PATH, IFACE, "StateChanged", GLib.Variant("(s)", (js,)))

    # ---- methods ---------------------------------------------------------

    def m_GetState(self, _s):
        with self.lock:
            js = self.state.public(self.live_owner is not None, self.hw.presence(), self.kb_type == "per-key")
            return GLib.Variant("(s)", (js,))

    def _keyboard_effects(self):
        if self.kb_type == "per-key":
            return list(validate.EFFECTS)
        if self.kb_type == "zones":
            kz = [z for z in self.zones.values() if z.get("group") == "keyboard"]
            common = set.intersection(*(set(models.zone_effects(z)) for z in kz))
            return [n for n, target in ZONE_KB_EFFECT.items() if target in common]
        return []

    def m_GetLayout(self, _s):
        pub = {**self.layout, "model": models.public(self.model), "model_forced": self.model_forced,
               "zones": models.public_zones(self.model), "effects": self._keyboard_effects(),
               "hardware_effects": list(protocol.EFFECTS), "software_effects": list(effects.SOFTWARE),
               "zone_effects": list(validate.ZONE_EFFECTS), "custom": self.custom}
        return GLib.Variant("(s)", (json.dumps(pub, ensure_ascii=False),))

    def m_ListChassis(self, _s):
        out = {"dmi_product": self.dmi_product(), "custom": self.custom,
               "profile": {k: self.profile[k] for k in ("name", "chassis", "method")},
               "model": self.model["id"], "model_forced": self.model_forced,
               "models": [{k: m.get(k) for k in ("id", "name", "support", "dmi_product")}
                          for m in self.models.values()],
               "devices": self.hw.presence(),
               "templates": [{k: t.get(k) for k in ("id", "name", "layout", "verified", "note", "dmi_product")}
                             for t in self.templates.values()]}
        return GLib.Variant("(s)", (json.dumps(out, ensure_ascii=False),))

    def m_SelectModel(self, _s, model_id):
        """"" = automatic (DMI); otherwise one of the shipped model ids."""
        if model_id and model_id not in self.models:
            raise validate.Invalid("model: unknown")
        if model_id:
            tmp = self.model_path.with_suffix(".tmp")
            tmp.write_text(self.models[model_id]["id"])        # our string, not the caller's
            os.replace(tmp, self.model_path)
        else:
            try:
                self.model_path.unlink()
            except FileNotFoundError:
                pass
        with self.lock:
            self._load_model()
            self._load_profile()
        self.conn.emit_signal(None, PATH, IFACE, "LayoutChanged", None)
        self._changed(keyboard=True, zones=True, power=True)

    def _need_per_key(self):
        if self.kb_type != "per-key":
            raise validate.Invalid("this model has no per-key keyboard")

    def _keyboard_zones(self):
        return [z for z in self.zone_ids if self.zones[z].get("group") == "keyboard"]

    def m_SaveProfile(self, _s, text):
        self._need_per_key()
        prof = profile.parse(text, self.templates)
        tmp = self.profile_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prof, indent=1))
        os.replace(tmp, self.profile_path)
        with self.lock:
            self._load_profile()
        self._layout_changed()

    def m_ResetProfile(self, _s):
        try:
            self.profile_path.unlink()
        except FileNotFoundError:
            pass
        with self.lock:
            self._load_profile()
        self._layout_changed()

    def m_SetKeyboardColor(self, _s, r, g, b):
        c = validate.color(r, g, b)
        if self.kb_type == "none":
            raise validate.Invalid("this model has no keyboard lighting")
        with self.lock:
            kb = self.state.data["keyboard"]
            kb.update(mode="static", base=list(c), keys={})
            for z in self._keyboard_zones():
                self.state.data["zones"][z] = list(c)
                self.state.data["zone_effects"][z]["name"] = "static"
        self._changed(keyboard=True, zones=self.kb_type == "zones")

    def m_SetKeys(self, _s, entries):
        self._need_per_key()
        colors = validate.key_colors(entries, self.key_ids)
        with self.lock:
            kb = self.state.data["keyboard"]
            kb["mode"] = "static"
            for kid, c in colors.items():
                kb["keys"][str(kid)] = list(c)
        self._changed(keyboard=True)

    def m_SetKeyboardEffect(self, _s, name, tempo, c1, c2):
        eff = validate.effect(name, tempo, c1, c2)
        if eff["name"] not in self._keyboard_effects():
            raise validate.Invalid("effect: not available on this model's keyboard")
        with self.lock:
            kb = self.state.data["keyboard"]
            kb["mode"] = "static" if eff["name"] == "static" else "effect"
            if eff["name"] != "static":
                kb["effect"] = {**eff, "c1": list(eff["c1"]), "c2": list(eff["c2"])}
            for z in self._keyboard_zones():         # zone keyboard: the controller animates
                self.state.data["zones"][z] = list(eff["c1"])
                self.state.data["zone_effects"][z] = {"name": ZONE_KB_EFFECT[eff["name"]],
                                                      "tempo": eff["tempo"], "c2": list(eff["c2"])}
        self._changed(keyboard=True, zones=self.kb_type == "zones")

    def m_SetBrightness(self, _s, pct):
        p = validate.percent(pct)
        with self.lock:
            self.state.data["brightness"] = p
        self._changed(keyboard=True, zones=True, power=True)

    def _is_power(self, z):
        return self.zones[z].get("role") == "power"

    def m_SetZoneColor(self, _s, zone, r, g, b):
        z, c = validate.zone(zone, self.zone_ids), validate.color(r, g, b)
        with self.lock:
            self.state.data["zones"][z] = list(c)
            self.state.data["zone_effects"][z]["name"] = "static"    # picking a colour = static colour
        self._changed(zones=not self._is_power(z), power=self._is_power(z))

    def m_SetZoneEffect(self, _s, zone, name, tempo, c1, c2):
        z = validate.zone(zone, self.zone_ids)
        eff = validate.zone_effect(name, tempo, c1, c2, models.zone_effects(self.zones[z]))
        with self.lock:
            self.state.data["zones"][z] = list(eff["c1"])
            self.state.data["zone_effects"][z] = {"name": eff["name"], "tempo": eff["tempo"],
                                                  "c2": list(eff["c2"])}
        self._changed(zones=not self._is_power(z), power=self._is_power(z))

    def m_SetEnabled(self, _s, enabled):
        if not isinstance(enabled, bool):
            raise validate.Invalid("enabled: boolean expected")
        with self.lock:
            self.state.data["enabled"] = enabled
        self._changed(keyboard=True, zones=True, power=True)

    def m_SetRipple(self, _s, enabled, rgb, speed, under, background):
        self._need_per_key()
        rp = validate.ripple(enabled, rgb, speed, under, background)
        with self.lock:
            self.state.data["ripple"] = rp
        self._changed(keyboard=True)

    def m_BeginLive(self, sender):
        self._need_per_key()
        with self.lock:
            if self.live_owner not in (None, sender):
                raise validate.Invalid("live mode is held by another client")
            self.live_owner = sender
        self._live_frames, self._live_t0 = 0, time.monotonic()
        if not self.live_watch:
            self.live_watch = Gio.bus_watch_name_on_connection(
                self.conn, sender, Gio.BusNameWatcherFlags.NONE, None, lambda *_: self._end_live())
        self.kb_worker.request(frame={}, enter=True)
        return None

    def m_LiveFrame(self, sender, entries):
        if self.live_owner != sender:
            raise validate.Invalid("BeginLive first")
        frame = validate.key_colors(entries, ALL_IDS)   # transient: the wizard blinks unknown ids
        self.kb_worker.request(frame=frame)             # coalesced by the worker, never dropped
        return None

    def m_EndLive(self, sender):
        if self.live_owner == sender:
            self._end_live()
        return None

    def _end_live(self):
        dt = time.monotonic() - getattr(self, "_live_t0", time.monotonic())
        n = getattr(self, "_live_frames", 0)
        if dt > 0.5:
            log.info("live session: %d frames written to the keyboard in %.1f s (%.1f/s)", n, dt, n / dt)
        with self.lock:
            self.live_owner = None
        if self.live_watch:
            Gio.bus_unwatch_name(self.live_watch)
            self.live_watch = 0
        self.kb_worker.request(full=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s", stream=sys.stderr)
    bus = "session" if "--session" in sys.argv else "system"
    Daemon(bus).run()
