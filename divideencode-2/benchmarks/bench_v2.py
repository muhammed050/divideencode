"""DE2 V2 benchmark suite (dd.txt section 14).

Measures compression ratio, compression/decompression wall+CPU time and
MB/s, peak memory (tracemalloc, outside the timed region), and the codec
selected per block. Compares DE2 against V1, gzip, raw deflate, xz,
zstd and brotli over the project corpus.

Usage: python benchmarks/bench_v2.py [--quick] [--results-dir DIR]
"""
import argparse
import csv
import os
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import de2
from divideencode import compress as v1_compress, decompress as v1_decompress

import zlib
import gzip
import lzma

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False

try:
    import zstandard
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False


def _timed(fn, *a):
    cpu0 = time.process_time()
    t0 = time.perf_counter()
    out = fn(*a)
    return out, time.perf_counter() - t0, time.process_time() - cpu0


def bench_de2(data):
    blob, ct, ccpu = _timed(de2.compress, data)
    out, dt, dcpu = _timed(de2.decompress, blob)
    if out != data:
        raise AssertionError("roundtrip failure")
    probe = data[:131072]
    tracemalloc.start()
    de2.compress(probe)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "size": len(blob), "ct": ct, "dt": dt,
        "ccpu": ccpu, "dcpu": dcpu, "mem": peak,
        "modes": ",".join(sorted(set(de2.block_modes(blob)))),
    }


def bench_v1(data):
    blob, ct, _ = _timed(v1_compress, data)
    out, dt, _ = _timed(v1_decompress, blob)
    assert out == data
    return {"size": len(blob), "ct": ct, "dt": dt}


def bench_deflate(data):
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    blob, ct, _ = _timed(lambda: comp.compress(data) + comp.flush())
    dec = zlib.decompressobj(-15)
    out, dt, _ = _timed(lambda: dec.decompress(blob) + dec.flush())
    assert out == data
    return {"size": len(blob), "ct": ct, "dt": dt}


def bench_gzip(data):
    blob, ct, _ = _timed(gzip.compress, data, 9)
    out, dt, _ = _timed(gzip.decompress, blob)
    assert out == data
    return {"size": len(blob), "ct": ct, "dt": dt}


def bench_brotli(data):
    blob, ct, _ = _timed(lambda: brotli.compress(data, quality=11))
    out, dt, _ = _timed(brotli.decompress, blob)
    assert out == data
    return {"size": len(blob), "ct": ct, "dt": dt}


_ZC = None


def bench_zstd(data):
    global _ZC, _ZD
    if _ZC is None:
        _ZC = zstandard.ZstdCompressor(level=19)
        _ZD = zstandard.ZstdDecompressor()
    blob, ct, _ = _timed(_ZC.compress, data)
    out, dt, _ = _timed(_ZD.decompress, blob)
    assert out == data
    return {"size": len(blob), "ct": ct, "dt": dt}


def bench_xz(data):
    filt = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}]
    blob, ct, _ = _timed(lambda: lzma.compress(data, format=lzma.FORMAT_XZ,
                                               filters=filt))
    out, dt, _ = _timed(lzma.decompress, blob)
    assert out == data
    return {"size": len(blob), "ct": ct, "dt": dt}


def fmt(t):
    return "%.3fs" % t if t >= 0.5 else "%.1fms" % (t * 1000)


def mbs(n, t):
    return n / 1048576.0 / t if t > 0 else float("inf")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--results-dir", default=None)
    args = ap.parse_args()
    root = os.path.dirname(os.path.abspath(__file__))
    samples_dir = os.path.join(root, "..", "samples")
    results_dir = args.results_dir or os.path.join(root, "results")
    os.makedirs(results_dir, exist_ok=True)

    algos = [("V1", bench_v1), ("deflate", bench_deflate),
             ("gzip", bench_gzip)]
    if HAS_BROTLI:
        algos.append(("brotli", bench_brotli))
    else:
        print("NOTE: brotli not available -> skipped")
    if HAS_ZSTD:
        algos.append(("zstd", bench_zstd))
    else:
        print("NOTE: zstandard not available -> skipped")
    algos.append(("xz", bench_xz))

    names = sorted(os.listdir(samples_dir))
    if args.quick:
        names = [n for n in names if not n.startswith("big.")
                 and not n.startswith("photo.png")]

    rows = []
    for name in names:
        path = os.path.join(samples_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        orig = len(data)
        row = {"file": name, "orig": orig}
        res = bench_de2(data)
        row.update({
            "de2_size": res["size"], "de2_ct": res["ct"], "de2_dt": res["dt"],
            "de2_ccpu": res["ccpu"], "de2_dcpu": res["dcpu"],
            "de2_mem": res["mem"], "de2_modes": res["modes"],
        })
        for aname, fn in algos:
            try:
                r = fn(data)
                row[aname + "_size"] = r["size"]
                row[aname + "_ct"] = r["ct"]
                row[aname + "_dt"] = r["dt"]
            except Exception as exc:
                row[aname + "_size"] = None
                print("   %s failed: %s" % (aname, exc))
        rows.append(row)
        best = min((row[a + "_size"] for a, _ in algos
                    if row.get(a + "_size")), default=0)
        print("%-18s %8d B  DE2 %8d (%.4f, c=%s d=%s, %.2f/%.2f MB/s) "
              "best-other %8d  modes=%s" % (
                  name, orig, res["size"], res["size"] / max(1, orig),
                  fmt(res["ct"]), fmt(res["dt"]),
                  mbs(orig, res["ct"]), mbs(orig, res["dt"]),
                  best, res["modes"]))

    # ---- persist ----
    csv_path = os.path.join(results_dir, "v2_results.csv")
    cols = ["file", "orig", "de2_size", "de2_ct", "de2_dt", "de2_ccpu",
            "de2_dcpu", "de2_mem", "de2_modes"]
    for a, _ in algos:
        cols += [a + "_size", a + "_ct", a + "_dt"]
    with open(csv_path, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=cols)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k) for k in cols})

    md = ["# DE2 V2 Benchmark Results", "",
          "- Run: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
          "- DE2: balanced profile, block=256KiB; V1: beam HEAD; "
          "deflate lvl9 raw; gzip lvl9; brotli q11; zstd lvl19; xz 9e", ""]
    md.append("| File | Orig | DE2 | ratio | V1 | deflate | gzip | brotli |"
              " zstd | xz | DE2 MB/s (c/d) | modes |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        o = r["orig"]

        def sz(a):
            v = r.get(a + "_size")
            return str(v) if v else "-"
        md.append("| %s | %d | %d | %.4f | %s | %s | %s | %s | %s | %s | "
                  "%.2f / %.2f | %s |" % (
                      r["file"], o, r["de2_size"],
                      r["de2_size"] / max(1, o),
                      sz("V1"), sz("deflate"), sz("gzip"), sz("brotli"),
                      sz("zstd"), sz("xz"),
                      mbs(o, r["de2_ct"]), mbs(o, r["de2_dt"]),
                      r["de2_modes"]))
    md_path = os.path.join(results_dir, "v2_RESULTS.md")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md) + "\n")
    print("\nwrote %s\nwrote %s" % (csv_path, md_path))


if __name__ == "__main__":
    main()
