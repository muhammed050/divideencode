import hashlib
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress, decompress


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def roundtrip(data):
    blob = compress(data)
    restored = decompress(blob)
    assert restored == data, "byte mismatch"
    assert sha256(restored) == sha256(data), "sha mismatch"
    return len(data), len(blob)


class TestRoundTripText(unittest.TestCase):
    def test_ascii_text(self):
        data = b"the quick brown fox jumps over the lazy dog. " * 100
        orig, comp = roundtrip(data)
        self.assertLess(comp, orig)

    def test_unicode_text_utf8(self):
        text = ("héllo wörld — Ünïcode ✓ 数据压缩 🚀\n" * 200)
        roundtrip(text.encode("utf-8"))

    def test_mixed_lines(self):
        lines = []
        for i in range(5000):
            lines.append("line %d: value=%d status=OK" % (i, i * 7 % 1000))
        roundtrip(("\n".join(lines) + "\n").encode())


class TestRoundTripStructured(unittest.TestCase):
    def test_json_like(self):
        recs = []
        for i in range(3000):
            recs.append('{"id": %d, "name": "item_%d", "score": %.2f}'
                        % (i, i, (i * 13 % 997) / 7))
        roundtrip(("[" + ",\n".join(recs) + "]").encode())

    def test_csv_like(self):
        rows = ["id,name,amount,active"]
        for i in range(4000):
            rows.append("%d,user%d,%d.%02d,%s"
                        % (i, i % 97, i * 31 % 5000, i * 7 % 100,
                           "yes" if i % 3 else "no"))
        roundtrip(("\n".join(rows)).encode())

    def test_repeating_pattern(self):
        roundtrip(b"ABCABCABCABCABC" * 400)

    def test_long_runs(self):
        roundtrip(bytes(50000) + b"\x01" * 5000 + bytes(50000))

    def test_incrementing_bytes(self):
        roundtrip(bytes(i & 0xFF for i in range(65536)))


class TestRoundTripBinary(unittest.TestCase):
    def test_binary_all_values(self):
        roundtrip(bytes(range(256)) * 40)

    def test_pseudo_binary_structured(self):
        import struct
        values = [int(32000 + 500 * ((i * 37) % 19)) for i in range(8192)]
        roundtrip(struct.pack("<%dH" % len(values), *values))


class TestShaVerification(unittest.TestCase):
    def test_sha256_matches_on_various_inputs(self):
        rng = 12345
        state = rng
        for size in (0, 1, 2, 3, 17, 255, 256, 1024, 7777):
            buf = bytearray()
            while len(buf) < size:
                state = (state * 1103515245 + 12345) & 0x7FFFFFFF
                buf.append(state & 0xFF)
            data = bytes(buf[:size])
            blob = compress(data)
            restored = decompress(blob)
            self.assertEqual(sha256(data), sha256(restored))


if __name__ == "__main__":
    unittest.main()
