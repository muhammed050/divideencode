import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress, decompress


def lcg_bytes(size, seed):
    out = bytearray()
    s = seed & 0x7FFFFFFF
    while len(out) < size:
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((s >> 16) & 0xFF)
    return bytes(out[:size])


def biased_bytes(size, seed, alphabet):
    out = bytearray()
    s = seed & 0x7FFFFFFF
    k = len(alphabet)
    while len(out) < size:
        s = (s * 48271) % 2147483647
        out.append(alphabet[s % k])
    return bytes(out[:size])


class TestRandomData(unittest.TestCase):
    def test_crypto_random_never_corrupts(self):
        for size in (1, 10, 100, 1024, 16384):
            data = os.urandom(size)
            blob = compress(data)
            self.assertEqual(decompress(blob), data)

    def test_prng_random_multiple_seeds(self):
        for seed in (1, 42, 999, 20260822):
            for size in (512, 4096, 32768):
                data = lcg_bytes(size, seed)
                blob = compress(data)
                self.assertEqual(decompress(blob), data)

    def test_biased_random_low_entropy_alphabet(self):
        for alphabet in (b"AB", b"ACGT", b" \t\n"):
            for size in (1024, 16384):
                data = biased_bytes(size, 77, alphabet)
                blob = compress(data)
                self.assertEqual(decompress(blob), data)

    def test_random_ratio_is_bounded(self):
        for size in (4096, 32768, 131072):
            data = os.urandom(size)
            blob = compress(data)
            overhead = len(blob) - size
            self.assertLessEqual(overhead, 64,
                                 "random data must not inflate by more than header")

    def test_seedable_random_hex_text(self):
        data = os.urandom(2048).hex().encode()
        blob = compress(data)
        self.assertEqual(decompress(blob), data)


if __name__ == "__main__":
    unittest.main()
