import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from v2 import tokenize, detokenize, analyze, Config, LZ2Error


def rt(data, cfg=None):
    blob, st = tokenize(data, cfg)
    out = detokenize(blob)
    assert out == data, "round-trip mismatch (%d bytes in)" % len(data)
    return blob, st


class TestTinyInputs(unittest.TestCase):
    def test_empty(self):
        blob, st = rt(b"")
        self.assertEqual(st.token_count, 0)
        self.assertEqual(detokenize(blob), b"")

    def test_sizes_1_to_5(self):
        rng = 12345
        for size in range(1, 6):
            buf = bytearray()
            state = rng
            while len(buf) < size:
                state = (state * 1103515245 + 12345) & 0x7FFFFFFF
                buf.append(state & 0xFF)
            rt(bytes(buf))
        rt(b"\x00")
        rt(b"\xff\xff\xff\xff\xff")

    def test_min_match_boundary(self):
        rt(b"abcd")            # exactly MIN_MATCH, nothing to reference
        rt(b"abcdabcd")        # one exact match
        rt(b"abcdabc")         # 3-byte partial repeat -> literals


class TestDegenerate(unittest.TestCase):
    def test_all_zero(self):
        blob, st = rt(bytes(10000))
        self.assertLess(len(blob), 100)

    def test_all_ff(self):
        blob, _ = rt(b"\xff" * 10000)
        self.assertLess(len(blob), 100)

    def test_alternating(self):
        blob, _ = rt(b"\x00\xff" * 5000)
        self.assertLess(len(blob), 100)

    def test_highly_repetitive(self):
        rt(b"ABCABCABC" * 3000)

    def test_incompressible(self):
        import os
        data = os.urandom(4096)
        blob, st = rt(data)
        self.assertEqual(st.match_count + st.rep_count, 0)
        self.assertLessEqual(len(blob), 4096 + 4096 // 8 + 64)


class TestStructuredSamples(unittest.TestCase):
    def test_json_like(self):
        recs = []
        for i in range(2000):
            recs.append('{"id": %d, "name": "item_%d", "score": %.2f}'
                        % (i, i, (i * 13 % 997) / 7))
        blob, st = rt(("[" + ",\n".join(recs) + "]").encode())
        self.assertGreater(st.rep_count + st.match_count, 50)

    def test_utf8_text(self):
        text = ("Ünïcödé ✓ 日本語テキスト 🚀 emoji mix\n" * 200).encode("utf-8")
        rt(text)

    def test_source_like(self):
        js = ("export function handler_%d(value, options = null) {\n"
              "  const results = [];\n"
              "  results.push(transform(value, %d));\n"
              "}\n")
        src = "".join(js % (i, i * 7 % 999) for i in range(400)).encode()
        blob, st = rt(src)
        self.assertLess(len(blob), len(src))

    def test_csv_like(self):
        rows = ["id,name,amount"]
        for i in range(3000):
            rows.append("%d,user%d,%d.%02d" % (i, i % 97, i * 31 % 5000,
                                               i * 7 % 100))
        rt(("\n".join(rows)).encode())


class TestSampleCorpus(unittest.TestCase):
    """Round-trip every repo sample file that exists."""

    SAMPLES = [
        "notes.md", "text_en.txt", "page.html", "feed.xml", "app.js",
        "app.ts", "main.py", "program.c", "engine.cpp", "App.java",
        "lib.rs", "dump.sql", "data.json", "server.log", "table.csv",
        "doc.pdf", "archive.zip", "photo.jpg", "photo.webp",
        "sensor_i16.bin", "counters_u32.bin", "big.json",
    ]

    def test_samples_roundtrip(self):
        base = os.path.join(os.path.dirname(__file__), "..", "samples")
        tested = 0
        for name in self.SAMPLES:
            path = os.path.join(base, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            blob, st = rt(data)
            self.assertLessEqual(
                st.token_count,
                st.literal_count + st.match_count + st.rep_count + 1,
                "token accounting broken for %s" % name)
            tested += 1
        self.assertGreater(tested, 15, "expected most samples to exist")


class TestConfigs(unittest.TestCase):
    data = (b"the quick brown fox jumps over the lazy dog. " * 30 +
            b"\x00" * 500 + b"ABCDEFGH" * 120)

    def test_window_sweep(self):
        for w in (MINW := 8, 64, 256, 4096, 65536):
            rt(self.data, Config(window=w))

    def test_chain_sweep(self):
        for c in (1, 2, 4, 8, 16, 32, 64, 128):
            rt(self.data, Config(max_chain=c))

    def test_lazy_off_on(self):
        b0, s0 = rt(self.data, Config(lazy=0))
        b1, s1 = rt(self.data, Config(lazy=1))
        self.assertLessEqual(len(b1), len(b0),
                             "lazy must never be worse than greedy here")

    def test_nice_length_sweep(self):
        for nice in (4, 8, 32, 96, 100000):
            rt(self.data, Config(nice_length=nice))

    def test_deterministic_output(self):
        a = tokenize(self.data)[0]
        b = tokenize(self.data)[0]
        self.assertEqual(a, b)


class TestMalformedStreams(unittest.TestCase):
    good = tokenize(b"hello world hello world hello world " * 5)[0]

    def test_empty_stream_raises(self):
        with self.assertRaises(LZ2Error):
            detokenize(b"")

    def test_truncated_prefixes(self):
        for cut in range(1, len(self.good)):
            with self.assertRaises(LZ2Error):
                detokenize(self.good[:cut])

    def test_trailing_garbage(self):
        with self.assertRaises(LZ2Error):
            detokenize(self.good + b"\x00")

    def test_random_garbage_never_crashes(self):
        import os
        for seed in range(200):
            blob = os.urandom(48)
            try:
                detokenize(blob)
            except LZ2Error:
                pass

    def test_rep_id_overflow(self):
        # header varint(8), flags byte with bit0 set (match), then a rep token
        stream = bytes([8, 0x01]) + bytes([0, 0, 9])
        with self.assertRaises(LZ2Error):
            detokenize(stream)

    def test_distance_beyond_output(self):
        # literal 'A' then match claiming distance 5 (only 1 byte produced)
        stream = bytes([12, 0x01]) + bytes([0, 65]) + bytes([0, 5, 255])
        with self.assertRaises(LZ2Error):
            detokenize(stream)

    def test_overrun_declared_size(self):
        # declared 5 bytes, first token is a match of length 4 at dist 1,
        # second token another long match that would exceed 5
        stream = bytes([5, 0x03]) + bytes([0, 65]) + \
            bytes([0, 1]) + bytes([0, 100])
        with self.assertRaises(LZ2Error):
            detokenize(stream)


if __name__ == "__main__":
    unittest.main()
