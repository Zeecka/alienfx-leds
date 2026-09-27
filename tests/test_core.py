"""Unit tests without hardware: exact bytes of measured sequences, and the
validation boundary. Run: python3 -m unittest discover -s tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from alienfix import protocol, validate  # noqa: E402
from alienfix.state import State  # noqa: E402
from alienfix import devices, models  # noqa: E402

IDS = {i for ids in json.loads((ROOT / "data/profiles/alienware-m15-r7-azerty.json").read_text())["keys"].values()
       for i in ids}
ZONE_IDS = ["power", "lid", "rear0", "rear1"]


class Protocol(unittest.TestCase):
    def test_custom_mode_matches_sdk_efftype0(self):
        r = protocol.kb_custom_mode()
        self.assertEqual(len(r), 64)
        self.assertEqual(r[:9].hex(" "), "cc 80 01 fe 00 00 01 01 01")

    def test_colors_are_one_based_15_per_packet_with_loop(self):
        pairs = [(i, (i, 0, 255)) for i in range(20)]
        out = protocol.kb_colors(pairs)
        self.assertEqual(len(out), 4)                      # 2 ColorSet + 2 Loop
        self.assertEqual(out[0][:7].hex(" "), "cc 8c 02 00 01 00 00")
        self.assertEqual(out[0][4 + 14 * 4], 15)           # 15th entry carries id 14 + 1
        self.assertEqual(out[1][:3].hex(" "), "cc 8c 13")
        self.assertTrue(all(len(r) == 64 for r in out))

    def test_effect_bytes_and_colour_count(self):
        w = protocol.kb_effect("wave", 5, (1, 2, 3), (4, 5, 6))
        self.assertEqual(w[:16].hex(" "), "cc 80 03 05 00 00 01 01 01 01 01 02 03 04 05 06")
        b = protocol.kb_effect("breathing", 7, (1, 2, 3), (4, 5, 6))
        self.assertEqual(b[9], 0)                          # one colour -> n-1 = 0

    def test_brightness_scale(self):
        self.assertEqual(protocol.kb_brightness(100)[4], 255)
        self.assertEqual(protocol.kb_brightness(0)[4], 0)

    def test_power_programs_six_states_on_index_2(self):
        out = protocol.elc_power((10, 20, 30))
        self.assertEqual(len(out), 32)
        self.assertTrue(all(len(r) == 34 and r[0] == 0 for r in out))
        sel = [r for r in out if r[2] == 0x23]
        self.assertEqual({r[6] for r in sel}, {2})
        states = sorted({r[6] for r in out if r[2] == 0x22})
        self.assertEqual(states, list(range(0x5B, 0x61)))

    def test_zone_colors_group_by_colour_and_never_send_ff(self):
        out = protocol.elc_zone_colors([(0, (1, 1, 1)), (1, (1, 1, 1)), (3, (9, 9, 9))])
        cmds = [r for r in out if r[2] == 0x27]
        self.assertEqual(len(cmds), 2)
        self.assertEqual(cmds[0][6:10].hex(" "), "00 02 00 01")
        for r in out:
            self.assertNotEqual(r[1:3], b"\x03\xff")       # 0xFF = flash erase per OpenRGB


class Validation(unittest.TestCase):
    def test_rejects_unknown_ids_and_out_of_range(self):
        with self.assertRaises(validate.Invalid):
            validate.key_colors([(33, 1, 2, 3)], IDS)       # 33 has no LED
        with self.assertRaises(validate.Invalid):
            validate.key_colors([(200, 1, 2, 3)], IDS)
        with self.assertRaises(validate.Invalid):
            validate.key_colors([(0, 256, 0, 0)], IDS)
        with self.assertRaises(validate.Invalid):
            validate.key_colors([(0, 1, 2)], IDS)
        with self.assertRaises(validate.Invalid):
            validate.key_colors([(0, 1, 2, 3)] * 256, IDS)
        self.assertEqual(validate.key_colors([(0, 1, 2, 3)], IDS), {0: (1, 2, 3)})

    def test_names_come_from_closed_tables(self):
        for bad in ("waves", "wave;rm -rf /", "", "WAVE"):
            with self.assertRaises(validate.Invalid):
                validate.effect(bad, 5, (0, 0, 0), (0, 0, 0))
        with self.assertRaises(validate.Invalid):
            validate.zone("fan", ZONE_IDS)
        name = "".join(["w", "a", "v", "e"])               # equal, but not our object
        self.assertIs(validate.effect(name, 5, (0, 0, 0), (0, 0, 0))["name"], validate.EFFECTS[validate.EFFECTS.index("wave")])

    def test_tempo_and_percent_bounds(self):
        for t in (0, 31, True):
            with self.assertRaises(validate.Invalid):
                validate.effect("wave", t, (0, 0, 0), (0, 0, 0))
        with self.assertRaises(validate.Invalid):
            validate.percent(101)


class Persistence(unittest.TestCase):
    def test_corrupt_or_hostile_state_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            p.write_text(json.dumps({"brightness": 999, "zones": {"fan": [1, 2, 3], "lid": [1, 2, 300]},
                                     "keyboard": {"keys": {"33": [1, 1, 1], "0": [5, 5, 5], "x": 1}}}))
            s = State(p, IDS, ZONE_IDS)
            self.assertEqual(s.data["brightness"], 80)
            self.assertNotIn("fan", s.data["zones"])
            self.assertEqual(s.data["zones"]["lid"], [0, 90, 255])
            self.assertEqual(s.data["keyboard"]["keys"], {"0": [5, 5, 5]})
            s.save()
            self.assertEqual(State(p, IDS, ZONE_IDS).data["keyboard"]["keys"], {"0": [5, 5, 5]})


if __name__ == "__main__":
    unittest.main()


class Profiles(unittest.TestCase):
    from alienfix import profile as P
    T = P.load_templates(ROOT / "data/chassis")

    def ok(self, **kw):
        base = {"name": "My PC", "chassis": "alienware-m15-r7-azerty", "keys": {"ESC": [0]}}
        return {**base, **kw}

    def test_shipped_profile_is_valid_and_complete(self):
        p = self.P.validate(json.loads((ROOT / "data/profiles/alienware-m15-r7-azerty.json").read_text()), self.T)
        self.assertEqual(len(p["keys"]), 85)
        self.assertNotIn("SPACE", p["keys"])

    def test_hostile_profiles_are_rejected(self):
        bad = [self.ok(chassis="../../etc/passwd"), self.ok(name=""), self.ok(name="x" * 61),
               self.ok(name="a\nb"), self.ok(keys={}), self.ok(keys={"NOTAKEY": [1]}),
               self.ok(keys={"ESC": [255]}), self.ok(keys={"ESC": [-1]}), self.ok(keys={"ESC": [True]}),
               self.ok(keys={"ESC": [1], "F1": [1]}), self.ok(keys={"ESC": [1, 2, 3, 4, 5]}),
               self.ok(method="exec")]
        for b in bad:
            with self.assertRaises(validate.Invalid, msg=repr(b)):
                self.P.validate(b, self.T)
        with self.assertRaises(validate.Invalid):
            self.P.parse("{" * 40000, self.T)

    def test_names_come_back_as_template_strings(self):
        p = self.P.validate(self.ok(keys={"".join("ESC"): [3]}), self.T)
        self.assertEqual(p["keys"], {"ESC": [3]})

    def test_template_rows_are_aligned(self):
        for t in self.T.values():
            rows = {}
            for k in t["keys"]:
                rows.setdefault(k["y"], []).append(k)
            for y, ks in rows.items():
                ks.sort(key=lambda k: k["x"])
                self.assertAlmostEqual(sum(k["w"] for k in ks), t["width"], msg=(t["id"], y))
                for a, b in zip(ks, ks[1:]):
                    self.assertAlmostEqual(a["x"] + a["w"], b["x"], msg=(t["id"], a["name"]))
                self.assertEqual(len({k["h"] for k in ks}), 1)

    def test_layout_positions_come_from_the_template(self):
        prof = self.P.validate(self.ok(keys={"ESC": [0], "BACKSPACE": [34, 35]}), self.T)
        lay = self.P.layout(prof, self.T[prof["chassis"]])
        self.assertEqual(lay["keys"]["34"]["u"], lay["keys"]["35"]["u"])
        self.assertEqual(lay["keys"]["0"]["v"], 0.25)
        self.assertIn("SPACE", lay["ghosts"])


class Effects(unittest.TestCase):
    from alienfix import effects as E
    POS = {0: (0.5, 0.25), 1: (8.0, 2.0), 2: (15.5, 5.0)}

    def test_software_renderers_stay_in_range_and_cover_every_key(self):
        for name in self.E.SOFTWARE:
            r = self.E.renderer(name)
            for t in (0, 0.37, 5.2):
                f = r(self.POS, 16, t, 5, (255, 0, 10), (0, 30, 255))
                self.assertEqual(set(f), set(self.POS), name)
                for c in f.values():
                    self.assertTrue(all(isinstance(x, int) and 0 <= x <= 255 for x in c), (name, c))

    def test_morph_and_rainbow_are_periodic(self):
        p = self.E.period(5)
        self.assertEqual(self.E.morph(self.POS, 16, 0, 5, (255, 0, 0), (0, 0, 255))[0], (255, 0, 0))
        self.assertEqual(self.E.morph(self.POS, 16, p, 5, (255, 0, 0), (0, 0, 255))[0], (0, 0, 255))
        a = self.E.rainbow(self.POS, 16, 1.0, 5)
        b = self.E.rainbow(self.POS, 16, 1.0 + p, 5)
        for k in a:                                   # equal up to float rounding
            self.assertTrue(all(abs(x - y) <= 1 for x, y in zip(a[k], b[k])), (a[k], b[k]))

    def test_gradient_goes_left_to_right(self):
        f = self.E.gradient(self.POS, 16, 0, 5, (255, 0, 0), (0, 0, 255))
        self.assertGreater(f[0][0], f[2][0])
        self.assertLess(f[0][2], f[2][2])


class ZoneEffects(unittest.TestCase):
    def test_zone_effect_validation(self):
        for bad in (("rainbow", 5), ("pulse", 0), ("pulse;x", 5), ("", 5)):
            with self.assertRaises(validate.Invalid):
                validate.zone_effect(bad[0], bad[1], (1, 2, 3), (4, 5, 6))
        e = validate.zone_effect("morph", 5, (1, 2, 3), (4, 5, 6))
        self.assertIs(e["name"], validate.ZONE_EFFECTS[2])

    def test_zone_action_bytes(self):
        acts = protocol.v4_actions({"name": "morph", "tempo": 5, "c1": (1, 2, 3), "c2": (4, 5, 6)})
        self.assertEqual(acts, [[2, 7, 0xCF, 0, 40, 1, 2, 3], [2, 7, 0xCF, 0, 40, 4, 5, 6]])
        out = protocol.elc_zone_effects([(3, {"name": "pulse", "tempo": 2, "c1": (9, 9, 9), "c2": (0, 0, 0)})])
        self.assertEqual(out[2][:7].hex(" "), "00 03 23 01 00 01 03")
        self.assertEqual(out[3][:11].hex(" "), "00 03 24 01 07 dc 00 10 09 09 09")
        self.assertTrue(all(len(r) == 34 for r in out))

    def test_power_static_unchanged_and_effect_used_for_every_state(self):
        base = protocol.elc_power((10, 20, 30))
        self.assertEqual(base, protocol.elc_power((10, 20, 30), {"name": "static", "tempo": 7,
                                                                   "c1": (10, 20, 30), "c2": (0, 0, 0)}))
        fx = protocol.elc_power((10, 20, 30), {"name": "pulse", "tempo": 7, "c1": (10, 20, 30), "c2": (0, 0, 0)})
        self.assertEqual(sum(1 for r in fx if r[2] == 0x24 and r[3] == 1), 6)

    def test_hostile_state_file_effect_names_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            p.write_text(json.dumps({"keyboard": {"mode": "effect", "effect": {
                "name": "../../x", "tempo": 5, "c1": [1, 1, 1], "c2": [2, 2, 2]}},
                "zone_effects": {"lid": {"name": "rm", "tempo": 5, "c2": [0, 0, 0]},
                                 "fan": {"name": "pulse", "tempo": 5, "c2": [0, 0, 0]}}}))
            s = State(p, IDS, ZONE_IDS)
            self.assertEqual(s.data["keyboard"]["effect"]["name"], "wave")
            self.assertEqual(s.data["zone_effects"]["lid"]["name"], "static")
            self.assertNotIn("fan", s.data["zone_effects"])


class Models(unittest.TestCase):
    ALL = models.load(ROOT / "data/models")

    def test_every_shipped_model_is_valid(self):
        self.assertGreater(len(self.ALL), 20)
        m = self.ALL["alienware-m15-r7"]
        self.assertEqual(m["support"], "verified")
        self.assertEqual({z["id"]: z["index"] for z in m["zones"]}, {"power": 2, "lid": 3, "rear0": 0, "rear1": 1})

    def test_pick_by_dmi_usb_and_choice(self):
        self.assertEqual(models.pick(self.ALL, "Alienware m15 R7")["id"], "alienware-m15-r7")
        self.assertEqual(models.pick(self.ALL, "Dell G15 5520")["keyboard"]["type"], "zones")
        self.assertIsNone(models.pick(self.ALL, "ThinkPad X1"))
        legacy = models.pick(self.ALL, "", usb=["187c:0521"])
        self.assertEqual(legacy["zones"][0]["controller"], "legacy")
        self.assertEqual(models.pick(self.ALL, "Alienware m15 R7", forced="dell-g15-5520")["id"], "dell-g15-5520")
        self.assertEqual(models.pick(self.ALL, "Alienware m15 R7", forced="nope")["id"], "alienware-m15-r7")

    def test_generic_model_from_presence(self):
        g = models.generic({"elc": True, "keyboard": True}, wmi_zones=2)
        models.check(g)
        self.assertEqual(g["keyboard"]["type"], "per-key")
        self.assertEqual([z["id"] for z in g["zones"]], ["elc0", "elc1", "elc2", "elc3", "wmi0", "wmi1"])

    def test_bad_models_are_rejected(self):
        ok = {"id": "x", "name": "X", "support": "reported", "keyboard": {"type": "none"},
              "zones": [{"id": "a", "label": "A", "controller": "elc", "index": 0}]}
        models.check(ok)
        for bad in ({**ok, "support": "sure"},
                    {**ok, "zones": [{"id": "a", "label": "A", "controller": "elc", "index": 99}]},
                    {**ok, "zones": [{"id": "a", "label": "A", "controller": "legacy", "mask": 0}]},
                    {**ok, "zones": [{"id": "a", "label": "A", "controller": "wmi", "index": 0, "role": "power"}]},
                    {**ok, "keyboard": {"type": "zones"}},
                    {**ok, "usb": ["187c-0521"]}):
            with self.assertRaises(validate.Invalid, msg=repr(bad)):
                models.check(bad)

    def test_zone_effects_follow_the_controller(self):
        self.assertEqual(models.zone_effects({"controller": "elc"}), ("static", "pulse", "morph"))
        self.assertEqual(models.zone_effects({"controller": "wmi"}), ("static",))
        with self.assertRaises(validate.Invalid):
            validate.zone_effect("pulse", 5, (1, 2, 3), (4, 5, 6), ("static",))


class Descriptors(unittest.TestCase):
    def test_output_report_sizes(self):
        desc = bytes.fromhex("0600ff0901a101" "0901" "150026ff00" "7508" "9521" "8102" "0901" "9102" "c0")
        self.assertEqual(devices.output_reports(desc), {0: 33})
        self.assertEqual(devices.classify("0000187C", desc)[0], "elc")
        self.assertIsNone(devices.classify("0000046D", desc)[0])
        legacy = bytes.fromhex("0600ff0901a101" "8502" "7508" "9508" "0901" "9102" "c0")
        self.assertEqual(devices.classify("0000187C", legacy), ("legacy", {"size": 9}))
        v3 = legacy.replace(bytes.fromhex("9508"), bytes.fromhex("950b"))
        self.assertEqual(devices.classify("0000187C", v3), ("legacy", {"size": 12}))


class LegacyAndWmi(unittest.TestCase):
    def test_legacy_v2_packets(self):
        out = protocol.legacy_colors(9, [(0x2000, (0xF0, 0x80, 0x10)), (0x0001, (1, 2, 3))])
        self.assertEqual(out[0], bytes([0x02, 0x03, 1, 0x00, 0x20, 0x00, 0xF8, 0x10, 0x00]))
        self.assertEqual(out[1], bytes([0x02, 0x04]) + bytes(7))
        self.assertEqual(out[2][:3], bytes([0x02, 0x03, 2]))
        self.assertEqual(out[-1], bytes([0x02, 0x05]) + bytes(7))
        self.assertTrue(all(len(r) == 9 for r in out))

    def test_legacy_v3_packets_are_8_bit(self):
        out = protocol.legacy_colors(12, [(0x10203, (0xAB, 0xCD, 0xEF))])
        self.assertEqual(out[0], bytes([0x02, 0x03, 1, 0x01, 0x02, 0x03, 0xAB, 0xCD, 0xEF, 0, 0, 0]))
        self.assertEqual(protocol.legacy_reset(12), bytes([0x02, 0x07, 0x04]) + bytes(9))

    def test_wmi_value_is_four_bit_per_channel(self):
        self.assertEqual(protocol.wmi_value((255, 128, 0)), "0f0800\n")

    def test_power_index_is_a_parameter(self):
        self.assertIn(bytes([0x00, 0x03, 0x23, 0x01, 0x00, 0x01, 0x04]),
                      [r[:7] for r in protocol.elc_power((1, 2, 3), index=4)])

