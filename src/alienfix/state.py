"""Persistent lighting state (the daemon's source of truth)."""
import copy
import json
import os
from pathlib import Path

from . import validate

DEFAULT = {
    "enabled": True,
    "brightness": 80,
    "keyboard": {
        "mode": "static",
        "keys": {},                       # {id: [r, g, b]}; missing ids use "base"
        "base": [0, 90, 255],
        "effect": {"name": "wave", "tempo": 5, "c1": [0, 90, 255], "c2": [255, 0, 160]},
    },
    "zones": {},                          # {zone id: [r, g, b]}, zone ids come from the model
    # per zone: {"name": static|pulse|morph, "tempo", "c2"}; c1 is zones[zone]
    "zone_effects": {},
    # typing ripple (per-key keyboards): off by default, it reads key presses
    "ripple": {"enabled": False, "color": [255, 110, 0], "speed": 10, "under": "effect",
               "background": [0, 0, 25]},
}
ZONE_COLOR = [0, 90, 255]


def _zone_default():
    return {"name": "static", "tempo": 7, "c2": [0, 0, 0]}


class State:
    def __init__(self, path, key_ids, zone_ids):
        self.path = Path(path)
        self.key_ids = sorted(key_ids)
        self.data = copy.deepcopy(DEFAULT)
        self.set_zones(zone_ids)
        try:
            saved = json.loads(self.path.read_text())
            self._merge(saved)
        except (OSError, ValueError):
            pass

    def set_zones(self, zone_ids):
        """Follow the active model: keep known zones, add new ones, drop the rest
        from the live state (the saved file keeps them until the next save)."""
        d = self.data
        d["zones"] = {z: d["zones"].get(z, list(ZONE_COLOR)) for z in zone_ids}
        d["zone_effects"] = {z: d["zone_effects"].get(z, _zone_default()) for z in zone_ids}

    def _merge(self, saved):
        """Take only known fields with sane types from the file."""
        d = self.data
        if isinstance(saved.get("enabled"), bool):
            d["enabled"] = saved["enabled"]
        if isinstance(saved.get("brightness"), int) and 0 <= saved["brightness"] <= 100:
            d["brightness"] = saved["brightness"]
        kb = saved.get("keyboard", {})
        if kb.get("mode") in ("static", "effect"):
            d["keyboard"]["mode"] = kb["mode"]
        if _rgb(kb.get("base")):
            d["keyboard"]["base"] = list(kb["base"])
        for k, v in (kb.get("keys") or {}).items():
            if k.isdigit() and int(k) in self.key_ids and _rgb(v):
                d["keyboard"]["keys"][k] = list(v)
        eff = kb.get("effect") or {}
        if eff.get("name") in validate.EFFECTS and _tempo(eff.get("tempo")) \
                and _rgb(eff.get("c1")) and _rgb(eff.get("c2")):
            d["keyboard"]["effect"] = {k: eff[k] for k in ("name", "tempo", "c1", "c2")}
        for z, v in (saved.get("zones") or {}).items():
            if z in d["zones"] and _rgb(v):
                d["zones"][z] = list(v)
        for z, e in (saved.get("zone_effects") or {}).items():
            if z in d["zone_effects"] and isinstance(e, dict) and e.get("name") in validate.ZONE_EFFECTS \
                    and _tempo(e.get("tempo")) and _rgb(e.get("c2")):
                d["zone_effects"][z] = {"name": e["name"], "tempo": e["tempo"], "c2": list(e["c2"])}
        rp = saved.get("ripple") or {}
        try:
            d["ripple"] = validate.ripple(rp["enabled"], rp["color"], rp["speed"], rp["under"],
                                          rp["background"])
        except (KeyError, TypeError, validate.Invalid):
            pass

    def key_colors(self):
        kb = self.data["keyboard"]
        return {i: tuple(kb["keys"].get(str(i), kb["base"])) for i in self.key_ids}

    def save(self):
        tmp = self.path.with_suffix(".tmp")       # atomic: a crash never leaves half a file
        tmp.write_text(json.dumps(self.data, indent=1))
        os.replace(tmp, self.path)

    def public(self, live, presence, per_key=True):
        d = copy.deepcopy(self.data)
        d["live"] = live
        d["devices"] = presence
        d["ripple"]["available"] = per_key          # the ripple needs a per-key keyboard
        return json.dumps(d)


def _tempo(v):
    return isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 30


def _rgb(v):
    return isinstance(v, list) and len(v) == 3 and all(isinstance(c, int) and 0 <= c <= 255 for c in v)
