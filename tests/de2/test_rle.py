import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress, decompress
from divideencode.patterns import rle_encode, rle_decode


def direct_roundtrip(data):
    """Encode/decode through the RLE codec alone and require exact recovery."""
    blob = rle_encode(data)
    assert blob is not None, "rle_encode returned None for %r" % data[:32]
    out, pos = rle_decode(blob, 0, len(blob), len(data))
    assert pos == len(blob), "rle decoder did not consume full stream"
    assert out == data, "RLE roundtrip mismatch for len=%d" % len(data)
    return blob


class TestRleroundtrip(unittest.TestCase):
    def _check(self, data):
        blob = direct_roundtrip(data)
        # RLE output must stay bounded: literals cost n + ceil(n/128),
        # runs cost 2 bytes per <=130 symbols. Anything wildly larger means
        # the encoder is emitting redundant data.
        self.assertLessEqual(len(blob), len(data) + len(data) // 128 + 8,
                             "RLE blob exploded: %d -> %d"
                             % (len(data), len(blob)))
        return blob

    def test_bug_case_xaaabbbc(self):
        """Regression test for the missing `lit_start` advance (P0 fix)."""
        blob = self._check(b"xaaabbbc")
        self.assertLessEqual(len(blob), 10,
                             "8-byte input must not expand past 10 bytes")

    def test_multiple_separated_runs(self):
        data = b"abc" + b"d" * 5 + b"efg" + b"h" * 7 + b"ijk" + b"l" * 3
        self._check(data)

    def test_adjacent_runs(self):
        data = b"a" * 4 + b"b" * 5 + b"c" * 6
        self._check(data)

    def test_no_runs(self):
        data = bytes(range(256)) * 2
        self._check(data)

    def test_one_run(self):
        data = b"prefix" + b"z" * 50 + b"suffix"
        self._check(data)

    def test_all_identical_bytes(self):
        data = b"\x00" * 100000
        blob = self._check(data)
        self.assertLess(len(blob), 1600)

    def test_long_literals(self):
        rng = random.Random(1234)
        data = bytes(rng.randrange(256) for _ in range(500))
        self._check(data)

    def test_128_byte_literal_boundary(self):
        data = b"x" * 128 + b"a" * 5
        self._check(data)
        data = b"x" * 127 + b"a" * 5
        self._check(data)

    def test_129_byte_literal_boundary(self):
        data = b"x" * 129 + b"a" * 5
        self._check(data)
        data = b"x" * 130 + b"a" * 5
        self._check(data)

    def test_many_runs(self):
        rng = random.Random(99)
        parts = []
        for _ in range(300):
            parts.append(bytes((rng.randrange(256),)) *
                         rng.randrange(1, 40))
            parts.append(bytes((rng.randrange(256),)) *
                         rng.randrange(1, 4))
        self._check(b"".join(parts))

    def test_random_data_fuzz(self):
        rng = random.Random(20260822)
        for trial in range(200):
            size = rng.randrange(0, 600)
            style = trial % 4
            if style == 0:
                data = bytes(rng.randrange(256) for _ in range(size))
            elif style == 1:
                data = bytes(rng.choice(b"ab") for _ in range(size))
            elif style == 2:
                data = (bytes((rng.randrange(256),)) * rng.randrange(1, 20)
                        )[:size]
            else:
                data = bytearray()
                while len(data) < size:
                    if rng.random() < 0.5:
                        data += bytes((rng.randrange(256),)) * \
                            rng.randrange(1, 200)
                    else:
                        data.append(rng.randrange(256))
                data = bytes(data[:size])
            self._check(data)

    def test_full_container_roundtrip_on_run_heavy_data(self):
        data = b"ab" + b"c" * 9 + b"de" + b"f" * 200 + b"g" + \
            os.urandom(64)
        blob = compress(data)
        self.assertEqual(decompress(blob), data)


if __name__ == "__main__":
    unittest.main()
