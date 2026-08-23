import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress, decompress, recursive_compress


class TestEdgeCases(unittest.TestCase):
    def roundtrip(self, data):
        blob = compress(data)
        self.assertEqual(decompress(blob), data)
        return len(data), len(blob)

    def test_empty_file(self):
        o, c = self.roundtrip(b"")
        self.assertEqual(c, 10)

    def test_single_byte(self):
        for b in (0x00, 0x41, 0xFF):
            self.roundtrip(bytes((b,)))

    def test_two_bytes(self):
        self.roundtrip(b"AB")
        self.roundtrip(b"AA")

    def test_all_zero_data(self):
        o, c = self.roundtrip(bytes(100000))
        self.assertLess(c / max(o, 1), 0.01)

    def test_all_one_data(self):
        o, c = self.roundtrip(b"\xff" * 100000)
        self.assertLess(c / max(o, 1), 0.01)

    def test_repeating_patterns(self):
        for pattern in (b"A", b"AB", b"ABC", b"ABCD", b"\x00\x80", b"Hello World! "):
            self.roundtrip(pattern * (5000 // max(1, len(pattern))))

    def test_periodic_with_noise(self):
        base = b"0123456789abcdef" * 64
        noisy = bytearray(base)
        for i in range(0, len(noisy), 97):
            noisy[i] ^= 0x5A
        self.roundtrip(bytes(noisy))

    def test_unicode_text(self):
        text = ("Ünïcödé ✓ 日本語テキスト 🚀 emoji + ascii mix\n" * 300)
        self.roundtrip(text.encode("utf-8"))

    def test_binary_structured(self):
        import struct
        vals = [i * 3 % 65536 for i in range(20000)]
        self.roundtrip(struct.pack("<%dH" % len(vals), *vals))

    def test_large_input(self):
        rng = os.urandom(512 * 1024)
        self.roundtrip(rng)

    def test_recursive_compression_terminates_and_is_lossless(self):
        data = b"recursive recursive recursive " * 2000
        final, sizes = recursive_compress(data)
        self.assertEqual(sizes[0], len(data))
        self.assertEqual(len(final), sizes[-1])
        restored = decompress(decompress(final)) if len(sizes) > 2 else decompress(final)
        self.assertEqual(restored, data)

    def test_compress_accepts_bytearray(self):
        ba = bytearray(b"bytearray input works fine " * 50)
        blob = compress(ba)
        self.assertEqual(decompress(blob), bytes(ba))

    def test_depth_parameter(self):
        data = b"x" * 4096
        for depth in (0, 1, 2, 4):
            blob = compress(data, depth=depth)
            self.assertEqual(decompress(blob), data)


if __name__ == "__main__":
    unittest.main()
