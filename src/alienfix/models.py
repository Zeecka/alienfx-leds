"""Machine models: which lights a laptop or desktop has, and how to reach them.

A model (data/models/*.json) describes one machine family:

    {"id": "alienware-m15-r7", "name": "Alienware m15 R7",
     "dmi_product": ["Alienware m15 R7"], "usb": ["187c:0521"] (legacy models),
     "support": "verified" | "reported" | "untested",
     "source": "where the facts come from",
     "keyboard": {"type": "per-key", "chassis": ["alienware-m15-r7-azerty", ...]}
               | {"type": "zones"} | {"type": "none"},
     "zones": [{"id": "power", "label": "Power button", "controller": "elc",
                "index": 2, "role": "power", "group": "chassis", "verified": true}, ...]}

- "per-key": the keyboard is a Darfon-style per-key controller; its key map
  comes from a profile (profile.py).
- "zones": the keyboard backlight is made of controller zones (group
  "keyboard"); "Keyboard" color and effects apply to those zones.
- controller: "elc" (AW-ELC, USB HID API v4), "legacy" (older AlienFX USB
  HID, API v2/v3), "wmi" (the kernel's alienware-wmi rgb_zones sysfs).
  elc and wmi zones have an "index"; legacy zones a 24-bit "mask".

The model is chosen by the user (SelectModel), else from the DMI product
name, else from the USB id of a legacy controller. When nothing matches, a generic model is built from the
controllers actually present, with neutral zone names.
"""

import json
import re
from pathlib import Path

from .protocol import ZONE_EFFECTS
from .validate import Invalid

CONTROLLERS = ("elc", "legacy", "wmi")
SUPPORT = ("verified", "reported", "untested")
GROUPS = ("keyboard", "chassis")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_ZONES = 32
MAX_INDEX = {"elc": 31, "wmi": 7}
USB_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")


def check(m):
    """Validate a shipped model file (developer error if this raises)."""
    if not ID_RE.match(m.get("id", "")):
        raise Invalid(f"model id {m.get('id')!r}")
    if m.get("support") not in SUPPORT:
        raise Invalid(f"{m['id']}: support")
    kb = m.get("keyboard", {"type": "none"})
    if kb.get("type") not in ("per-key", "zones", "none"):
        raise Invalid(f"{m['id']}: keyboard type")
    zones = m.get("zones", [])
    if len(zones) > MAX_ZONES:
        raise Invalid(f"{m['id']}: too many zones")
    seen = set()
    for z in zones:
        if not ID_RE.match(z.get("id", "")) or z["id"] in seen:
            raise Invalid(f"{m['id']}: zone id {z.get('id')!r}")
        seen.add(z["id"])
        if z.get("controller") not in CONTROLLERS:
            raise Invalid(f"{m['id']}/{z['id']}: controller")
        if z["controller"] == "legacy":
            mask = z.get("mask")
            if isinstance(mask, bool) or not isinstance(mask, int) or not 0 < mask <= 0xFFFFFF:
                raise Invalid(f"{m['id']}/{z['id']}: mask")
        else:
            idx = z.get("index")
            if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx <= MAX_INDEX[z["controller"]]:
                raise Invalid(f"{m['id']}/{z['id']}: index")
        if z.get("group", "chassis") not in GROUPS:
            raise Invalid(f"{m['id']}/{z['id']}: group")
        if (
            not isinstance(z.get("label"), str)
            or not 0 < len(z["label"]) <= 60
            or not isinstance(z.get("note", ""), str)
            or len(z.get("note", "")) > 200
        ):
            raise Invalid(f"{m['id']}/{z['id']}: label or note")
        if z.get("role") not in (None, "power"):
            raise Invalid(f"{m['id']}/{z['id']}: role")
        if z.get("role") == "power" and z["controller"] != "elc":
            raise Invalid(f"{m['id']}/{z['id']}: power role needs the elc controller")
    if not all(isinstance(u, str) and USB_RE.match(u) for u in m.get("usb", [])):
        raise Invalid(f"{m['id']}: usb")
    if kb["type"] == "zones" and not any(z.get("group") == "keyboard" for z in zones):
        raise Invalid(f"{m['id']}: zone keyboard without keyboard zones")
    return m


def load(directory):
    out = {}
    for p in sorted(Path(directory).glob("*.json")):
        m = check(json.loads(p.read_text(encoding="utf-8")))
        out[m["id"]] = m
    return out


def zone_effects(zone):
    """Effects a zone supports: the ELC runs pulse/morph itself; the others
    only hold a color (software animation of them is not attempted)."""
    return ZONE_EFFECTS if zone["controller"] == "elc" else ("static",)


def generic(present, wmi_zones=0):
    """A model for an unknown machine, from the controllers that are present."""
    zones = []
    if present.get("elc"):
        zones += [
            {
                "id": f"elc{i}",
                "label": f"Light {i} (AW-ELC)",
                "controller": "elc",
                "index": i,
                "group": "chassis",
                "verified": False,
            }
            for i in range(4)
        ]
    if present.get("legacy"):
        zones += [
            {
                "id": f"fx{i}",
                "label": f"Light bit {i} (AlienFX)",
                "controller": "legacy",
                "mask": 1 << i,
                "group": "chassis",
                "verified": False,
            }
            for i in range(8)
        ]
    zones += [
        {
            "id": f"wmi{i}",
            "label": f"Zone {i} (alienware-wmi)",
            "controller": "wmi",
            "index": i,
            "group": "chassis",
            "verified": False,
        }
        for i in range(wmi_zones)
    ]
    return {
        "id": "generic",
        "name": "Unknown model (generic)",
        "dmi_product": [],
        "support": "untested",
        "source": "Built from the controllers found on this machine; zone names are neutral.",
        "keyboard": {"type": "per-key" if present.get("keyboard") else "none"},
        "zones": zones,
    }


def pick(models, dmi, forced=None, usb=()):
    """forced (a model id chosen by the user) wins; then the DMI name; then
    the USB id of a legacy controller (unique per model family); else None."""
    if forced and forced in models:
        return models[forced]
    for m in models.values():
        if dmi and dmi in m.get("dmi_product", []):
            return m
    for m in models.values():
        if set(m.get("usb", [])) & set(usb):
            return m
    return None


def public(m):
    """What GetLayout serves about the model (zones carry their effect list)."""
    return {
        "id": m["id"],
        "name": m["name"],
        "support": m["support"],
        "source": m.get("source", ""),
        "keyboard": m.get("keyboard", {"type": "none"})["type"],
    }


def public_zones(m):
    return [
        {
            "id": z["id"],
            "label": z["label"],
            "group": z.get("group", "chassis"),
            "verified": bool(z.get("verified")),
            "controller": z["controller"],
            "role": z.get("role"),
            "note": z.get("note", ""),
            "effects": list(zone_effects(z)),
        }
        for z in m["zones"]
    ]
