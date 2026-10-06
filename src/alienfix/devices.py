"""Find and drive the lighting controllers (hidraw nodes and one sysfs class).

Controllers are recognised by their HID report descriptor, not by a product
id list, so variants with another PID are still found:
- keyboard: Darfon-style per-key controller: vendor usage page 0xFF89,
  usage 0xCC, FEATURE report 0xCC (63 bytes) — "API v5".
- elc:      AW-ELC, Alienware VID 0x187C, vendor page 0xFF00, 33-byte OUTPUT
  report without id — "API v4" (187c:0550, 187c:0551).
- legacy:   older AlienFX, VID 0x187C, OUTPUT report 0x02 of 8 or 11 bytes —
  "API v2/v3" (187c:0511..0530). Same rule as the AlienFX-SDK, which tells
  the versions apart by output report length (9 / 12 / 34 with the id byte).
- wmi:      the kernel's alienware-wmi rgb_zones (pre-2018 desktops such as
  the X51 and Alpha); no hidraw involved.
The keyboard node also carries keystrokes: only the daemon opens it.
"""
import errno
import fcntl
import logging
import os
import time
from pathlib import Path

from . import protocol

log = logging.getLogger("alienfixd.devices")
SYS = Path("/sys/class/hidraw")
WMI = Path("/sys/devices/platform/alienware-wmi/rgb_zones")
ALIENWARE_VID = "0000187C"


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def _HIDIOCSFEATURE(n):
    return _ioc(3, 0x06, n)


def _HIDIOCGINPUT(n):
    return _ioc(3, 0x0A, n)


def output_reports(desc):
    """{report id: payload bytes} of the OUTPUT reports in a HID report
    descriptor (short items only; enough for these vendor collections)."""
    sizes, rid, rsize, rcount, i = {}, 0, 0, 0, 0
    while i < len(desc):
        prefix = desc[i]
        if prefix == 0xFE:                               # long item: skip
            i += 3 + (desc[i + 1] if i + 1 < len(desc) else 0)
            continue
        n = (0, 1, 2, 4)[prefix & 0x03]
        data = int.from_bytes(desc[i + 1:i + 1 + n], "little")
        tag = prefix & 0xFC
        if tag == 0x84:
            rid = data
        elif tag == 0x74:
            rsize = data
        elif tag == 0x94:
            rcount = data
        elif tag == 0x90:                                # Output main item
            sizes[rid] = sizes.get(rid, 0) + rsize * rcount
        i += 1 + n
    return {r: (bits + 7) // 8 for r, bits in sizes.items()}


KB_SIGNATURE = bytes.fromhex("0689ff09cca10185cc")   # page FF89, usage CC, collection, report id CC


def _hid_id(node):
    for line in (node / "device/uevent").read_text().splitlines():
        if line.startswith("HID_ID="):
            _bus, vid, pid = line[7:].split(":")
            return vid.upper(), pid.upper()
    return None, None


def classify(vid, desc):
    """-> ("keyboard" | "elc" | "legacy", info) or (None, None)."""
    if KB_SIGNATURE in desc and b"\x95\x3f\xb1" in desc:          # 63 x 8-bit Feature
        return "keyboard", {}
    if vid != ALIENWARE_VID:
        return None, None
    out = output_reports(desc)
    if out.get(0) == 33 and desc.startswith(bytes.fromhex("0600ff0901a101")):
        return "elc", {}
    if out.get(protocol.LEGACY_REPORT_ID) in (8, 11):
        return "legacy", {"size": out[protocol.LEGACY_REPORT_ID] + 1}
    return None, None


def find():
    """-> {"keyboard": (path, info) | None, "elc": ..., "legacy": ...}"""
    found = {"keyboard": None, "elc": None, "legacy": None}
    if not SYS.exists():
        return found
    for node in sorted(SYS.iterdir()):
        try:
            desc = (node / "device/report_descriptor").read_bytes()
            vid, pid = _hid_id(node)
        except OSError:
            continue
        role, info = classify(vid, desc)
        if role and found[role] is None:
            found[role] = (f"/dev/{node.name}", {**info, "usb": f"{vid[-4:]}:{pid[-4:]}".lower()})
    return found


def wmi_zone_count():
    try:
        return len([p for p in WMI.iterdir() if p.name.startswith("zone")])
    except OSError:
        return 0


class Device:
    """One hidraw node, reopened transparently if it vanished (resume, replug)."""

    def __init__(self, role):
        self.role = role
        self.fd = None
        self.path = None
        self.info = {}

    def _open(self):
        hit = find()[self.role]
        if hit is None:
            raise FileNotFoundError(f"{self.role} controller not found")
        self.path, self.info = hit
        self.fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC)
        log.info("%s: opened %s (%s)", self.role, self.path, self.info.get("usb"))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
        self.fd = None

    def ensure_open(self):
        if self.fd is None:
            self._open()

    def send(self, reports, pace=0.0):
        for attempt in (1, 2):
            try:
                self.ensure_open()
                for r in reports:
                    self._send_one(r)
                    if pace:
                        time.sleep(pace)
                return
            except OSError as e:
                self.close()
                if attempt == 2 or e.errno not in (errno.ENODEV, errno.EIO, errno.ENOENT, errno.EPIPE):
                    raise
                log.warning("%s: %s, reopening", self.role, e)
                time.sleep(0.5)

    def _send_one(self, report):
        if self.role == "keyboard":
            buf = bytearray(report)
            fcntl.ioctl(self.fd, _HIDIOCSFEATURE(len(buf)), buf)
        else:
            os.write(self.fd, report)          # ELC: ~64 ms per command (controller-side)

    def input_report(self, report_id, size):
        """HIDIOCGINPUT; None if the kernel or device does not answer."""
        buf = bytearray([report_id]) + bytearray(size - 1)
        try:
            fcntl.ioctl(self.fd, _HIDIOCGINPUT(len(buf)), buf)
        except OSError:
            return None
        return bytes(buf)


