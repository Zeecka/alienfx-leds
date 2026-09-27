#!/usr/bin/env python3
"""data/keymap.json (camera-measured ids) -> data/profiles/alienware-m15-r7-azerty.json."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from alienfix import profile  # noqa: E402

km = json.loads((ROOT / "data/keymap.json").read_text())["keys"]
keys = {}
for kid, m in sorted(km.items(), key=lambda kv: int(kv[0])):
    keys.setdefault(m["name"], []).append(int(kid))
templates = profile.load_templates(ROOT / "data/chassis")
p = profile.validate({"name": "Alienware m15 R7 (camera-measured)", "chassis": "alienware-m15-r7-azerty",
                      "method": "camera", "keys": keys}, templates)
out = ROOT / "data/profiles/alienware-m15-r7-azerty.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(p, indent=1) + "\n")
print(out.name, len(p["keys"]), "keys,", sum(map(len, p["keys"].values())), "ids")
