"""Chassis templates and per-machine profiles.

- A chassis template (data/chassis/*.json) is the physical keyboard: key
  names, legends and geometry in key units. It is shipped, read-only.
- A profile maps template key names to LED ids for one machine:
      {"version": 1, "name": "...", "chassis": "<template id>",
       "method": "camera" | "blink", "keys": {"ESC": [0], "BACKSPACE": [34, 35]}}
  The m15 R7 profile ships (measured by camera); users create others with
  the "does it blink?" wizard, and the daemon stores the active one.

Everything a client sends is validated here before it is stored: the
template must exist, key names must belong to it, ids are integers 0..254
used once, the name is short printable text that never reaches hardware.
"""
import json
import re
from pathlib import Path

from .validate import Invalid       # one error type for the daemon

MAX_ID = 254                   # ids go on the wire as id+1 in one byte
MAX_PROFILE_BYTES = 32768
NAME_RE = re.compile(r"^[\w .,()'+\-—]{1,60}$", re.UNICODE)


def load_templates(directory):
    out = {}
    for p in sorted(Path(directory).glob("*.json")):
        t = json.loads(p.read_text())
        out[t["id"]] = t
    return out


def validate(profile, templates):
    """Rebuild a clean profile from untrusted input, or raise Invalid."""
    if not isinstance(profile, dict):
        raise Invalid("profile: object expected")
    chassis = profile.get("chassis")
    if chassis not in templates:
        raise Invalid("profile: unknown chassis template")
    name = profile.get("name", "")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise Invalid("profile: name must be 1-60 letters, digits, spaces or .,()'+-")
    method = profile.get("method", "blink")
    if method not in ("camera", "blink"):
        raise Invalid("profile: unknown method")
    keys = profile.get("keys")
    if not isinstance(keys, dict) or not keys:
        raise Invalid("profile: keys must be a non-empty object")
    known = {k["name"]: k["name"] for k in templates[chassis]["keys"]}   # our strings, not the client's
    seen, clean = set(), {}
    for kname, ids in keys.items():
        if kname not in known:
            raise Invalid(f"profile: key {kname!r} is not on this chassis")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 4:
            raise Invalid(f"profile: {kname}: 1 to 4 ids expected")
        for i in ids:
            if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i <= MAX_ID:
                raise Invalid(f"profile: {kname}: id out of range 0..{MAX_ID}")
            if i in seen:
                raise Invalid(f"profile: id {i} assigned to two keys")
            seen.add(i)
        clean[known[kname]] = sorted(int(i) for i in ids)
    return {"version": 1, "name": name, "chassis": chassis, "method": method, "keys": clean}


def parse(text, templates):
    if not isinstance(text, str) or len(text.encode()) > MAX_PROFILE_BYTES:
        raise Invalid("profile: too large")
    try:
        data = json.loads(text)
    except ValueError:
        raise Invalid("profile: not JSON") from None
    return validate(data, templates)


def key_geometry(template):
    """{name: {"legend", "rects": [(x, y, w, h)], "u", "v"}}; u/v = centre of the main rect."""
    geo = {}
    for k in template["keys"]:
        g = geo.setdefault(k["name"], {"legend": k["legend"], "rects": []})
        g["rects"].append((k["x"], k["y"], k["w"], k["h"]))
    for g in geo.values():
        x, y, w, h = g["rects"][0]
        g["u"], g["v"] = round(x + w / 2, 3), round(y + h / 2, 3)
    return geo


def layout(profile, template):
    """What GetLayout serves: ids with template positions, plus the template itself."""
    geo = key_geometry(template)
    keys = {}
    for kname, ids in profile["keys"].items():
        g = geo[kname]
        row = int(g["rects"][0][1] + 0.01)
        for i in ids:
            keys[str(i)] = {"name": kname, "legend": g["legend"], "row": row, "u": g["u"], "v": g["v"]}
    ghosts = {n: {"legend": g["legend"], "u": g["u"], "v": g["v"]}
              for n, g in geo.items() if n not in profile["keys"]}
    return {"profile": {k: profile[k] for k in ("name", "chassis", "method")},
            "chassis": template, "keys": keys, "ghosts": ghosts,
            "no_keycode": template.get("no_keycode", [])}
