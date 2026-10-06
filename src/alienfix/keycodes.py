"""Key presses of the built-in keyboard, for the typing ripple.

Privacy: the daemon reads key presses only while the ripple is on (off by
default, switched on through polkit). It reads only the built-in keyboard:
the per-key lighting keyboard itself (same USB id) and the laptop's i8042
keyboard. External keyboards are never opened. A press becomes a key name
and then a position on the keyboard; the code is not stored, logged or sent
anywhere. No grab: other programs get every key as usual.
"""

import logging
import os
import re
import select
import struct
import threading
import time
from pathlib import Path

log = logging.getLogger("alienfixd.keys")

INPUT_HEADER = "/usr/include/linux/input-event-codes.h"
FALLBACK_EVDEV = {
    "ESC": 1,
    "1": 2,
    "2": 3,
    "3": 4,
    "4": 5,
    "5": 6,
    "6": 7,
    "7": 8,
    "8": 9,
    "9": 10,
    "0": 11,
    "MINUS": 12,
    "EQUAL": 13,
    "BACKSPACE": 14,
    "TAB": 15,
    "Q": 16,
    "W": 17,
    "E": 18,
    "R": 19,
    "T": 20,
    "Y": 21,
    "U": 22,
    "I": 23,
    "O": 24,
    "P": 25,
    "LEFTBRACE": 26,
    "RIGHTBRACE": 27,
    "ENTER": 28,
    "LEFTCTRL": 29,
    "A": 30,
    "S": 31,
    "D": 32,
    "F": 33,
    "G": 34,
    "H": 35,
    "J": 36,
    "K": 37,
    "L": 38,
    "SEMICOLON": 39,
    "APOSTROPHE": 40,
    "GRAVE": 41,
    "LEFTSHIFT": 42,
    "BACKSLASH": 43,
    "Z": 44,
    "X": 45,
    "C": 46,
    "V": 47,
    "B": 48,
    "N": 49,
    "M": 50,
    "COMMA": 51,
    "DOT": 52,
    "SLASH": 53,
    "RIGHTSHIFT": 54,
    "LEFTALT": 56,
    "SPACE": 57,
    "CAPSLOCK": 58,
    "F1": 59,
    "F2": 60,
    "F3": 61,
    "F4": 62,
    "F5": 63,
    "F6": 64,
    "F7": 65,
    "F8": 66,
    "F9": 67,
    "F10": 68,
    "102ND": 86,
    "F11": 87,
    "F12": 88,
    "RIGHTCTRL": 97,
    "RIGHTALT": 100,
    "HOME": 102,
    "UP": 103,
    "LEFT": 105,
    "RIGHT": 106,
    "END": 107,
    "DOWN": 108,
    "DELETE": 111,
    "MUTE": 113,
    "VOLUMEDOWN": 114,
    "VOLUMEUP": 115,
    "LEFTMETA": 125,
    "MICMUTE": 248,
}


def load_evdev_names(path=INPUT_HEADER):
    """Return {evdev code: name} from the kernel header, or the fallback."""
    names = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    for m in re.finditer(r"^#define\s+KEY_(\w+)\s+(0x[0-9a-fA-F]+|\d+)\b", text, re.M):
        names.setdefault(int(m.group(2), 0), m.group(1))
    if not names:
        names = {code: name for name, code in FALLBACK_EVDEV.items()}
    return names


# struct input_event: struct timeval (two longs), __u16 type, __u16 code, __s32 value
EVENT = struct.Struct("@llHHi")
EV_KEY = 1
PRESS = 1
BUS_USB, BUS_I8042 = 0x03, 0x11
LETTERS = (FALLBACK_EVDEV["A"], FALLBACK_EVDEV["Z"], FALLBACK_EVDEV["SPACE"])
RESCAN_S = 5.0


def _has_keys(bitmap, codes):
    """bitmap: sysfs capabilities/key, hex words, most significant first."""
    words = [int(w, 16) for w in bitmap.split()][::-1]
    return all(c // 64 < len(words) and words[c // 64] >> (c % 64) & 1 for c in codes)


def builtin_keyboards(usb_id, sys_root="/sys"):
    """/dev/input/event* nodes of the built-in keyboard: the i8042 keyboard,
    and the USB keyboard whose vid:pid is the lighting keyboard's (usb_id)."""
    out = []
    for ev in sorted(Path(sys_root, "class/input").glob("event*")):
        dev = ev / "device"
        try:
            bus = int((dev / "id/bustype").read_text(encoding="utf-8"), 16)
            vendor = int((dev / "id/vendor").read_text(encoding="utf-8"), 16)
            product = int((dev / "id/product").read_text(encoding="utf-8"), 16)
            vid_pid = f"{vendor:04x}:{product:04x}"
            keys = (dev / "capabilities/key").read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        if not _has_keys(keys, LETTERS):
            continue
        if bus == BUS_I8042 or (bus == BUS_USB and usb_id and vid_pid == usb_id.lower()):
            out.append(f"/dev/input/{ev.name}")
    return out


def presses(data):
    """Key codes pressed in a buffer of input events (auto-repeat ignored)."""
    n = len(data) // EVENT.size
    return [
        code
        for _s, _us, typ, code, value in (EVENT.unpack_from(data, i * EVENT.size) for i in range(n))
        if typ == EV_KEY and value == PRESS
    ]


class KeyWatcher:
    """Calls on_press(code) for each key pressed on the built-in keyboard,
    from its own thread, between start() and stop()."""

    def __init__(self, on_press, usb_id, find=builtin_keyboards):
        self.on_press, self.usb_id, self.find = on_press, usb_id, find
        self.thread = None
        self.stopping = threading.Event()

    @property
    def running(self):
        return self.thread is not None

    def start(self):
        if self.thread is None:
            self.stopping.clear()
            self.thread = threading.Thread(target=self._run, name="keys", daemon=True)
            self.thread.start()

    def stop(self):
        if self.thread is not None:
            self.stopping.set()
            self.thread.join(timeout=2)
            self.thread = None

    def _run(self):
        fds, scanned = {}, 0.0
        try:
            while not self.stopping.is_set():
                if time.monotonic() - scanned > RESCAN_S:  # hot-plug, resume
                    scanned = time.monotonic()
                    for path in set(self.find(self.usb_id)) - set(fds.values()):
                        try:
                            fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)] = path
                            log.info("ripple: reading key presses from %s", path)
                        except OSError as e:
                            log.warning("ripple: cannot read %s: %s", path, e.strerror)
                if not fds:
                    self.stopping.wait(0.5)
                    continue
                for fd in select.select(list(fds), [], [], 0.5)[0]:
                    try:
                        data = os.read(fd, EVENT.size * 64)
                    except BlockingIOError:
                        continue
                    except OSError:
                        data = b""
                    if not data:  # device gone
                        os.close(fd)
                        fds.pop(fd)
                        continue
                    for code in presses(data):
                        self.on_press(code)
        finally:
            for fd in fds:
                os.close(fd)
            if fds:
                log.info("ripple: key presses no longer read")
