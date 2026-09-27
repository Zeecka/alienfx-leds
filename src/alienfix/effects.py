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
ANIMATED = {"rainbow", "spectrum", "morph", "starlight"}      # gradient is static


def period(tempo):
    """Same meaning as the hardware tempo: a period, ~0.6 s x tempo."""
    return 0.6 * max(1, tempo)


def _mix(c1, c2, k):
    return tuple(int(round(a + (b - a) * k)) for a, b in zip(c1, c2))


def _hue(h):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return (int(r * 255), int(g * 255), int(b * 255))


def rainbow(pos, width, t, tempo, c1=None, c2=None):
    p = period(tempo)
    return {i: _hue(u / width - t / p) for i, (u, v) in pos.items()}


def spectrum(pos, width, t, tempo, c1=None, c2=None):
    colour = _hue(t / (2 * period(tempo)))
    return {i: colour for i in pos}


def morph(pos, width, t, tempo, c1, c2):
    k = 0.5 - 0.5 * math.cos(2 * math.pi * t / (2 * period(tempo)))
    colour = _mix(c1, c2, k)
    return {i: colour for i in pos}


def gradient(pos, width, t, tempo, c1, c2):
    return {i: _mix(c1, c2, min(1.0, max(0.0, u / width))) for i, (u, v) in pos.items()}


class Starlight:
    """Random keys light up in c1 and fade back to c2 (the background)."""

    LIFE = 0.9

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.stars = {}            # id -> start time
        self.last = None

    def __call__(self, pos, width, t, tempo, c1, c2):
        ids = list(pos)
        if self.last is None:
            self.last = t
        rate = len(ids) / (2 * period(tempo))          # every key twinkles about once per 2 periods
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
    return {"rainbow": rainbow, "spectrum": spectrum, "morph": morph,
            "gradient": gradient, "starlight": Starlight()}[name]
