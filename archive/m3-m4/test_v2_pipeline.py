import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode.v2 import compress as v2c, decompress as v2d
from divideencode.v2.preproc import (
    delta_bytes, undelta_bytes, struct_pack, struct_unpack,
    plane_pack, plane_unpack, classify, plan_candidates,
    METHOD_STORED, METHOD_LZE, METHOD_DELTA, METHOD_RLE, METHOD_STRUCT,
    STRUCT_TEXT,
)
from divideencode.v2.codec import _method_payload

ROOT = os.path.join(os.path.dirname(__file__), "..")
SAMPLES = os.path.join(ROOT, "samples")


class TestTransforms(unittest.TestCase):
    def test_delta_roundtrip(self):
        rng = random.Random(5)
        for n in (0, 1, 255, 256, 4096):
            data = bytes(rng.getrandbits(8) for _ in range(n))
            self.assertEqual(undelta_bytes(delta_bytes(data)), data)

    def test_struct_text_roundtrip(self):
        data = b'{"k": "value", "n": [1, 2, 3]}\n' * 500
        packed = struct_pack(data)
        self.assertEqual(struct_unpack(packed, len(data)), data)

    def test_struct_empty(self):
        packed = struct_pack(b"")
        self.assertEqual(struct_unpack(packed, 0), b"")

    def test_plane_roundtrip(self):
        rng = random.Random(6)
        for k in (2, 4):
            data = bytes(rng.getrandbits(8) for _ in range(1003))
            packed = plane_pack(data, k)
            self.assertEqual(plane_unpack(packed, len(data), k), data)


class TestClassifier(unittest.TestCase):
    def test_deterministic(self):
        data = open(os.path.join(SAMPLES, "data.json"), "rb").read() \
            if os.path.exists(os.path.join(SAMPLES, "data.json")) \
            else b"x" * 20000
        self.assertEqual(plan_candidates(data), plan_candidates(data))

    def test_small_input_minimal(self):
        cands = plan_candidates(b"hello")
        self.assertEqual(cands[0], (METHOD_LZE, None))

    def test_binary_gets_numeric_candidates(self):
        import struct
        vals = [100000 + i * 17 for i in range(40000)]
        data = struct.pack("<%dI" % len(vals), *vals)
        methods = {m for m, _p in plan_candidates(data)}
        self.assertIn(METHOD_STRUCT, methods)
        # stride 4 expected for u32 ramps with stable top planes
        params = [p for m, p in plan_candidates(data)
                  if m == METHOD_STRUCT]
        self.assertIn(4, params)

    def test_text_gets_struct_text(self):
        data = ("{\"user\":\"bob\",\"action\":\"login\"}\n" * 3000).encode()
        params = [p for m, p in plan_candidates(data)
                  if m == METHOD_STRUCT]
        self.assertIn(STRUCT_TEXT, params)


class TestPipelineEndToEnd(unittest.TestCase):
    def _sample(self, name):
        path = os.path.join(SAMPLES, name)
        if not os.path.exists(path):
            self.skipTest("sample missing: %s" % name)
        with open(path, "rb") as fh:
            return fh.read()

    def test_numeric_files_improve_over_m2(self):
        # M2 (pre-preprocessing) reference sizes; M3 must not regress them.
        m2 = {"counters_u32.bin": 81119, "sensor_i16.bin": 117874,
              "random_prng.bin": 2790}
        for name, ref in m2.items():
            data = self._sample(name)
            blob = v2c(data)
            self.assertEqual(v2d(blob), data)
            self.assertLess(len(blob), ref, "%s did not improve" % name)

    def test_counters_beats_de1(self):
        data = self._sample("counters_u32.bin")
        blob = v2c(data)
        self.assertLess(len(blob), 9437)   # DE1 result

    def test_prng_beats_de1(self):
        data = self._sample("random_prng.bin")
        blob = v2c(data)
        self.assertLess(len(blob), 808)    # DE1 result

    def test_all_samples_roundtrip_and_noshrink(self):
        names = sorted(os.listdir(SAMPLES)) if os.path.isdir(SAMPLES) else []
        checked = 0
        for name in names:
            path = os.path.join(SAMPLES, name)
            if not os.path.isfile(path):
                continue
            data = self._sample(name)
            blob = v2c(data)
            self.assertEqual(v2d(blob), data, name)
            self.assertLessEqual(len(blob), len(data) + 16, name)
            checked += 1
        self.assertGreater(checked, 0)

    def test_forced_methods_roundtrip(self):
        data = (b"HEADER,col1,col2\n" + b"1234,abcdefghij,zzzzzzzz\n" * 800)
        for method in (METHOD_LZE, METHOD_DELTA, METHOD_RLE,
                       (METHOD_STRUCT, STRUCT_TEXT),
                       (METHOD_STRUCT, 2), (METHOD_STRUCT, 4)):
            if isinstance(method, tuple):
                payload = _method_payload(data, method[0], method[1])
                param = method[1]
            else:
                payload = _method_payload(data, method)
                param = None
            from divideencode.v2.codec import decode_payload, parse_header
            # wrap into a container manually to exercise decode paths
            import zlib
            from divideencode.v2.container import build_header
            blob = build_header(len(data), zlib.crc32(data)) + payload
            out = v2d(blob)
            self.assertEqual(out, data, "method %r" % (method,))


if __name__ == "__main__":
    unittest.main()
