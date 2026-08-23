import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode.v2.lz import (LIT, MATCH, REP, MIN_MATCH,
                                DEFAULT_WINDOW, tokenize, apply_tokens,
                                token_stats)
from divideencode.v2 import build_header, parse_header, MAGIC, ContainerError

ROOT = os.path.join(os.path.dirname(__file__), "..")
SAMPLES = os.path.join(ROOT, "samples")


def rt(data, **kw):
    tokens = tokenize(data, **kw)
    out = apply_tokens(tokens)
    assert out == data, "roundtrip mismatch (%d bytes)" % len(data)
    return tokens


class TestTokenizerRoundtrip(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(rt(b""), [])

    def test_one_byte(self):
        self.assertEqual(rt(b"\x00"), [(LIT, 0)])
        self.assertEqual(rt(b"A"), [(LIT, 65)])

    def test_two_bytes(self):
        rt(b"AB")
        rt(b"AA")

    def test_too_short_for_match(self):
        rt(b"abc")            # exactly MIN_MATCH-1
        rt(b"abcd")           # exactly MIN_MATCH, single occurrence

    def test_all_zero(self):
        data = bytes(100000)
        tokens = rt(data)
        st = token_stats(tokens)
        self.assertGreater(st["match_covered"], 90000)

    def test_all_one(self):
        data = b"\xff" * 100000
        tokens = rt(data)
        st = token_stats(tokens)
        self.assertGreater(st["match_covered"], 90000)

    def test_repeated_text(self):
        rt(b"the quick brown fox " * 5000)

    def test_random_bytes(self):
        rng = random.Random(1234)
        for size in (64, 4096, 65536):
            rt(bytes(rng.getrandbits(8) for _ in range(size)))

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
                data = fh.read(262144)
            rt(data)
            checked += 1
        self.assertGreater(checked, 0)


class TestTokenizerProperties(unittest.TestCase):
    def test_deterministic(self):
        data = ("{ \"k\": [1,2,3], \"msg\": \"hello world\" }\n" * 3000
                ).encode()
        self.assertEqual(tokenize(data), tokenize(data))

    def test_window_respected(self):
        rng = random.Random(7)
        block = bytes(rng.getrandbits(8) for _ in range(4096))
        data = block * 20                      # repeats far beyond window
        window = 8192
        tokens = tokenize(data, window_size=window)
        for tok in tokens:
            if tok[0] == MATCH:
                self.assertLessEqual(tok[2], window)
        self.assertEqual(apply_tokens(tokens), data)

    def test_lazy_finds_longer_match(self):
        # 'abcd' ... 'abcX' ... : greedy would take len-4 at 'abc',
        # lazy should defer and catch the longer run afterwards.
        data = b"abcdefg" + b"x" * 50 + b"abcdefgh" + b"y" * 50
        tokens = rt(data)
        st = token_stats(tokens)
        self.assertGreater(st["matches"] + st["reps"], 0)

    def test_no_lazy_still_correct(self):
        data = b"abcabcabcabc" * 500
        self.assertEqual(apply_tokens(tokenize(data, lazy=False)), data)
        self.assertEqual(apply_tokens(tokenize(data, lazy=True)), data)

    def test_rep_offsets_used_on_repeats(self):
        # A-B-A pattern at distance d: second A should come out as a rep
        # or a match; reconstruction must hold either way.
        a = b"The quick brown fox jumps over the lazy dog."
        b = b"Pack my box with five dozen liquor jugs."
        data = a + b + a + a + b
        tokens = rt(data)
        st = token_stats(tokens)
        self.assertGreater(st["match_covered"], len(data) // 2)

    def test_small_window_roundtrip(self):
        data = (b"ABCDEFGH" * 3) * 200
        for w in (MIN_MATCH, 8, 64, 4096):
            tokens = tokenize(data, window_size=w)
            self.assertEqual(apply_tokens(tokens), data)


class TestDE2Scaffolding(unittest.TestCase):
    def test_header_roundtrip(self):
        import zlib
        hdr = build_header(123456, zlib.crc32(b"payload"))
        self.assertTrue(hdr.startswith(MAGIC))
        length, crc, payload_pos = parse_header(hdr)
        self.assertEqual(length, 123456)
        self.assertEqual(crc, zlib.crc32(b"payload"))
        self.assertEqual(payload_pos, len(hdr))

    def test_bad_magic_rejected(self):
        with self.assertRaises(ContainerError):
            parse_header(b"NOPE\x01")

    def test_bad_version_rejected(self):
        with self.assertRaises(ContainerError):
            parse_header(b"DE2\x09\x80")

    def test_truncated_rejected(self):
        with self.assertRaises(ContainerError):
            parse_header(b"DE2\x01")


if __name__ == "__main__":
    unittest.main()
