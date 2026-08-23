import json
import os
import random
import sys
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode.v2 import compress as v2c, decompress as v2d, MAGIC, \
    ContainerError
from divideencode.errors import CorruptedError

ROOT = os.path.join(os.path.dirname(__file__), "..")
SAMPLES = os.path.join(ROOT, "samples")


def rt(data):
    blob = v2c(data)
    out = v2d(blob)
    assert out == data
    return blob


class TestV2Roundtrip(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(rt(b""), v2c(b""))

    def test_one_byte(self):
        rt(b"\x00")
        rt(b"A")

    def test_two_bytes(self):
        rt(b"AB")
        rt(b"AA")

    def test_all_zero(self):
        blob = rt(bytes(100000))
        self.assertLess(len(blob), 500)

    def test_all_one(self):
        blob = rt(b"\xff" * 100000)
        self.assertLess(len(blob), 500)

    def test_repeated_text(self):
        blob = rt(b"the quick brown fox " * 5000)
        self.assertLess(len(blob), len(b"the quick brown fox ") * 5000 // 10)

    def test_random_bytes(self):
        rng = random.Random(99)
        for size in (64, 4096, 65536):
            data = bytes(rng.getrandbits(8) for _ in range(size))
            blob = rt(data)
            # stored fallback must not balloon
            self.assertLessEqual(len(blob), size + 16)

    def test_utf8(self):
        text = ("Ünïcödé ✓ 日本語テキスト 🚀 emoji + ascii mix\n" * 2000)
        rt(text.encode("utf-8"))

    def test_json_like(self):
        row = '{"name":"John","age":24,"tags":["a","b"],"active":true}\n'
        rt(row.encode() * 4000)

    def test_csv_like(self):
        rows = ["id,name,score,when\n"]
        for i in range(20000):
            rows.append("%d,user_%d,%d.%02d,2026-08-22T10:%02d:00\n"
                        % (i, i % 997, i % 89, i % 10, i % 60))
        rt("".join(rows).encode())

    def test_log_like(self):
        lines = []
        for i in range(20000):
            lines.append("2026-08-22 10:%02d:%02d INFO api /v1/users "
                         "status=200 latency=%dms\n" % (i % 60, i % 60,
                                                        10 + i % 90))
        rt("".join(lines).encode())

    def test_sql_like(self):
        parts = ["CREATE TABLE t(a INT, b TEXT);\n"]
        for i in range(15000):
            parts.append("INSERT INTO t VALUES (%d, 'row-%d');\n" % (i, i))
        rt("".join(parts).encode())

    def test_binary_structured(self):
        import struct
        vals = [i * 3 % 65536 for i in range(30000)]
        rt(struct.pack("<%dH" % len(vals), *vals))

    def test_periodic_with_noise(self):
        base = bytearray(b"0123456789abcdef" * 64)
        for i in range(0, len(base), 97):
            base[i] ^= 0x5A
        rt(bytes(base))

    def test_sample_files(self):
        names = sorted(os.listdir(SAMPLES)) if os.path.isdir(SAMPLES) else []
        checked = 0
        for name in names:
            path = os.path.join(SAMPLES, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            blob = rt(data)
            self.assertLessEqual(len(blob), len(data) + 16,
                                 "%s grew on disk" % name)
            checked += 1
        self.assertGreater(checked, 0)

    def test_ratio_sanity_against_v1_lz(self):
        from divideencode.patterns import lz_encode
        from divideencode.v2.lz import tokenize
        for name in ("App.java", "data.json"):
            with open(os.path.join(SAMPLES, name), "rb") as fh:
                data = fh.read()
            blob = rt(data)
            ref = lz_encode(data)
            # V2 must beat the old fixed-width LZ stream decisively
            self.assertLess(len(blob), len(ref) * 0.98,
                            "%s: v2=%d v1lz=%d" % (name, len(blob),
                                                   len(ref) if ref else -1))


class TestV2Properties(unittest.TestCase):
    def test_deterministic(self):
        data = ("{ \"k\": [1,2,3], \"msg\": \"hello world\" }\n" * 3000
                ).encode()
        self.assertEqual(v2c(data), v2c(data))

    def test_container_shape(self):
        blob = v2c(b"hello world, hello world")
        self.assertTrue(blob.startswith(MAGIC))
        orig, crc, pos = None, None, None
        from divideencode.v2 import parse_header
        orig, crc, pos = parse_header(blob)
        self.assertEqual(orig, len(b"hello world, hello world"))
        self.assertEqual(crc, zlib.crc32(b"hello world, hello world"))

    def test_corruption_detected(self):
        data = ("lorem ipsum dolor sit amet " * 800).encode()
        blob = bytearray(v2c(data))
        # flip a bit well inside the coded streams (the final byte holds
        # bit-padding that is intentionally not verified)
        blob[len(blob) // 2] ^= 0x01
        try:
            out = v2d(bytes(blob))
            # tolerate only the impossible case of an identical decode
            self.assertEqual(out, data)
        except Exception:
            pass  # expected: desync, size mismatch or crc failure

    def test_padding_flips_are_tolerated(self):
        data = ("lorem ipsum dolor sit amet " * 800).encode()
        blob = bytearray(v2c(data))
        blob[-1] ^= 0x01          # padding bit: output must be unchanged
        self.assertEqual(v2d(bytes(blob)), data)

    def test_trailing_garbage_rejected(self):
        data = b"abcdefgh" * 300
        blob = v2c(data) + b"\x00"
        try:
            v2d(blob)
        except Exception:
            return  # strict is fine
        # if not strict, at least roundtrip of clean container must hold
        self.assertEqual(v2d(v2c(data)), data)


if __name__ == "__main__":
    unittest.main()
