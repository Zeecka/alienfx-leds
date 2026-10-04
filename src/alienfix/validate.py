"""Validation of everything that arrives over D-Bus, before any hardware use.

Rule: nothing received is forwarded as-is. Integers are re-parsed and bounded,
colours are rebuilt from three bounded ints, names are looked up in closed
tables and replaced by our own constants. Anything else raises Invalid.
"""
from . import effects, protocol

# keyboard: static, hardware effects (firmware), software effects (daemon-rendered)
EFFECTS = ("static",) + tuple(protocol.EFFECTS) + effects.SOFTWARE
ZONE_EFFECTS = protocol.ZONE_EFFECTS
MAX_KEYS = 255


class Invalid(ValueError):
    pass


def _int(v, lo, hi, what):
    if isinstance(v, bool) or not isinstance(v, int):
        raise Invalid(f"{what}: integer expected")
    if not lo <= v <= hi:
        raise Invalid(f"{what}: {v} out of range {lo}..{hi}")
    return int(v)


def color(r, g, b):
    return (_int(r, 0, 255, "red"), _int(g, 0, 255, "green"), _int(b, 0, 255, "blue"))


def key_colors(entries, allowed_ids):
    """a(yyyy) -> {id: (r, g, b)} with ids restricted to allowed_ids (the active
    profile for persistent colours; 0..254 for transient live frames)."""
    if not isinstance(entries, (list, tuple)):
        raise Invalid("keys: array expected")
    if len(entries) > MAX_KEYS:
        raise Invalid(f"keys: at most {MAX_KEYS} entries")
    out = {}
    for e in entries:
        if not isinstance(e, (list, tuple)) or len(e) != 4:
            raise Invalid("keys: (id, r, g, b) expected")
        kid = _int(e[0], 0, 254, "key id")
        if kid not in allowed_ids:
            raise Invalid(f"key id {kid}: no such key")
        out[kid] = color(*e[1:])
    return out


def effect(name, tempo, c1, c2):
    if name not in EFFECTS:
        raise Invalid(f"effect: unknown")
    return {"name": EFFECTS[EFFECTS.index(name)],       # our constant, not the caller's string
            "tempo": _int(tempo, protocol.TEMPO_MIN, protocol.TEMPO_MAX, "tempo"),
            "c1": color(*c1), "c2": color(*c2)}


def zone_effect(name, tempo, c1, c2, allowed=ZONE_EFFECTS):
    if name not in ZONE_EFFECTS or name not in allowed:
        raise Invalid("zone effect: unknown")
    return {"name": ZONE_EFFECTS[ZONE_EFFECTS.index(name)],
            "tempo": _int(tempo, protocol.TEMPO_MIN, protocol.TEMPO_MAX, "tempo"),
            "c1": color(*c1), "c2": color(*c2)}


def zone(name, zones):
    """zones: the active model's zone ids (a closed list, our strings)."""
    zones = list(zones)
    if not isinstance(name, str) or name not in zones:
        raise Invalid("zone: unknown")
    return zones[zones.index(name)]


def percent(v):
    return _int(v, 0, 100, "brightness")


def ripple(enabled, rgb, speed, under, background):
    if not isinstance(enabled, bool):
        raise Invalid("ripple: boolean expected")
    if under not in effects.RIPPLE_UNDER:
        raise Invalid("ripple: under must be effect or color")
    return {"enabled": enabled, "color": list(color(*rgb)),
            "speed": _int(speed, *effects.RIPPLE_SPEEDS, "ripple speed"),
            "under": effects.RIPPLE_UNDER[effects.RIPPLE_UNDER.index(under)],
            "background": list(color(*background))}
