#!/usr/bin/env python3
"""The real alienfixd on the SESSION bus, with fake hardware, for GUI tests.

Same D-Bus interface, same validation and state logic as the installed
service (src/alienfix/daemon.py); only the hardware layer is replaced by a
recorder that prints what would be sent. Nothing touches a device.

Environment:
  MOCK_MODEL=<id>   simulate that model (data/models/<id>.json), e.g.
                    dell-g15-5520, akbl-m14x-r1; default alienware-m15-r7.
                    MOCK_MODEL=generic simulates an unknown machine with an
                    AW-ELC controller and a per-key keyboard.
  MOCK_FAST=1       no simulated hardware delay (default: 64 ms per ELC
                    command, like the real controller).
  MOCK_DENY=1       refuse every mutating call with Error.NotAuthorized.
  MOCK_KEYS=<path>  read key presses for the ripple from this file (a FIFO
                    fed with struct input_event records) instead of the
                    built-in keyboard, which a user cannot open anyway.
"""

import logging
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("ALIENFIX_DATA", str(ROOT / "data"))
os.environ["STATE_DIRECTORY"] = tempfile.mkdtemp(prefix="alienfix-mock-")

from alienfix import daemon, models

MODEL = os.environ.get("MOCK_MODEL", "alienware-m15-r7")
FAST = os.environ.get("MOCK_FAST") == "1"
DENY = os.environ.get("MOCK_DENY") == "1"
KEYS = os.environ.get("MOCK_KEYS")
log = logging.getLogger("mock-hw")


class FakeHardware:
    """Records calls instead of writing reports."""

    def __init__(self):
        catalog = models.load(ROOT / "data" / "models")
        m = catalog.get(MODEL)
        ctrls = {z["controller"] for z in m["zones"]} if m else {"elc"}
        per_key = (m or {"keyboard": {"type": "per-key"}})["keyboard"]["type"] == "per-key"
        self._presence = {
            "keyboard": per_key,
            "elc": "elc" in ctrls,
            "legacy": "legacy" in ctrls,
            "wmi": "wmi" in ctrls,
            "usb": (m or {}).get("usb", []),
        }
        self.frames = 0

    def presence(self):
        return dict(self._presence)

    def wmi_zone_count(self):
        return 2 if self._presence["wmi"] else 0

    def keyboard_usb(self):
        return "0d62:dabc" if self._presence["keyboard"] else None

    def _cmd(self, what, n=1):
        log.info(what)
        if not FAST:
            time.sleep(0.064 * n)

    def keyboard_static(self, colors, brightness):
        self._cmd(f"keyboard static: {len(colors)} ids, brightness {brightness}")

    def keyboard_effect(self, effect, brightness):
        self._cmd(f"keyboard effect {effect['name']} tempo {effect['tempo']}")

    def keyboard_frame(self, pairs, enter_custom=False):
        self.frames += 1
        if self.frames % 30 == 0:
            log.info("live frame #%d (%d ids)", self.frames, len(pairs))

    def keyboard_soft_start(self, brightness):
        self._cmd("keyboard: per-key mode for a software effect")

    def zones(self, pairs):
        self._cmd(f"elc 0x27: {pairs}", len(pairs) + 3)

    def zone_effects(self, items):
        self._cmd(f"elc actions: {[(i, e['name']) for i, e in items]}", 2 * len(items) + 3)

    def power(self, rgb, effect=None, index=2):
        self._cmd(f"elc power button index {index}: {rgb} {effect and effect['name']}", 32)

    def legacy_zones(self, pairs):
        self._cmd(f"legacy: {[(hex(m), c) for m, c in pairs]}", len(pairs) * 2 + 2)

    def wmi_zones(self, pairs):
        self._cmd(f"wmi: {pairs}")


class MockDaemon(daemon.Daemon):
    def __init__(self, bus_type):
        super().__init__(bus_type)
        self.keys.find = lambda _usb: [KEYS] if KEYS else []
        self.key_log = 0

    def _on_key_press(self, code):
        super()._on_key_press(code)
        self.key_log += 1
        log.info("key press #%d -> ripple", self.key_log)  # the count only, like the real one

    def _on_call(self, conn, sender, path, iface, method, params, inv):
        if DENY and method not in daemon.READ_ONLY:
            inv.return_dbus_error(daemon.ERR + "NotAuthorized", "not allowed (MOCK_DENY)")
            return
        super()._on_call(conn, sender, path, iface, method, params, inv)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", stream=sys.stdout)
    setattr(daemon, "Hardware", FakeHardware)  # noqa: B010  (a test double, on purpose)
    catalog = models.load(ROOT / "data" / "models")
    dmi = (catalog[MODEL]["dmi_product"] or [""])[0] if MODEL in catalog else "Mock PC"
    setattr(daemon.Daemon, "dmi_product", staticmethod(lambda: dmi))  # noqa: B010
    if MODEL in catalog and not catalog[MODEL]["dmi_product"] and not catalog[MODEL].get("usb"):
        Path(os.environ["STATE_DIRECTORY"], "model").write_text(MODEL, encoding="utf-8")
    MockDaemon("session").run()


if __name__ == "__main__":
    main()
