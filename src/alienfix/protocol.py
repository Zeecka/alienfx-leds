"""Report encoders for the USB HID lighting controllers.

Pure functions: validated values in, report bytes out. No I/O here.
The Darfon keyboard and AW-ELC sequences were confirmed with a camera on an
Alienware m15 R7 (docs/m15-r7.md); comments say what was measured.

Keyboard (Darfon 0d62:dabc): FEATURE report 0xCC, 64 bytes on the wire.
Chassis (AW-ELC 187c:0550): OUTPUT report without id, 34 bytes on the wire
(leading 0x00 + 33).
"""

KB_REPORT_ID = 0xCC
KB_SIZE = 64
ELC_SIZE = 34
KEYS_PER_PACKET = 15
# Measured 13:58: right after a mode change (effect -> per-key), ColorSet
# packets are silently dropped for 20-50 ms. No error, no status change.
MODE_SETTLE = 0.1

# Hardware effect types (0x80 command). Measured: 0x4 is not a dual-color
# wave (frozen band), 0x5-0x7 show nothing, 0xB shows nothing without typing.
EFFECTS = {
    "breathing": 0x02,
    "wave": 0x03,  # two-color when n = 2
    "pulse": 0x08,
    "mixpulse": 0x09,  # alternates c1 / c2
    "nightrider": 0x0A,
}
TEMPO_MIN, TEMPO_MAX = 1, 30  # period: wave loops in ~0.6 s x tempo

# Power button stores one color per power state (0x5b..0x60); the SDK
# programs all six and so do we, so it keeps its color when asleep/off.
POWER_STATES = (0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60)
POWER_INDEX = 2  # m15 R7, measured: only index 2 moves the button
# Which ELC index lights what is per model (data/models/). On the m15 R7,
# measured with a camera: 3 = lid logo, 0 and 1 = the two rear-strip channels.


def _kb(*payload):
    buf = bytes([KB_REPORT_ID, *payload])
    assert len(buf) <= KB_SIZE
    return buf.ljust(KB_SIZE, b"\0")


def _elc(*payload):
    buf = bytes([0x00, *payload])
    assert len(buf) <= ELC_SIZE
    return buf.ljust(ELC_SIZE, b"\0")


# ---- keyboard -------------------------------------------------------------


def kb_custom_mode():
    """Per-key mode. Measured: after any hardware effect, per-key writes stay
    invisible until this is sent (the SDK sends it when effType == 0)."""
    return _kb(0x80, 0x01, 0xFE, 0x00, 0x00, 0x01, 0x01, 0x01)


def kb_colors(pairs):
    """ColorSet packets, each followed by a Loop. pairs: [(id, (r, g, b))].
    Measured: ColorSet alone already applies on this firmware; the Loop is
    kept because it costs 1.5 ms and is what the SDK documents."""
    out = []
    for i in range(0, len(pairs), KEYS_PER_PACKET):
        body = []
        for kid, (r, g, b) in pairs[i : i + KEYS_PER_PACKET]:
            body += [kid + 1, r, g, b]  # ids are sent 1-based
        out.append(_kb(0x8C, 0x02, 0x00, *body))
        out.append(_kb(0x8C, 0x13))
    return out


def kb_effect(name, tempo, c1, c2):
    typ = EFFECTS[name]
    n = 2 if name in ("wave", "mixpulse") else 1
    return _kb(0x80, typ, tempo, 0x00, 0x00, 0x01, 0x01, 0x01, n - 1, *c1, *c2)


def kb_brightness(percent):
    """Hardware dimmer, 0..255 on the wire. Measured monotonic; colors are
    kept across 0 and back."""
    return _kb(0x83, 0x38, 0x9C, round(percent * 255 / 100))


# ---- chassis --------------------------------------------------------------


def _control(kind, target=(0x00, 0xFF)):
    return _elc(0x03, 0x21, 0x00, kind, *target)


def elc_zone_colors(pairs):
    """setOneColor (0x27) for the non-power zones, one command per color.
    pairs: [(zone_id, (r, g, b))]. Measured on the lid logo and rear strip."""
    by_color = {}
    for zid, rgb in pairs:
        by_color.setdefault(tuple(rgb), []).append(zid)
    out = [_control(0x04), _control(0x01)]
    for (r, g, b), ids in by_color.items():
        out.append(_elc(0x03, 0x27, r, g, b, 0x00, len(ids), *ids))
    out.append(_control(0x03))
    return out


# Chassis actions (AlienFX-SDK SetV4Action): colorSel 0x23 on a light index,
# then colorSet 0x24 with up to 3 actions of 8 bytes, repeated for more:
#   [type, duration ms (16-bit BE), tempo (16-bit BE), R, G, B]
#   type 0 color / 1 pulse / 2 morph
# The SDK calls the duration bytes "time" and "opcode" (07 d0, 07 dc, 07 cf):
# they are 2000 ms and so on, as OpenRGB decodes AWCC traces; AWCC sends
# tempo = duration / 10, between 100 and 250 (static: 2000 ms, tempo 250).
# Measured here (2026-10-07): a 2-step morph of 207 ms steps loops in 0.42 s
# on the lid logo, while the very same actions on the power button loop in
# 3.0 s: power steps are scaled down by POWER_SLOWDOWN.
# Static color is the sequence measured on camera (power button, 13:16-13:50).
# Other effects are morph loops through keyframes (effects.chassis_frames).
ZONE_EFFECTS = ("static", "pulse", "morph")
ACTIONS_PER_PACKET = 3
ELC_COMMAND_TIME = 0.064  # s per command, measured
POWER_SLOWDOWN = 3.0 / 0.42  # measured once, on the m15 R7
TEMPO_RANGE = (15, 250)  # AWCC: 100..250; OpenRGB goes down to 15 for spectrum


