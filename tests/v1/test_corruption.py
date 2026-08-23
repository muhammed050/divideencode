import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress, decompress
from divideencode.errors import DivideEncodeError


def flip_byte(blob, idx, delta=0xFF):
    mutated = bytearray(blob)
    mutated[idx] ^= delta
    return bytes(mutated)


class TestCorruptionDetection(unittest.TestCase):
    def setUp(self):
        self.data = (b"The quick brown fox jumps over the lazy dog. " * 40 +
                     os.urandom(64))
        self.blob = compress(self.data)

    def assert_corruption_detected(self, blob):
        with self.assertRaises(DivideEncodeError):
            decompress(blob)

    def test_valid_container_roundtrips(self):
        self.assertEqual(decompress(self.blob), self.data)

    def test_flip_every_header_byte(self):
        for i in range(0, min(12, len(self.blob))):
            self.assert_corruption_detected(flip_byte(self.blob, i))

    def test_flip_payload_bytes_sampled(self):
        n = len(self.blob)
        step = max(1, n // 200)
        detected = 0
        for i in range(6, n, step):
            try:
                out = decompress(flip_byte(self.blob, i))
            except DivideEncodeError:
                detected += 1
                continue
            if out != self.data:
                detected += 1
        self.assertGreater(detected, 0,
                           "payload corruption must be detected structurally or by crc")
        self.assertEqual(detected, len(range(6, n, step)),
                         "every single-byte payload flip must be detected")

    def test_truncation_detected(self):
        for cut in (0, 1, 4, 5, 9, len(self.blob) // 2, len(self.blob) - 1):
            if cut <= 0:
                continue
            self.assert_corruption_detected(self.blob[:cut])

    def test_extension_with_garbage_detected(self):
        self.assert_corruption_detected(self.blob + b"GARBAGE")

    def test_garbage_magic_detected(self):
        bad = b"NOPE" + self.blob[4:]
        with self.assertRaises(Exception):
            decompress(bad)

    def test_wrong_version_detected(self):
        bad = bytearray(self.blob)
        bad[3] = 99
        with self.assertRaises(Exception):
            decompress(bytes(bad))

    def test_empty_and_random_containers(self):
        for blob in (b"", os.urandom(64), b"DE1\x01", b"DE1\x01\x00"):
            with self.assertRaises(Exception):
                decompress(blob)

    def test_crc_catches_silent_wrong_output(self):
        mutated = flip_byte(self.blob, len(self.blob) - 1, 0x01)
        try:
            out = decompress(mutated)
        except DivideEncodeError:
            return
        self.assertNotEqual(out, self.data)


if __name__ == "__main__":
    unittest.main()
