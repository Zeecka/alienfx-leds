#!/usr/bin/env python3
"""Generate the chassis templates in data/chassis/ (physical key geometry).

A template is the real keyboard of a machine: rows of keys with widths in key
units (1u = one key pitch), every row spanning the same width. Keys are named
with positional evdev names (KEY_ prefix dropped), so a template is valid for
any logical layout; legends are only what is printed on the keycaps.

The Alienware m15 R7 geometry was fitted to the top-down camera scan of
2026-09-27 (data/keymap-20260927-151053.json): key pitch ~94 px, every row
16u, widths snapped to 0.25u, function row half height (its centre sits
0.72u above the number row). See docs/protocol-measurements.md.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "chassis"

# Row geometry shared by the m15 R7 variants: (top, height) in u.
ROWS = [(0.0, 0.5), (0.5, 1.0), (1.5, 1.0), (2.5, 1.0), (3.5, 1.0), (4.5, 1.0)]

FN_ROW = [("ESC", "Esc"), *[(f"F{i}", f"F{i}") for i in range(1, 13)],
          ("HOME", "Home"), ("END", "End"), ("DELETE", "Del")]
BOTTOM = [("LEFTCTRL", "Ctrl", 1), ("FN", "Fn", 1), ("LEFTMETA", "Win", 1), ("LEFTALT", "Alt", 1),
          ("SPACE", "", 5.25), ("RIGHTALT", "AltGr", 1), ("WINLOCK", "Win🔒", 1),
          ("RIGHTCTRL", "Ctrl", 1.75), ("LEFT", "←", 1), ("DOWN", "↓", 1), ("RIGHT", "→", 1)]

# Legends per logical layout, for the positional names that differ.
LEGENDS = {
    "fr": {"GRAVE": "²", "1": "&1", "2": "é2", "3": "\"3", "4": "'4", "5": "(5", "6": "-6",
           "7": "è7", "8": "_8", "9": "ç9", "0": "à0", "MINUS": ")", "EQUAL": "=",
           "Q": "A", "W": "Z", "LEFTBRACE": "^", "RIGHTBRACE": "$", "A": "Q",
           "SEMICOLON": "M", "APOSTROPHE": "ù", "BACKSLASH": "*", "102ND": "<", "Z": "W",
           "M": ",", "COMMA": ";", "DOT": ":", "SLASH": "!"},
    "uk": {"GRAVE": "`", "MINUS": "-", "EQUAL": "=", "LEFTBRACE": "[", "RIGHTBRACE": "]",
           "SEMICOLON": ";", "APOSTROPHE": "'", "BACKSLASH": "#", "102ND": "\\",
           "COMMA": ",", "DOT": ".", "SLASH": "/"},
    "us": {"GRAVE": "`", "MINUS": "-", "EQUAL": "=", "LEFTBRACE": "[", "RIGHTBRACE": "]",
           "SEMICOLON": ";", "APOSTROPHE": "'", "BACKSLASH": "\\", "COMMA": ",", "DOT": ".",
           "SLASH": "/"},
}
SPECIAL = {"BACKSPACE": "⌫", "TAB": "Tab", "ENTER": "Enter", "CAPSLOCK": "Caps",
           "LEFTSHIFT": "Shift", "RIGHTSHIFT": "Shift", "UP": "↑", "MICMUTE": "Mic",
           "MUTE": "Mute", "VOLUMEUP": "Vol+", "VOLUMEDOWN": "Vol−", "SPACE": ""}


FIXED = {n: l for n, l in FN_ROW} | {n: l for n, l, _w in BOTTOM}


def legend(name, lang):
    if lang == "us" and name == "RIGHTALT":
        return "Alt"
    if name in FIXED:
        return FIXED[name]
    if name in SPECIAL:
        return SPECIAL[name]
    return LEGENDS[lang].get(name, name)


def rows_iso(lang):
    digits = ["GRAVE", *"1234567890", "MINUS", "EQUAL"]
    return [
        [(n, 1) for n, _ in FN_ROW],
        [*[(n, 1) for n in digits], ("BACKSPACE", 2), ("MICMUTE", 1)],
        [("TAB", 1.5), *[(n, 1) for n in "QWERTYUIOP"], ("LEFTBRACE", 1), ("RIGHTBRACE", 1),
         ("ENTER", 1.5), ("MUTE", 1)],
        [("CAPSLOCK", 1.75), *[(n, 1) for n in "ASDFGHJKL"], ("SEMICOLON", 1),
         ("APOSTROPHE", 1), ("BACKSLASH", 1), ("ENTER", 1.25), ("VOLUMEUP", 1)],
        [("LEFTSHIFT", 1.25), ("102ND", 1), *[(n, 1) for n in "ZXCVBNM"], ("COMMA", 1),
         ("DOT", 1), ("SLASH", 1), ("RIGHTSHIFT", 1.75), ("UP", 1), ("VOLUMEDOWN", 1)],
        [(n, w) for n, _, w in BOTTOM],
    ]


def rows_ansi():
    rows = rows_iso("us")
    rows[2] = [("TAB", 1.5), *[(n, 1) for n in "QWERTYUIOP"], ("LEFTBRACE", 1),
               ("RIGHTBRACE", 1), ("BACKSLASH", 1.5), ("MUTE", 1)]
    rows[3] = [("CAPSLOCK", 1.75), *[(n, 1) for n in "ASDFGHJKL"], ("SEMICOLON", 1),
               ("APOSTROPHE", 1), ("ENTER", 2.25), ("VOLUMEUP", 1)]
    rows[4] = [("LEFTSHIFT", 2.25), *[(n, 1) for n in "ZXCVBNM"], ("COMMA", 1), ("DOT", 1),
               ("SLASH", 1), ("RIGHTSHIFT", 1.75), ("UP", 1), ("VOLUMEDOWN", 1)]
    return rows


def build(tid, name, lang, rows, verified, note):
    keys, width = [], None
    for (top, h), row in zip(ROWS, rows):
        x = 0.0
        for kname, w in row:
            keys.append({"name": kname, "legend": legend(kname, lang),
                         "x": round(x, 3), "y": top, "w": w, "h": h})
            x += w
        width = width or x
        assert abs(x - width) < 1e-6, (tid, top, x, width)
    return {"id": tid, "name": name, "dmi_product": ["Alienware m15 R7"], "layout": lang,
            "verified": verified, "note": note, "width": width,
            "height": ROWS[-1][0] + ROWS[-1][1], "no_led": ["SPACE"] if verified else [],
            "no_keycode": ["FN", "WINLOCK"], "keys": keys}


TEMPLATES = [
    build("alienware-m15-r7-azerty", "Alienware m15 R7 — AZERTY (FR)", "fr", rows_iso("fr"), True,
          "Measured: geometry fitted to a top-down camera scan of this machine."),
    build("alienware-m15-r7-qwerty-uk", "Alienware m15 R7 — QWERTY (UK)", "uk", rows_iso("uk"), False,
          "Derived: same ISO chassis as the measured AZERTY unit, UK legends. Not verified."),
    build("alienware-m15-r7-qwerty-us", "Alienware m15 R7 — QWERTY (US)", "us", rows_ansi(), False,
          "Derived: ANSI arrangement on the measured chassis (no < key, wide Enter/Shift). "
          "Not verified."),
]

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for t in TEMPLATES:
        (OUT / f"{t['id']}.json").write_text(json.dumps(t, indent=1, ensure_ascii=False) + "\n")
        print(t["id"], len({k["name"] for k in t["keys"]}), "keys")