class Hardware:
    """All controllers + the encoders."""

    def __init__(self):
        self.kb = Device("keyboard")
        self.elc = Device("elc")
        self.legacy = Device("legacy")

    def presence(self):
        f = find()
        out = {role: f[role] is not None for role in ("keyboard", "elc", "legacy")}
        out["wmi"] = wmi_zone_count() > 0
        out["usb"] = sorted(v[1]["usb"] for v in f.values() if v)
        return out

    @staticmethod
    def wmi_zone_count():
        return wmi_zone_count()

    @staticmethod
    def keyboard_usb():
        """vid:pid of the per-key lighting keyboard, which also types the keys."""
        kb = find()["keyboard"]
        return kb[1]["usb"] if kb else None

    # ---- per-key keyboard ---------------------------------------------------
    def _custom_mode(self):
        self.kb.send([protocol.kb_custom_mode()])
        time.sleep(protocol.MODE_SETTLE)

    def keyboard_static(self, colors, brightness):
        self._custom_mode()
        self.kb.send([*protocol.kb_colors(sorted(colors.items())), protocol.kb_brightness(brightness)],
                     pace=0.002)

    def keyboard_effect(self, effect, brightness):
        self.kb.send([protocol.kb_effect(effect["name"], effect["tempo"], effect["c1"], effect["c2"])])
        time.sleep(protocol.MODE_SETTLE)
        self.kb.send([protocol.kb_brightness(brightness)])

    def keyboard_frame(self, pairs, enter_custom=False):
        if enter_custom:
            self._custom_mode()
        self.kb.send(protocol.kb_colors(pairs))

    def keyboard_soft_start(self, brightness):
        """Enter per-key mode for a daemon-rendered effect, then set brightness."""
        self._custom_mode()
        self.kb.send([protocol.kb_brightness(brightness)])

    # ---- AW-ELC -------------------------------------------------------------
    def zones(self, pairs):
        self.elc.send(protocol.elc_zone_colors(pairs))

    def zone_effects(self, items):
        self.elc.send(protocol.elc_zone_effects(items))

    def power(self, rgb, effect=None, index=protocol.POWER_INDEX):
        self.elc.send(protocol.elc_power(rgb, effect, index))

    # ---- legacy AlienFX -----------------------------------------------------
    def legacy_zones(self, pairs):
        """pairs: [(mask, rgb)]. Reset, wait for ready (best effort), colours, update."""
        dev = self.legacy
        dev.ensure_open()
        size = dev.info.get("size", protocol.LEGACY_V2_SIZE)
        dev.send([protocol.legacy_reset(size)])
        self._legacy_wait_ready(size)
        dev.send(protocol.legacy_colors(size, pairs))

    def _legacy_wait_ready(self, size):
        for _ in range(50):                   # the SDK polls up to 100 x 10 ms
            self.legacy.send([protocol.legacy_status(size)])
            rep = self.legacy.input_report(protocol.LEGACY_REPORT_ID, size)
            if rep is None or protocol.LEGACY_READY in rep[:2]:
                return
            time.sleep(0.01)

    # ---- kernel alienware-wmi -----------------------------------------------
    @staticmethod
    def wmi_zones(pairs):
        for index, rgb in pairs:
            (WMI / f"zone{index:02d}").write_text(protocol.wmi_value(rgb))
