"""M1 benchmark: V2 tokenizer vs V1 _lz_encode_core.

Reports wall time, throughput, and a format-neutral cost proxy for the
V2 tokens (literal = 1 B, match = 3 B, rep = 2 B, + flag bits) against
the actual byte size of the V1 LZ stream.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode.patterns import _lz_encode_core
from divideencode.v2.lz import tokenize, token_stats

FILES = [
    "App.java", "main.py", "program.c", "app.js", "lib.rs",
    "page.html", "feed.xml", "data.json", "big.json", "dump.sql",
    "server.log", "table.csv", "text_en.txt", "notes.md", "doc.pdf",
    "sensor_i16.bin", "counters_u32.bin", "random_prng.bin",
]

REPS = 3


def best_time(fn, *a):
    t0 = time.perf_counter()
    out = fn(*a)
    return out, time.perf_counter() - t0


def bench(path):
    with open(path, "rb") as fh:
        data = fh.read()
    n = len(data)

    v1_blob = None
    v1_s = 9e9
    for _ in range(REPS):
        blob, dt = best_time(_lz_encode_core, data)
        if dt < v1_s:
            v1_s, v1_blob = dt, blob

    v2_tokens = None
    v2_s = 9e9
    for _ in range(REPS):
        toks, dt = best_time(tokenize, data)
        if dt < v2_s:
            v2_s, v2_tokens = dt, toks

    st = token_stats(v2_tokens)
    cover = 100.0 * st["match_covered"] / n
    print("%-16s %8dB | v1 %7.1fms %6.2fMB/s %8dB | "
          "v2 %7.1fms %6.2fMB/s ~%8dB (%.3fx) | cov %.1f%% rep %d"
          % (os.path.basename(path), n,
             v1_s * 1e3, n / v1_s / 1048576.0, len(v1_blob),
             v2_s * 1e3, n / v2_s / 1048576.0, st["cost_estimate"],
             len(v1_blob) / max(1, st["cost_estimate"]),
             cover, st["reps"]))


def main():
    samples = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "samples")
    print("V2 tokenizer (M1) vs V1 LZ core -- best of %d runs" % REPS)
    for name in FILES:
        path = os.path.join(samples, name)
        if os.path.isfile(path):
            bench(path)


if __name__ == "__main__":
    main()
