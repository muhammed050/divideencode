import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import de2
from divideencode.errors import CorruptedError, DivideEncodeError


def roundtrip(data, **kw):
    blob = de2.compress(data, **kw)
    out = de2.decompress(blob)
    assert out == data, "roundtrip mismatch (n=%d)" % len(data)
    return blob


class TestDe2RoundTrip(unittest.TestCase):
    def test_empty(self):
        blob = roundtrip(b"")
        self.assertEqual(len(blob), 11)  # header only: no blocks

    def test_tiny_inputs(self):
        for d in (b"a", b"ab", b"aa", b"\x00", b"\xff" * 3):
            roundtrip(d)

    def test_repetitive(self):
        roundtrip(b"abc" * 10000)
        roundtrip(bytes(100000))
        roundtrip(b"A" * 70000 + b"B" * 300)

    def test_text(self):
        roundtrip(b"the quick brown fox jumps over the lazy dog. " * 2000)
        roundtrip("Ünïcödé ✓ 日本語 🚀 mix\n".encode() * 1500)

    def test_random_stays_small(self):
        rng = os.urandom(50000)
        blob = de2.compress(rng)
        self.assertLessEqual(len(blob), len(rng) + 64,
                             "random data must not inflate")
        self.assertEqual(de2.decompress(blob), rng)

    def test_binary_all_values(self):
        roundtrip(bytes(range(256)) * 128)

    def test_incrementing_u32_counters(self):
        import struct
        vals = [i for i in range(20000)]
        roundtrip(struct.pack("<%dI" % len(vals), *vals))

    def test_multi_block_boundaries(self):
        data = os.urandom(1000) + b"PATTERN" * 400 + os.urandom(1000)
        blob = de2.compress(data, block_size=1024)
        self.assertEqual(de2.decompress(blob), data)
        # exact multiples of the block size
        data2 = b"z" * 4096
        self.assertEqual(
            de2.decompress(de2.compress(data2, block_size=1024)), data2)
        # one byte over a boundary
        data3 = b"q" * 4097
        self.assertEqual(
            de2.decompress(de2.compress(data3, block_size=1024)), data3)

    def test_block_size_configurable(self):
        data = bytes(os.urandom(512)) * 8
        for bs in (64, 256, 1024, 65536, 262144):
            self.assertEqual(de2.decompress(de2.compress(data, block_size=bs)),
                             data)


class TestDe2Modes(unittest.TestCase):
    def _mode_of(self, data):
        from divideencode.de2.container import parse_header, iter_blocks
        blob = de2.compress(data)
        _t, count, pos = parse_header(blob)
        blocks = list(iter_blocks(blob, pos, len(blob), count,
                                  len(data)))
        return blocks[0][0].mode

    def test_incompressible_uses_raw(self):
        from divideencode.de2.classifier import MODE_RAW
        self.assertEqual(self._mode_of(os.urandom(4096)), MODE_RAW)

    def test_run_heavy_uses_rle_or_better(self):
        from divideencode.de2.classifier import MODE_RAW, MODE_RLE
        mode = self._mode_of(b"\x00" * 50000)
        self.assertIn(mode, (MODE_RAW, MODE_RLE))

    def test_text_uses_lz(self):
        from divideencode.de2.classifier import MODE_LZ
        mode = self._mode_of(b"lorem ipsum dolor sit amet. " * 800)
        self.assertEqual(mode, MODE_LZ)

    def test_counters_use_delta_lz(self):
        import struct
        from divideencode.de2.classifier import MODE_DELTA_LZ
        vals = list(range(5000))
        data = struct.pack("<%dI" % len(vals), *vals)
        self.assertEqual(self._mode_of(data), MODE_DELTA_LZ)

    def test_delta_zigzag_alternating(self):
        import struct
        from divideencode.de2.transforms import delta_encode, delta_decode
        vals = []
        x = 0
        for i in range(4000):
            x += 1 if i % 2 == 0 else -1
            vals.append(x & 0xFFFF)
        data = struct.pack("<%dH" % len(vals), *vals)
        payload, tmeta = delta_encode(data, w=2, zigzag=True)
        self.assertEqual(delta_decode(payload, tmeta), data)


class TestDe2Corruption(unittest.TestCase):
    def setUp(self):
        self.data = b"The quick brown fox! " * 300 + os.urandom(32)
        self.blob = de2.compress(self.data)

    def _reject(self, mutated):
        with self.assertRaises(Exception):
            de2.decompress(mutated)

    def test_flip_header(self):
        for i in range(0, min(10, len(self.blob))):
            m = bytearray(self.blob)
            m[i] ^= 0xFF
            self._reject(bytes(m))

    def test_flip_payload_sampled(self):
        n = len(self.blob)
        step = max(1, n // 120)
        detected = 0
        total = 0
        for i in range(11, n, step):
            total += 1
            m = bytearray(self.blob)
            m[i] ^= 0xA5
            try:
                de2.decompress(bytes(m))
            except Exception:
                detected += 1
        self.assertEqual(detected, total,
                         "every single-byte flip must be caught")

    def test_truncation(self):
        for cut in (1, 5, 9, 10, 11, len(self.blob) // 2, len(self.blob) - 1):
            self._reject(self.blob[:cut])

    def test_garbage_suffix(self):
        self._reject(self.blob + b"GARBAGE")

    def test_bad_magic_version_flags(self):
        for pos, val in ((0, ord("X")), (3, 9), (4, 0x01)):
            m = bytearray(self.blob)
            if pos == 0:
                m[0:3] = b"XXX"
            else:
                m[pos] = val
            self._reject(bytes(m))

    def test_fuzz_decompress_never_crashes(self):
        rng = random.Random(20260822)
        for trial in range(150):
            style = trial % 4
            if style == 0:
                junk = bytes(rng.randrange(256)
                             for _ in range(rng.randrange(0, 200)))
            elif style == 1 and len(self.blob) > 20:
                buf = bytearray(self.blob)
                for _ in range(rng.randrange(1, 6)):
                    buf[rng.randrange(len(buf))] ^= rng.randrange(1, 256)
                junk = bytes(buf)
            elif style == 2:
                junk = self.blob[:rng.randrange(0, len(self.blob))]
            else:
                junk = self.blob + bytes(
                    rng.randrange(256) for _ in range(rng.randrange(0, 16)))
            try:
                de2.decompress(junk)
            except DivideEncodeError:
                pass
            except AssertionError:
                raise
            except RecursionError:
                raise


if __name__ == "__main__":
    unittest.main()