def _action(kind, ms, rgb, slowdown=1.0):
    tempo = max(1, round(min(TEMPO_RANGE[1], max(TEMPO_RANGE[0], ms / 10)) / slowdown))
    ms = max(1, min(0xFFFF, round(ms / slowdown)))
    return [kind, ms >> 8, ms & 0xFF, tempo >> 8, tempo & 0xFF, *rgb]


def v4_actions(effect, slowdown=1.0):
    """effect: {"name", "tempo", "c1", "c2"[, "frames", "loop"]} -> 8-byte action
    records. "frames": the keyframes of a looped morph lasting "loop" seconds
    (one frame = a static color). slowdown: POWER_SLOWDOWN for the power button."""
    frames = effect.get("frames")
    if effect["name"] == "static" or (frames and len(frames) == 1):
        return [[0x00, 0x07, 0xD0, 0x00, 0xFA, *(frames[0] if frames else effect["c1"])]]  # 2000 ms, 250
    loop = effect.get("loop") or 0.6 * effect["tempo"] * (1 if effect["name"] == "pulse" else 2)
    if effect["name"] == "pulse":
        return [_action(1, loop * 1000, effect["c1"], slowdown)]
    frames = frames or [effect["c1"], effect["c2"]]
    return [_action(2, loop * 1000 / len(frames), c, slowdown) for c in frames]


def _color_sets(actions):
    return [
        _elc(0x03, 0x24, *[b for a in actions[i : i + ACTIONS_PER_PACKET] for b in a])
        for i in range(0, len(actions), ACTIONS_PER_PACKET)
    ]


def elc_zone_effects(items):
    """Animated non-power zones. items: [(zone_id, effect)]."""
    out = [_control(0x04), _control(0x01)]
    for zid, effect in items:
        out += [_elc(0x03, 0x23, 0x01, 0x00, 0x01, zid), *_color_sets(v4_actions(effect))]
    out.append(_control(0x03))
    return out


def elc_power(rgb, effect=None, index=POWER_INDEX):
    """Program the power button for every power state (32 commands, ~2 s:
    the controller takes 64 ms per command; 6 more for a 6-keyframe loop).
    Static color measured at 13:50; effects reuse the same frame, not
    camera-verified."""
    color_sets = _color_sets(v4_actions(effect or {"name": "static", "c1": rgb}, POWER_SLOWDOWN))
    out = [_control(0x03)]
    for state in POWER_STATES:
        out += [
            _elc(0x03, 0x22, 0x00, 0x04, 0x00, state),  # remove
            _elc(0x03, 0x22, 0x00, 0x01, 0x00, state),  # start
            _elc(0x03, 0x23, 0x01, 0x00, 0x01, index),
            *color_sets,
            _elc(0x03, 0x22, 0x00, 0x02, 0x00, state),  # finish + save
        ]
    out.append(_control(0x05))  # play
    return out


def scale(rgb, percent):
    """Chassis has no observable hardware dimmer: dim by scaling the color."""
    return tuple(round(c * percent / 100) for c in rgb)


# ---- legacy AlienFX (USB HID "API v2/v3", 187c:0511..0530) -----------------
# Not measured here: byte layout from T-Troll's AlienFX-SDK (MIT,
# alienfx-controls.h COMMV1_*, AlienFX_SDK.cpp SetMaskAndColor/Reset/
# UpdateColors), cross-checked with akbl and trackmastersteve/alienfx.
# OUTPUT report id 0x02; 9 bytes on the wire (v2, 4-bit color) or 12 (v3).
LEGACY_REPORT_ID = 0x02
LEGACY_V2_SIZE, LEGACY_V3_SIZE = 9, 12
LEGACY_READY = 0x10  # status byte 0 after 02 06 (0x11 busy, 0x12 unknown command)


def _legacy(size, *payload):
    buf = bytes([LEGACY_REPORT_ID, *payload])
    assert len(buf) <= size
    return buf.ljust(size, b"\0")


def legacy_reset(size):
    """02 07 04: all lights on (1 touch controls, 2 sleep, 3 all off)."""
    return _legacy(size, 0x07, 0x04)


def legacy_status(size):
    return _legacy(size, 0x06)


def legacy_colors(size, pairs):
    """Static colors. pairs: [(mask, (r, g, b))], mask = 24-bit zone mask.
    Per zone: color command (opcode 3, chain, mask, color) then loop end
    (02 04), the chain counting up from 1; finally update (02 05)."""
    out = []
    for chain, (mask, (r, g, b)) in enumerate(pairs, start=1):
        if size == LEGACY_V2_SIZE:  # 4 bits per channel, two colors packed in 3 bytes
            color = [(r & 0xF0) | (g >> 4), b & 0xF0, 0]
        else:
            color = [r, g, b, 0, 0, 0]
        out.append(_legacy(size, 0x03, chain & 0xFF, (mask >> 16) & 0xFF, (mask >> 8) & 0xFF, mask & 0xFF, *color))
        out.append(_legacy(size, 0x04))
    out.append(_legacy(size, 0x05))
    return out


# ---- kernel alienware-wmi (rgb_zones sysfs) --------------------------------
def wmi_value(rgb):
    """Text written to /sys/devices/platform/alienware-wmi/rgb_zones/zoneNN.
    From the kernel source (alienware-wmi-base.c): the hex value is masked
    with 0x0f0f0f per channel, so each channel is 0..15. Not measured here."""
    r, g, b = (c >> 4 for c in rgb)
    return f"{r:02x}{g:02x}{b:02x}\n"
