# alienfixd D-Bus API

- Bus: **system**. Name `io.github.zeecka.AlienFix`, object
  `/io/github/zeecka/AlienFix`, interface `io.github.zeecka.AlienFix1`.
- Introspection: `data/io.github.zeecka.AlienFix1.xml`.
- For tests, clients can target the **session** bus with
  `ALIENFIX_BUS=session`; `tests/mock_daemon.py` runs the real daemon there
  with fake hardware (`MOCK_MODEL=<model id>` simulates any model).

## Authorisation

- `GetState`, `GetLayout`, `ListChassis`: open to everyone (nothing
  sensitive).
- Every other method: polkit action `io.github.zeecka.alienfix.control`
  (`allow_active=yes`, `allow_inactive=no`, `allow_any=no`). The active local
  session is allowed. Remote or inactive sessions are refused with
  `io.github.zeecka.AlienFix1.Error.NotAuthorized`.
- Invalid argument: `io.github.zeecka.AlienFix1.Error.InvalidArgs`. Nothing
  reaches the hardware. A call that the model does not support (per-key
  colours on a zone keyboard, an effect a zone cannot run) is an invalid
  argument too.

## `GetLayout()` → JSON

```json
{
  "model": {"id": "alienware-m15-r7", "name": "Alienware m15 R7", "support": "verified",
            "source": "...", "keyboard": "per-key"},
  "model_forced": false,
  "zones": [{"id": "power", "label": "Power button", "group": "chassis", "verified": true,
             "controller": "elc", "role": "power", "note": "...", "effects": ["static", "pulse", "morph"]}, ...],
  "effects": ["static", "breathing", "wave", ...],
  "profile": {"name": "Alienware m15 R7 (camera-measured)", "chassis": "alienware-m15-r7-azerty", "method": "camera"},
  "custom": false,
  "chassis": {"id": "...", "width": 16, "height": 5.5, "keys": [{"name": "ESC", "legend": "Esc", "x": 0, "y": 0, "w": 1, "h": 0.5}, ...]},
  "keys": {"0": {"name": "ESC", "legend": "Esc", "row": 0, "u": 0.5, "v": 0.25}},
  "ghosts": {"SPACE": {"legend": "", "u": 6.625, "v": 5.0}}
}
```

- `model.keyboard` is one of these values:
  - `"per-key"`: `keys` holds the key map. It is empty until one is created
    on a machine without a shipped map.
  - `"zones"`: the keyboard backlight is the zones whose `group` is
    `"keyboard"`.
  - `"none"`.
- `model.support` is one of these values:
  - `"verified"`: checked on the machine with a camera.
  - `"reported"`: taken from other projects' community mappings.
  - `"untested"`: a generic model built from the controllers found.
- `effects`: the names `SetKeyboardEffect` accepts on this model.
  - On a zone keyboard, `breathing` runs as the controller's pulse, and
    `wave`/`mixpulse` run as its morph.
- `chassis.keys`: the real key geometry in key units. A key can have two
  rectangles (the ISO Enter L shape).

## `GetState()` → JSON

```json
{
  "enabled": true, "brightness": 80,
  "keyboard": {"mode": "static", "base": [0, 90, 255], "keys": {"0": [255, 0, 0]},
               "effect": {"name": "wave", "tempo": 7, "c1": [255, 0, 0], "c2": [0, 0, 255]}},
  "zones": {"power": [255, 255, 255], "lid": [0, 128, 255]},
  "zone_effects": {"lid": {"name": "pulse", "tempo": 6, "c2": [0, 0, 0]}},
  "ripple": {"enabled": false, "color": [255, 110, 0], "speed": 10, "under": "effect",
             "background": [0, 0, 25], "available": true},
  "live": false,
  "devices": {"keyboard": true, "elc": true, "legacy": false, "wmi": false, "usb": ["0d62:dabc", "187c:0550"]}
}
```

## Methods

| Method | Does |
|---|---|
| `SetKeyboardColor(yyy)` | Whole keyboard in one colour. Per-key: static mode. Zone keyboard: every keyboard zone. |
| `SetKeys(a(yyyy))` | Per-key colours (`id, r, g, b`); ids must be in the key map. Per-key keyboards only. |
| `SetKeyboardEffect(s name, y tempo, (yyy) c1, (yyy) c2)` | See `effects` above. Per-key keyboards get 6 firmware effects plus 5 effects rendered by the daemon (rainbow, spectrum, morph, starlight, gradient). Tempo 1..30 is a period: higher is slower. |
| `SetBrightness(y)` | 0..100. Keyboard: hardware dimmer. Zones: colour scaling. |
| `SetZoneColor(s zone, y r, y g, y b)` | Static colour for one zone. |
| `SetZoneEffect(s zone, s name, y tempo, (yyy) c1, (yyy) c2)` | `name` must be in that zone's `effects`: AW-ELC zones take static, pulse and morph (c1 → c2); legacy and WMI zones take static only. |
| `SetEnabled(b)` | All lights off, or back to the saved state. |
| `SetRipple(b enabled, (yyy) color, y speed, s under, (yyy) background)` | Typing ripple, saved with the state. Per-key keyboards only (`ripple.available`). See below. |
| `SelectModel(s id)` | Use one of the shipped models (`ListChassis` → `models`); `""` goes back to automatic detection. Emits `LayoutChanged`. |
| `ListChassis()` → JSON | Keyboard templates, the model catalogue, the active model, the DMI product name and the detected controllers. |
| `SaveProfile(s)` / `ResetProfile()` | Store or drop a per-key key map, see below. |
| `BeginLive()`, `LiveFrame(a(yyyy))`, `EndLive()` | Live mode, see below. |

## Key maps (per-key keyboards)

`SaveProfile` takes `{"name", "chassis", "method": "blink"|"camera",
"keys": {"ESC": [0], "BACKSPACE": [34, 35]}}` and validates it strictly:

- the template must be known, and every key name must belong to it;
- each key has 1 to 4 ids, each id is 0..254 and is used once;
- the name is 1 to 60 plain characters;
- the whole document is at most 32 KiB.

The map is stored in `/var/lib/alienfix/profile.json` and becomes active.
`ResetProfile` goes back to the shipped map of the active model, if there is
one.

## Typing ripple

When `enabled`, each key pressed on the built-in keyboard sends a ring of
`color` across it at `speed` keys per second (2..40). The ring fades out
after about 9 keys. `under` is `"effect"` (rings over the current colours or
effect) or `"color"` (over `background`).

- The daemon reads key presses (evdev) **only while the ripple is on** and
  the lighting is enabled. It reads only the built-in keyboard: the USB
  device that is also the lighting keyboard, and the i8042 keyboard.
  External keyboards are never opened.
- A key code only becomes a position on the keyboard. It is not stored,
  logged or sent, and there is no grab.
- While the ripple is on, the daemon draws every frame. Firmware effects are
  then replaced by software look-alikes, close to them but not identical
  (only the wave period is measured).

## Live mode

`BeginLive()`, then repeated `LiveFrame(a(yyyy))`, then `EndLive()`.

- Frames are never saved. They accept every id 0..254, because the blink
  wizard lights ids that are not mapped yet.
- If the client leaves the bus, the daemon ends live mode and restores the
  state.
- While the keyboard is busy, pending frames are **merged**, so the latest
  colour of every id is always written.
- A client should wait for the reply to a `LiveFrame` before sending the next
  one, and drop the frames in between.

## Signals

- `StateChanged(s state_json)`: after every persistent change, never for a
  live frame.
- `LayoutChanged`: the model or the key map changed; read `GetLayout` again.
