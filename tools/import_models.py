#!/usr/bin/env python3
"""Generate data/models/*.json from other open-source projects' device data.

    tools/import_models.py <alienfx-tools checkout> <akbl checkout>

Sources (facts only: USB ids, light indices, masks and names; no code):
- T-Troll/alienfx-tools, alienfx-gui/Mappings/devices.csv (MIT): AW-ELC
  (187c:0550/0551) light indices, names and flags per model (flag 1 = power
  button, 2 = status indicator), and a few legacy models.
- rsm-gh/akbl, usr/share/AKBL/computers/*.ini (GPL-3.0): legacy AlienFX
  (187c:0511..0530) zone masks per model. akbl also lists 0550/0551 models
  with the 9-byte legacy protocol; alienfx-tools, OpenRGB and our own
  measurements use the 34-byte protocol there, so those entries are skipped.
- OpenRGB AlienwareController.cpp platform table (GPL-2.0) and tr1xem/AWCC
  database.json (GPL-3.0): Dell G-series 4-zone keyboards (zone ids, DMI
  names). Written by hand below (CURATED), with the source of each line.

DMI product names are only filled in where a source keyed on DMI (AWCC's
database, the kernel) names the model; otherwise the model is picked by
USB id (legacy) or chosen by the user in the app.
The hand-measured Alienware m15 R7 model is not generated here.
"""
import configparser
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "models"
KEEP = {"alienware-m15-r7.json"}

# devices.csv model name -> DMI product names (from AWCC database.json keys).
DMI = {
    "Alienware m15R1": ["Alienware m15"],
    "Alienware m15R3/R4": ["Alienware m15 R3", "Alienware m15 R4"],
    "Alienware Aurora R12": ["Alienware Aurora R12"],
    "Alienware m15R6 4-zone": ["Alienware m15 R6"],
    "Alienware Area-51m R2/m17R3/R4": ["Alienware Area-51m R2"],
    "Alienware x17 R1": ["Alienware x17 R1"],
    "Alienware m17R5": ["Alienware m17 R5 AMD"],
    "Alienware m16R1": ["Alienware m16 R1", "Alienware m16 R1 AMD"],
    "Alienware m18R1": ["Alienware m18 R1", "Alienware m18 R1 AMD"],
    "Alienware m16R2 (US)": ["Alienware m16 R2"],
    "Area 51m R1": ["Alienware Area-51m"],
    "Alienware m18R2": ["Alienware m18 R2"],
}
SKIP = {"Alienware m16R1 (Brazil)"}           # duplicate of "Alienware m16R1"
LEGACY_PIDS = {0x0511, 0x0512, 0x0513, 0x0514, 0x0515, 0x0518, 0x0520, 0x0521, 0x0522, 0x0524,
               0x0525, 0x0526, 0x0527, 0x0528, 0x0529, 0x0530}
ELC_PIDS = {0x0550, 0x0551}
KEYBOARD_WORDS = re.compile(r"\b(kb|keyboard)\b", re.I)


def kb_zones(ids, labels=("Keyboard left", "Keyboard center-left", "Keyboard center-right", "Keyboard right")):
    return [{"id": f"kb{n}", "label": labels[n], "controller": "elc", "index": i, "group": "keyboard",
             "verified": False} for n, i in enumerate(ids)]


CURATED = [
    {"id": "dell-g15-5511", "name": "Dell G15 5511", "dmi_product": ["Dell G15 5511"],
     "source": "OpenRGB AlienwareController.cpp platform 0x0E03 (zones 00-03); AWCC database.json (default zones 0-3).",
     "zones": kb_zones([0, 1, 2, 3])},
    {"id": "dell-g15-5520", "name": "Dell G15 5520", "dmi_product": ["Dell G15 5520"],
     "source": "OpenRGB platform 0x0E07 and AWCC database.json: zones 0x10-0x13.",
     "zones": kb_zones([0x10, 0x11, 0x12, 0x13])},
    {"id": "dell-g15-5530", "name": "Dell G15 5530", "dmi_product": ["Dell G15 5530"],
     "source": "OpenRGB platform 0x0E0A: zones 0x10-0x13.",
     "zones": kb_zones([0x10, 0x11, 0x12, 0x13])},
    {"id": "dell-g5-5505", "name": "Dell G5 15 SE 5505", "dmi_product": ["G5 5505"],
     "source": "OpenRGB platform 0x0C01 (Left, Middle, Right, Numpad = 00-03); DMI name from AWCC database.json.",
     "zones": kb_zones([0, 1, 2, 3], ("Keyboard left", "Keyboard middle", "Keyboard right", "Numpad"))},
    *[{"id": f"dell-g15-{n}", "name": f"Dell G15 {n}", "dmi_product": [f"Dell G15 {n}"],
       "source": "AWCC database.json: no zone override, AWCC's default zones 0-3.",
       "zones": kb_zones([0, 1, 2, 3])} for n in ("5510", "5515", "5525", "5535")],
]


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-")


def rev(repo):
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%h"],
                          capture_output=True, text=True).stdout.strip() or "?"


