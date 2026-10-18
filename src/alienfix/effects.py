"""Software keyboard effects, rendered by the daemon frame by frame.

The keyboard takes ~42 full frames/s (measured), so effects the firmware does
not have can be animated in software. The chassis controller takes 64 ms per
command, so chassis zones only get the controller's own actions (protocol.py).

Pure functions: (key positions, time, parameters) -> {id: (r, g, b)}.
Positions come from the chassis template (u, v in key units).
"""

import colorsys
import math
import random

SOFTWARE = ("rainbow", "spectrum", "morph", "starlight", "gradient")
# firmware effects drawn here anyway, on the chassis' clock (see chassis_frames)
SYNCED = ("breathing", "pulse")
ANIMATED = {"rainbow", "spectrum", "morph", "starlight"}  # gradient is static


def period(tempo):
    """Same meaning as the hardware tempo: a period, ~0.6 s x tempo."""
    return 0.6 * max(1, tempo)


def _mix(c1, c2, k):
    return tuple(round(a + (b - a) * k) for a, b in zip(c1, c2, strict=True))


def _hue(h):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def rainbow(pos, width, t, tempo, c1=None, c2=None):
    """Moves right; the rightmost key has the hue -t / p, like the chassis."""
    p, right = period(tempo), max((u for u, _v in pos.values()), default=0)
    return {i: _hue((u - right) / width - t / p) for i, (u, v) in pos.items()}


def spectrum(pos, width, t, tempo, c1=None, c2=None):
    color = _hue(t / (2 * period(tempo)))
    return dict.fromkeys(pos, color)


def morph(pos, width, t, tempo, c1, c2):
    k = 0.5 - 0.5 * math.cos(2 * math.pi * t / (2 * period(tempo)))
    color = _mix(c1, c2, k)
    return dict.fromkeys(pos, color)


def gradient(pos, width, t, tempo, c1, c2):
    return {i: _mix(c1, c2, min(1.0, max(0.0, u / width))) for i, (u, v) in pos.items()}


class Starlight:
    """Random keys light up in c1 and fade back to c2 (the background)."""

    LIFE = 0.9

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.stars = {}  # id -> start time
        self.last = None

    def __call__(self, pos, width, t, tempo, c1, c2):
        ids = list(pos)
        if self.last is None:
            self.last = t
        rate = len(ids) / (2 * period(tempo))  # every key twinkles about once per 2 periods
        for _ in range(int(rate * (t - self.last) + self.rng.random())):
            self.stars[self.rng.choice(ids)] = t
        self.last = t
        out = {}
        for i in ids:
            age = t - self.stars.get(i, -1e9)
            k = max(0.0, 1 - age / self.LIFE) if age >= 0 else 0.0
            out[i] = _mix(c2, c1, k)
        self.stars = {i: s for i, s in self.stars.items() if t - s < self.LIFE}
        return out


def renderer(name):
    return {"rainbow": rainbow, "spectrum": spectrum, "morph": morph, "gradient": gradient, "starlight": Starlight()}[
        name
    ]


# Software look-alikes of the firmware effects, for a client that paints over
# the current effect in live mode (the ripple): per-key frames and a firmware
# effect cannot run at the same time. Only the wave period is measured
# (0.6 s x tempo); the shapes are assumed from what the camera showed.
BLACK = (0, 0, 0)


def _band(x):
    """1 at the center of a band of half-width 1, 0 outside, smooth in between."""
    return 0.5 * (1 + math.cos(math.pi * x)) if abs(x) < 1 else 0.0


def breathing(pos, width, t, tempo, c1, c2=None):
    color = _mix(BLACK, c1, 0.5 - 0.5 * math.cos(2 * math.pi * t / period(tempo)))
    return dict.fromkeys(pos, color)


def pulse(pos, width, t, tempo, c1, c2=None):
    color = _mix(c1, BLACK, (t / period(tempo)) % 1.0)  # flash, then fade
    return dict.fromkeys(pos, color)


def mixpulse(pos, width, t, tempo, c1, c2):
    color = c1 if (t / period(tempo)) % 1.0 < 0.5 else c2
    return dict.fromkeys(pos, color)


def wave(pos, width, t, tempo, c1, c2):
    x0 = ((t / period(tempo)) % 1.0) * (width + 4) - 2  # band enters left, leaves right
    return {i: _mix(c2, c1, _band((u - x0) / 2)) for i, (u, v) in pos.items()}


def nightrider(pos, width, t, tempo, c1, c2=None):
    k = (t / period(tempo)) % 2.0
    x0 = (k if k < 1 else 2 - k) * width  # left to right and back
    return {i: _mix(BLACK, c1, _band((u - x0) / 1.5)) for i, (u, v) in pos.items()}


LOOKALIKE = {"breathing": breathing, "wave": wave, "pulse": pulse, "mixpulse": mixpulse, "nightrider": nightrider}


