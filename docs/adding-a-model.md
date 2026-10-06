# Adding or fixing a model

A model is one JSON file in `data/models/`. It tells the daemon which lights
the machine has and how to reach them.

## 1. What does your machine have?

```
alienfix models        # detected product name, controllers found, active model
alienfix zones         # zones of the active model
```

- **Controllers found.**
  - `elc`: an AW-ELC (USB `187c:0550`/`0551`, most machines since about 2019).
  - `legacy`: an older AlienFX controller (USB `187c:0511`..`0530`).
  - `wmi`: the kernel's `alienware-wmi` driver exposes `rgb_zones` (pre-2018
    desktops).
  - `keyboard`: a Darfon per-key keyboard.
- **If nothing matched**, the daemon uses a generic model: `elc0..elc3`,
  legacy bits 0..7, or the WMI zones. Try each zone from the Chassis page or
  with `alienfix zone elc2 '#ff0000'` and note what lights up.
- **If a similar model exists**, try it: `alienfix model <id>` (or Machine →
  *Use the lighting map of*). `alienfix model auto` goes back.

## 2. Write the file

```json
{
 "id": "alienware-m16-r2",
 "name": "Alienware m16 R2",
 "dmi_product": ["Alienware m16 R2"],
 "support": "reported",
 "source": "Tested by <you> on BIOS x.y.z: what you checked and how.",
 "keyboard": {"type": "per-key"},
 "zones": [
  {"id": "power", "label": "Power button", "controller": "elc", "index": 4, "role": "power", "group": "chassis", "verified": true},
  {"id": "logo", "label": "Lid logo", "controller": "elc", "index": 2, "group": "chassis", "verified": true}
 ]
}
```

- `dmi_product`: exactly `cat /sys/class/dmi/id/product_name`. Legacy
  controllers can match with `"usb": ["187c:0521"]` instead.
- `keyboard.type`:
  - `per-key` for a Darfon keyboard. Create its key map in the app with the
    blink wizard, then export it and add it to `data/profiles/`, with its
    template in `data/chassis/` if the geometry differs.
  - `zones` if the keyboard backlight is made of zones: give those zones
    `"group": "keyboard"`.
  - `none`.
- Zones:
  - `elc` and `wmi` zones take an `index`; `legacy` zones take a 24-bit `mask`.
  - `role: "power"` programs the light through the power-state sequence (each
    power state), which is the only path that moved the m15 R7 power button.
    Use it only for a real power button.
  - `verified: true` only for what you saw with your own eyes. Add a `note` for
    anything partial.
- `support`: `verified` is reserved for machines checked with a camera or
  equivalent. Anything else is `reported`.

`python3 -m unittest discover -s tests` checks every model file.
`tools/list_models.py` regenerates `docs/supported-models.md`.

## 3. Regenerating the imported models

The community models come from other projects, imported by:

```
tools/import_models.py <alienfx-tools checkout> <akbl checkout>
```

Only facts are taken: USB ids, light indices, masks and names. The sources and
licenses are listed in that script's header. Hand-verified models (like the
m15 R7) are never overwritten.
