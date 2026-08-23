"""M2 benchmark: DE2 codec vs V1 engine and reference compressors."""
import gzip
import os
import sys
import time
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress as v1c, decompress as v1d
from divideencode.v2 import compress as v2c, decompress as v2d

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

try:
    import zstandard
    _ZC = zstandard.ZstdCompressor(level=19)
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

FILES = [
    "App.java", "main.py", "program.c", "engine.cpp", "app.js", "app.ts",
    "lib.rs", "page.html", "feed.xml", "data.json", "big.json",
    "dump.sql", "server.log", "table.csv", "text_en.txt", "notes.md",
    "doc.pdf", "photo.jpg", "photo.png", "photo.webp", "archive.zip",
    "random_os.bin", "random_prng.bin", "sensor_i16.bin",
    "counters_u32.bin",
]


def t(fn, *a):
    t0 = time.perf_counter()
    out = fn(*a)
    return out, time.perf_counter() - t0


def main():
    samples = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "samples")
    hdr = "%-16s %8s | %7s %8s | %7s %8s %6s | %7s" % (
        "file", "orig", "DE1size", "DE1time", "DE2size", "DE2time",
        "vsDE1", "zlib9")
    print(hdr)
    print("-" * len(hdr) + (
        " %7s" % ("zstd19") if HAS_ZSTD else "") +
        (" %7s" % ("brotli") if HAS_BROTLI else ""))
    for name in FILES:
        path = os.path.join(samples, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        n = len(data)

        b1, t1 = t(v1c, data)
        assert v1d(b1) == data
        b2, t2 = t(v2c, data)
        assert v2d(b2) == data
        bz = zlib.compress(data, 9)

        line = "%-16s %8d | %7d %7.2fs | %7d %8.3fs %5.1f%% | %7d" % (
            name, n, len(b1), t1, len(b2), t2,
            100.0 * (len(b1) - len(b2)) / len(b1), len(bz))
        if HAS_ZSTD:
            line += " %7d" % len(_ZC.compress(data))
        if HAS_BROTLI:
            line += " %7d" % len(brotli.compress(data, quality=11))
        print(line, flush=True)


if __name__ == "__main__":
    main()
