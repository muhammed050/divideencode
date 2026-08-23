"""Freeze the M4 baseline: sizes, ratios, timings, CPU, memory, mode."""
import os
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress as v1c
from divideencode.v2 import compress as v2c, decompress as v2d, parse_header

FILES = [
    "App.java", "main.py", "program.c", "engine.cpp", "app.js", "app.ts",
    "lib.rs", "page.html", "feed.xml", "data.json", "big.json",
    "dump.sql", "server.log", "table.csv", "text_en.txt", "notes.md",
    "doc.pdf", "photo.jpg", "photo.png", "photo.webp", "archive.zip",
    "random_os.bin", "random_prng.bin", "sensor_i16.bin",
    "counters_u32.bin",
]


def payload_method(blob):
    _orig, _crc, pos = parse_header(blob)
    return blob[pos]


def main():
    samples = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "samples")
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "results", "m4_baseline.txt")
    rows = []
    for name in FILES:
        path = os.path.join(samples, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        n = len(data)

        c0, t0 = time.perf_counter(), time.process_time()
        blob = v2c(data)
        enc_wall, enc_cpu = time.perf_counter() - c0, time.process_time() - t0

        d0, t0 = time.perf_counter(), time.process_time()
        out = v2d(blob)
        dec_wall, dec_cpu = time.perf_counter() - d0, time.process_time() - t0
        assert out == data, name

        probe = data[:131072]
        tracemalloc.start()
        v2c(probe)
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        rows.append((name, n, len(blob), len(blob) / n,
                     enc_wall, dec_wall, enc_cpu, dec_cpu, peak,
                     payload_method(blob)))
        print("%-16s %8d -> %8d  ratio %.4f  enc %.3fs (cpu %.3f)  "
              "dec %.3fs  mem %dKB  mode=%d"
              % (name, n, len(blob), len(blob) / n, enc_wall, enc_cpu,
                 dec_wall, peak // 1024, payload_method(blob)),
              flush=True)

    with open(out_path, "w") as fh:
        fh.write("# M4 baseline (Branch 3) -- immutable reference\n")
        fh.write("# file orig compressed ratio enc_s dec_s enc_cpu_s "
                 "dec_cpu_s mem_peak_bytes mode\n")
        for r in rows:
            fh.write("%s %d %d %.5f %.4f %.4f %.4f %.4f %d %d\n" % r)
    print("baseline written:", out_path)


if __name__ == "__main__":
    main()
