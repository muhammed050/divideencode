import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress, decompress
from divideencode.features import EncodeContext, extract_features


class TestFeatures(unittest.TestCase):
    def test_empty(self):
        f = extract_features(b"")
        self.assertEqual(f.n, 0)
        self.assertEqual(f.k, 0)

    def test_uniform_runs(self):
        f = extract_features(b"\x00" * 50000)
        self.assertAlmostEqual(f.run_bytes_frac, 1.0, places=6)
        self.assertTrue(f.big_run)
        self.assertEqual(f.zero_frac, 1.0)
        self.assertEqual(f.H0, 0.0)

    def test_text_like(self):
        f = extract_features(b"the quick brown fox " * 2000)
        self.assertGreater(f.printable_frac, 0.99)
        self.assertGreater(f.rep4, 0.5)

    def test_random_low_structure(self):
        f = extract_features(os.urandom(40000))
        self.assertEqual(f.k, 256)
        self.assertGreater(f.H0, 7.9)
        self.assertLess(f.rep4, 0.01)
        self.assertLess(f.delta_eq_frac, 0.01)

    def test_hi_zero_little_endian_u16(self):
        import struct
        vals = struct.pack("<%dH" % 1000, *([200] * 1000))
        f = extract_features(vals)
        self.assertEqual(f.hi_zero2, 1.0)
        vals_big = struct.pack("<%dH" % 1000,
                               *([200 if i % 2 else 60000
                                  for i in range(1000)]))
        f2 = extract_features(vals_big)
        self.assertAlmostEqual(f2.hi_zero2, 0.5)


class TestEncodeContext(unittest.TestCase):
    def test_memo_roundtrip(self):
        ctx = EncodeContext()
        key = ("k", 3)
        self.assertIsNone(ctx.memo_get(key))
        ctx.memo_put(key, b"payload", b"blob")
        self.assertEqual(ctx.memo_get(key), b"blob")

    def test_caches_isolated_between_instances(self):
        c1 = EncodeContext()
        c1.freq_table(b"abc")
        c2 = EncodeContext()
        self.assertNotIn(id(b"abc"), [k for k in c2.freq_cache])

    def test_compress_repeated_calls_no_stale_state(self):
        a = b"A" * 4096
        b = bytes(range(256)) * 32
        for data in (a, b, a, b):
            blob = compress(data)
            self.assertEqual(decompress(blob), data)

    def test_depth_in_memo_key_semantics(self):
        # same content at different depths must produce independently
        # cached blobs; both must decode.
        data = b"the quick brown fox jumps over the lazy dog. " * 300
        b1 = compress(data, depth=1)
        b2 = compress(data, depth=3)
        self.assertEqual(decompress(b1), data)
        self.assertEqual(decompress(b2), data)


if __name__ == "__main__":
    unittest.main()