def from_csv(path, commit):
    rows = list(csv.reader(path.open(encoding="utf-8", errors="replace"), quotechar="'"))
    models, cur, dev = [], None, None
    for r in rows:
        if not r:
            continue
        if r[0] == "3":
            cur = {"name": r[1], "devs": []}
            models.append(cur)
        elif r[0] == "0" and cur is not None:
            dev = {"vid": int(r[1]), "pid": int(r[2]), "name": r[3], "lights": []}
            cur["devs"].append(dev)
        elif r[0] == "1" and dev is not None:
            dev["lights"].append((int(r[1]), int(r[2]), r[3]))
    out, seen = [], set()
    for m in models:
        if m["name"] in SKIP:
            continue
        fx = [d for d in m["devs"] if d["vid"] == 0x187C and d["pid"] in ELC_PIDS | LEGACY_PIDS]
        if not fx or not fx[0]["lights"] or len(fx[0]["lights"]) > 32:
            continue
        d = fx[0]
        legacy = d["pid"] in LEGACY_PIDS
        per_key = any(x["vid"] == 0x0D62 for x in m["devs"])
        zones = []
        for lid, flags, name in sorted(d["lights"]):
            if flags == 2:                  # caps/wifi/HDD indicators: not lighting
                continue
            if legacy and lid > 23:         # the legacy mask is 24 bits wide
                continue
            z = {"id": f"l{lid}", "label": name.strip().strip("<>").strip() or f"Light {lid}", "controller": "legacy" if legacy else "elc",
                 "group": "keyboard" if KEYBOARD_WORDS.search(name) and not per_key else "chassis",
                 "verified": False}
            if legacy:
                z["mask"] = 1 << lid        # SDK SetMaskAndColor: mask = 1 << index
            else:
                z["index"] = lid
                if flags == 1:
                    z["role"] = "power"
            zones.append(z)
        mid = slug(m["name"])
        if mid in seen:                     # a second, conflicting entry (m18R1): keep the first
            continue
        seen.add(mid)
        kb = "per-key" if per_key else ("zones" if any(z["group"] == "keyboard" for z in zones) else "none")
        model = {"id": mid, "name": m["name"], "dmi_product": DMI.get(m["name"], []),
                 "support": "reported",
                 "source": f"alienfx-tools devices.csv @ {commit} (MIT), community mapping for {d['vid']:04x}:{d['pid']:04x}.",
                 "keyboard": {"type": kb}, "zones": zones}
        if legacy:
            model["usb"] = [f"187c:{d['pid']:04x}"]
        out.append(model)
    return out


def from_akbl(directory, commit):
    out = []
    for ini in sorted(directory.glob("*.ini")):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        cp.read(ini, encoding="utf-8")
        common = cp["COMMON"]
        pid = int(common.get("PRODUCT_ID", "0"))
        if int(common.get("VENDOR_ID", "0")) != 0x187C or pid not in LEGACY_PIDS:
            continue
        zones = []
        for sec in cp.sections():
            if not sec.startswith("REGION "):
                continue
            r = cp[sec]
            mask = int(r.get("BLOCK", "0"))
            if not 0 < mask <= 0xFFFFFF or r.get("CAN_LIGHT", "True") != "True":
                continue
            desc = r.get("DESCRIPTION", sec[7:]).strip()
            zones.append({"id": slug(r.get("ID", sec[7:]))[:32] or f"m{mask}", "label": desc,
                          "controller": "legacy", "mask": mask,
                          "group": "keyboard" if KEYBOARD_WORDS.search(desc) else "chassis", "verified": False})
        ids = set()
        zones = [z for z in zones if not (z["id"] in ids or ids.add(z["id"]))]
        if not zones:
            continue
        name = common.get("NAME", ini.stem).strip()
        out.append({"id": "akbl-" + slug(name)[:27], "name": f"Alienware {name}" if not name.lower().startswith("alienware") else name,
                    "dmi_product": [], "usb": [f"187c:{pid:04x}"], "support": "reported",
                    "source": f"akbl {ini.name} @ {commit} (GPL-3.0 data file; masks and names only).",
                    "keyboard": {"type": "zones" if any(z["group"] == "keyboard" for z in zones) else "none"},
                    "zones": zones})
    return out


def main(afx_tools, akbl):
    afx_tools, akbl = Path(afx_tools), Path(akbl)
    models = from_csv(afx_tools / "alienfx-gui/Mappings/devices.csv", rev(afx_tools))
    models += from_akbl(akbl / "usr/share/AKBL/computers", rev(akbl))
    for c in CURATED:
        models.append({**c, "support": "reported", "keyboard": {"type": "zones"}})
    for p in OUT.glob("*.json"):              # replace only what this script generated
        try:
            old = json.loads(p.read_text())
        except ValueError:
            continue
        if p.name not in KEEP and old.get("generated_by") == "tools/import_models.py":
            p.unlink()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from alienfix import models as M
    for m in models:
        m["generated_by"] = "tools/import_models.py"
        M.check(m)
        (OUT / f"{m['id']}.json").write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n")
        print(f"{m['id']:<34} {m['keyboard']['type']:<8} {len(m['zones']):>2} zones  {', '.join(m['dmi_product']) or ', '.join(m.get('usb', []))}")
    print(len(models), "models")


if __name__ == "__main__":
    main(*sys.argv[1:3])
