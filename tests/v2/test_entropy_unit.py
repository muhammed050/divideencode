import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from v2.entropy import (
    compress, decompress, entropy_encode, entropy_decode,
    tokenize_tokens, Canon, EntropyError, BitWriter, BitReader,
    _LEN_BASES, _DIST_BASES,
)
from v2 import Config


def _realistic_json_records(n):
    """Varied JSON records: changing digits break giant matches, so the
    token stream contains many MATCH/REP tokens (like real-world JSON)."""
    recs = []
    for i in range(n):
        recs.append('{"id": %d, "name": "item_%d", "score": %.2f},\n'
                    % (i, i * 7 % 997, (i % 97) / 7))
    return ("[" + "\n".join(recs) + "]").encode()


def rt(data, cfg=None):
    blob = compress(data, cfg)
    out = decompress(blob)
    assert out == data, "entropy round-trip mismatch (%d B in)" % len(data)
    return blob


class TestEntropyRoundTrip(unittest.TestCase):
    def test_tiny_sizes(self):
        for data in (b"", b"a", b"ab", b"abc", b"abcd", b"abcde",
                     b"\x00", b"\xff\xff\xff\xff\xff"):
            rt(data)

    def test_all_zero(self):
        blob = rt(bytes(20000))
        self.assertLess(len(blob), 60)

    def test_repetitive(self):
        blob = rt(b"ABCABCABC" * 2000)
        self.assertLess(len(blob), 120)

    def test_random_stays_bounded(self):
        import os
        data = os.urandom(4096)
        blob = rt(data)
        # literals stream + tables + headers; must stay close to input size
        self.assertLessEqual(len(blob), 4096 + 700)

    def test_long_match_split(self):
        # single run -> matches longer than MAX_CHUNK must be split
        blob = rt(b"z" * 5000)
        self.assertLess(len(blob), 80)
        rt(b"abcdefghij" * 1200)

    def test_rep_offsets_survive_entropy_layer(self):
        # varied records -> distances repeat across records -> many
        # MATCH/REP tokens; entropy coding must beat the raw stream here
        data = _realistic_json_records(500)
        blob = rt(data)
        tokens, _ = tokenize_tokens(data)
        n_matches = sum(1 for t in tokens if t[0] != "L")
        self.assertGreater(n_matches, 50,
                           "expected a multi-match token stream")
        from v2.lz2 import serialize_raw
        raw = serialize_raw(tokens, len(data))
        self.assertLess(len(blob), len(raw))

    def test_corpus_files(self):
        base = os.path.join(os.path.dirname(__file__), "..", "samples")
        names = ["notes.md", "text_en.txt", "page.html", "feed.xml",
                 "app.js", "App.java", "main.py", "lib.rs", "program.c",
                 "engine.cpp", "app.ts", "dump.sql", "server.log",
                 "table.csv", "data.json", "doc.pdf", "archive.zip",
                 "photo.jpg", "photo.webp", "sensor_i16.bin",
                 "counters_u32.bin", "big.json"]
        for name in names:
            path = os.path.join(base, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            blob = rt(data)

    def test_configs(self):
        data = b"the quick brown fox jumps over the lazy dog. " * 50
        for window in (8, 256, 4096, 65536):
            for chain in (1, 8, 64):
                for lazy in (0, 1):
                    rt(data, Config(window=window, max_chain=chain,
                                    lazy=lazy))


class TestMetadataAccounting(unittest.TestCase):
    def test_tables_included_and_nonzero(self):
        data = _realistic_json_records(400)
        blob = compress(data)
        tokens, _ = tokenize_tokens(data)
        self.assertGreater(sum(1 for t in tokens if t[0] != "L"), 50)
        from v2.lz2 import serialize_raw
        raw = serialize_raw(tokens, len(data))
        self.assertLess(len(blob), len(raw))

    def test_deterministic(self):
        data = b"payload payload payload " * 100
        self.assertEqual(compress(data), compress(data))


class TestMalformedEntropyStreams(unittest.TestCase):
    good = compress(b"structured structured structured " * 20)

    def test_empty_stream_raises(self):
        with self.assertRaises(EntropyError):
            decompress(b"")
        with self.assertRaises(EntropyError):
            decompress(b"\x05")

    def test_truncated_prefixes(self):
        for cut in range(1, min(60, len(self.good))):
            with self.assertRaises(Exception):
                decompress(self.good[:cut])

    def test_trailing_garbage(self):
        with self.assertRaises(EntropyError):
            decompress(self.good + b"\x00")

    def test_reserved_presence_bits_rejected(self):
        bad = bytearray(self.good)
        # find presence byte: after two varints of small values
        bad[2] |= 0xF0
        with self.assertRaises(EntropyError):
            decompress(bytes(bad))

    def test_garbage_blobs_raise_entropy_error_or_lz2_error(self):
        import os
        from v2 import LZ2Error
        raised = 0
        for seed in range(300):
            blob = os.urandom(64)
            try:
                decompress(blob)
            except (EntropyError, LZ2Error, ValueError):
                raised += 1
        self.assertEqual(raised, 300)


class TestCanonInternals(unittest.TestCase):
    def test_canon_roundtrip_serialization(self):
        codelens = {65: 1, 66: 2, 67: 3, 68: 3}
        c = Canon(codelens)
        blob = c.serialize()
        pos_c = len(blob)
        parsed, consumed = Canon.parse(blob + b"PADDING", 0, len(blob))
        self.assertEqual(consumed, len(blob))
        self.assertEqual(parsed.pairs, sorted(c.pairs))

    def test_bitwriter_reader_roundtrip(self):
        bw = BitWriter()
        values = [(1, 1), (2, 3), (7, 3), (255, 8), (5, 4), (0, 5)]
        for v, n in values:
            bw.write(v, n)
        blob = bw.getvalue()
        br = BitReader(blob, 0, len(blob))
        for v, n in values:
            self.assertEqual(br.read(n), v)
        capacity = len(blob) * 8
        leftover = capacity - sum(n for _, n in values)
        self.assertLess(leftover, 8)

    def test_class_table_monotone(self):
        self.assertEqual(_LEN_BASES[0], 4)
        self.assertEqual(_DIST_BASES[0], 1)
        for bases, extras in ((_LEN_BASES, None), (_DIST_BASES, None)):
            prev_base = bases[0]
            prev_bits = 0
            for b in bases[1:]:
                self.assertGreater(b, prev_base)
                prev_base = b


if __name__ == "__main__":
    unittest.main()