def any_renderer(name):
    """Renderer for any keyboard effect: software, or a firmware look-alike."""
    return renderer(name) if name in SOFTWARE else LOOKALIKE[name]


# Chassis zones (AW-ELC): at 64 ms per command they cannot be streamed, so
# every keyboard effect the controller lacks becomes a loop of keyframes the
# controller morphs through on its own (protocol.v4_actions). The loops last
# as long as the keyboard's (chassis_loop) and are sampled from the same
# clock: every zone shows the color of the keyboard's right edge at the same
# instant. A zone's offset (a fraction of the loop) makes up for the time its
# loop started away from the shared clock, plus its rainbow_phase if any.
CHASSIS = ("breathing", "wave", "mixpulse", "nightrider", "rainbow", "spectrum", "starlight", "gradient")
CHASSIS_STEPS = 6  # keyframes; 6 hues make an exact RGB hue wheel
# keyboard periods per loop: same formulas as the renderers above
LOOP_PERIODS = {
    "rainbow": 1,
    "pulse": 1,
    "spectrum": 2,
    "morph": 2,
    "breathing": 1,
    "mixpulse": 1,
    "wave": 1,
    "nightrider": 1,
    "starlight": 2,
}


def chassis_loop(name, tempo):
    """Seconds per chassis loop, as long as the keyboard's (None: static)."""
    return LOOP_PERIODS[name] * period(tempo) if name in LOOP_PERIODS else None


def _cos(x):
    return 0.5 - 0.5 * math.cos(2 * math.pi * x)


def _near_start(x):
    return round((x % 1.0) * CHASSIS_STEPS) % CHASSIS_STEPS == 0


_AT = {  # color at loop fraction x (0 = the shared clock's start)
    "rainbow": lambda x, c1, c2: _hue(-x),  # the keyboard's right edge
    "spectrum": lambda x, c1, c2: _hue(x),
    "breathing": lambda x, c1, c2: _mix(BLACK, c1, _cos(x)),
    "morph": lambda x, c1, c2: _mix(c1, c2, _cos(x)),
    "mixpulse": lambda x, c1, c2: c1 if x % 1.0 < 0.5 else c2,
    "wave": lambda x, c1, c2: c1 if _near_start(x) else c2,
    "nightrider": lambda x, c1, c2: c1 if _near_start(x) else BLACK,
}


def chassis_frames(name, c1, c2, offset=0.0, seed=0, place=0.0):
    """Keyframes of one zone, or None for the controller's own actions.
    offset: loop fraction this zone is ahead of the shared clock; seed: the
    zone's own stars; place: 0..1 across the zones (gradient)."""
    c1, c2, n = tuple(c1), tuple(c2), CHASSIS_STEPS
    if name in _AT:
        return [_AT[name](j / n + offset, c1, c2) for j in range(n)]
    if name == "starlight":  # random on purpose: each zone twinkles on its own
        frames = [c2] * n
        frames[random.Random(seed).randrange(n)] = c1
        return frames
    if name == "gradient":
        return [_mix(c1, c2, place)]
    return None


# Typing ripple: a ring from each pressed key, painted over what the keyboard
# shows. While it is on, the whole keyboard is rendered here, firmware effects
# included (as look-alikes): per-key frames replace the firmware effect.
RIPPLE_SPEED = 10  # u / s, default
RIPPLE_SPEEDS = (2, 40)  # u / s, accepted range
RIPPLE_WIDTH = 1.3  # u, full width of the ring
RIPPLE_REACH = 9.0  # u traveled before the ring has faded out
RIPPLE_MAX = 32
RIPPLE_UNDER = ("effect", "color")  # what shows under the rings


class Ripple:
    __slots__ = ("color", "speed", "t0", "u", "v")

    def __init__(self, u, v, t0, color, speed=RIPPLE_SPEED):
        self.u, self.v, self.t0, self.color, self.speed = u, v, t0, tuple(color), speed

    @property
    def life(self):
        return RIPPLE_REACH / self.speed


def ripple_intensity(distance, age, speed=RIPPLE_SPEED):
    """0..1 contribution of a ring of given age (s) and speed (u/s) at distance (u)."""
    life = RIPPLE_REACH / speed
    if age < 0 or age >= life:
        return 0.0
    x = abs(distance - speed * age) / (RIPPLE_WIDTH / 2)
    if x >= 1:
        return 0.0
    return 0.5 * (1 + math.cos(math.pi * x)) * (1 - age / life)


def ripple_over(base, ripples, now, pos):
    """{id: color}: the strongest ring at each key blended over base."""
    out = {}
    for i, (u, v) in pos.items():
        under = base.get(i, BLACK)
        best, color = 0.0, under
        for rp in ripples:
            k = ripple_intensity(math.hypot(u - rp.u, v - rp.v), now - rp.t0, rp.speed)
            if k > best:
                best, color = k, rp.color
        out[i] = _mix(under, color, best)
    return out
