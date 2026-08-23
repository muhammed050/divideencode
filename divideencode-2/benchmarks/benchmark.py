import csv
import hashlib
import os
import platform
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress as de_compress, decompress as de_decompress
from divideencode import recursive_compress

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


def _timed(fn, *a, **kw):
    t0 = time.perf_counter()
    result = fn(*a, **kw)
    return result, time.perf_counter() - t0


def bench_divideencode(data):
    blob, ct = _timed(de_compress, data)
    out, dt = _timed(de_decompress, blob)
    assert out == data
    probe = data[:131072]
    tracemalloc.start()
    de_compress(probe)
    _, peak_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "size": len(blob), "ctime": ct, "dtime": dt,
        "mem": peak_peak,
    }


def bench_deflate(data):
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    t0 = time.perf_counter()
    blob = comp.compress(data) + comp.flush()
    ct = time.perf_counter() - t0
    t0 = time.perf_counter()
    dec = zlib.decompressobj(-15)
    out = dec.decompress(blob) + dec.flush()
    dt = time.perf_counter() - t0
    assert out == data
    return {"size": len(blob), "ctime": ct, "dtime": dt}


def bench_gzip(data):
    t0 = time.perf_counter()
    blob = gzip.compress(data, compresslevel=9)
    ct = time.perf_counter() - t0
    t0 = time.perf_counter()
    out = gzip.decompress(blob)
    dt = time.perf_counter() - t0
    assert out == data
    return {"size": len(blob), "ctime": ct, "dtime": dt}


def bench_brotli(data):
    t0 = time.perf_counter()
    blob = brotli.compress(data, quality=11)
    ct = time.perf_counter() - t0
    t0 = time.perf_counter()
    out = brotli.decompress(blob)
    dt = time.perf_counter() - t0
    assert out == data
    return {"size": len(blob), "ctime": ct, "dtime": dt}


_ZSTD_C = None


def bench_zstd(data):
    global _ZSTD_C
    if _ZSTD_C is None:
        _ZSTD_C = zstandard.ZstdCompressor(level=19)
        _ZSTD_D = zstandard.ZstdDecompressor()

        def c(d):
            return _ZSTD_C.compress(d)

        def dcb(b):
            return _ZSTD_D.decompress(b)
        bench_zstd._c = c
        bench_zstd._d = dcb
    t0 = time.perf_counter()
    blob = bench_zstd._c(data)
    ct = time.perf_counter() - t0
    t0 = time.perf_counter()
    out = bench_zstd._d(blob)
    dt = time.perf_counter() - t0
    assert out == data
    return {"size": len(blob), "ctime": ct, "dtime": dt}


def bench_lzma_xz(data):
    filt = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}]
    t0 = time.perf_counter()
    blob = lzma.compress(data, format=lzma.FORMAT_XZ, filters=filt)
    ct = time.perf_counter() - t0
    t0 = time.perf_counter()
    out = lzma.decompress(blob)
    dt = time.perf_counter() - t0
    assert out == data
    return {"size": len(blob), "ctime": ct, "dtime": dt}


def available_algorithms():
    algos = [("DE", bench_divideencode),
             ("ZIP(deflate)", bench_deflate),
             ("GZIP", bench_gzip)]
    if HAS_BROTLI:
        algos.append(("Brotli", bench_brotli))
    else:
        print("NOTE: brotli module not available -> NOT TESTED")
    if HAS_ZSTD:
        algos.append(("Zstd", bench_zstd))
    else:
        print("NOTE: zstandard module not available -> NOT TESTED")
    algos.append(("LZMA2(xz)", bench_lzma_xz))
    return algos


def fmt_time(t):
    if t >= 1.0:
        return "%.2fs" % t
    return "%.1fms" % (t * 1000.0)


def fmt_mem(m):
    return "%.1f MB" % (m / 1048576.0) if m >= 1048576 else "%.0f KB" % (m / 1024.0)


def run_benchmark(samples_dir, results_dir=None, quick=False):
    samples_dir = os.path.abspath(samples_dir)
    if results_dir is None:
        results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "results")
    os.makedirs(results_dir, exist_ok=True)

    names = sorted(os.listdir(samples_dir))
    if quick:
        names = [n for n in names if not n.startswith("big.")][:8]

    algos = available_algorithms()
    algo_names = [name for name, _ in algos]

    rows = []
    recursive_rows = []
    for name in names:
        path = os.path.join(samples_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        orig = len(data)
        sha = hashlib.sha256(data).hexdigest()
        row = {"file": name, "original": orig}
        print("%-20s (%8d B)" % (name, orig))
        for aname, fn in algos:
            try:
                res = fn(data)
                ratio = res["size"] / orig if orig else 1.0
                row[aname] = res["size"]
                row[aname + "_ct"] = res["ctime"]
                row[aname + "_dt"] = res["dtime"]
                if aname == "DE":
                    row["DE_mem"] = res["mem"]
                print("   %-14s %9d B  ratio %.4f  saved %6.2f%%  "
                      "c:%s d:%s" %
                      (aname, res["size"], ratio, 100 * (1 - ratio),
                       fmt_time(res["ctime"]), fmt_time(res["dtime"])))
            except Exception as exc:
                row[aname] = None
                print("   %-14s FAILED: %s" % (aname, exc))
        final, sizes = recursive_compress(data, max_passes=8)
        rec_best = min(sizes)
        passes = len(sizes) - 1
        recursive_rows.append((name, orig, sizes[-1], passes))
        print("   %-14s %9d B  (passes=%d)" % ("DE+recursive", len(final), passes))
        rows.append(row)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    env = "%s / Python %s / %s" % (platform.system(), platform.python_version(),
                                   platform.machine())

    csv_path = os.path.join(results_dir, "benchmark_results.csv")
    with open(csv_path, "w", newline="") as fh:
        wr = csv.writer(fh)
        header = ["file", "original"] + algo_names + \
                 ["DE_recursive_best", "DE_recursive_passes",
                  "DE_comp_time_s", "DE_decomp_time_s", "DE_peak_mem_bytes"]
        wr.writerow(header)
        for r in rows:
            line = [r["file"], r["original"]]
            for an in algo_names:
                line.append(r.get(an))
            rb = dict((n, (sz, p)) for n, o, sz, p in recursive_rows)
            sz, p = rb[r["file"]]
            line += [sz, p, r.get("DE_ct"), r.get("DE_dt"), r.get("DE_mem")]
            wr.writerow(line)

    md = []
    md.append("# DivideEncode Benchmark Results")
    md.append("")
    md.append("- Run: %s" % stamp)
    md.append("- Environment: %s" % env)
    md.append("- Settings: DivideEncode depth=3; ZIP=raw deflate lvl9; "
              "GZIP lvl9; Brotli q11; Zstd lvl19; LZMA2 xz preset 9e")
    md.append("- NOTE: 'LZMA2(xz)' uses liblzma (the same algorithm family as "
              "7-Zip); the 7z.exe binary itself was NOT TESTED.")
    md.append("- All tools verified lossless round-trip on every file before timing.")
    md.append("- Memory column is Python-side peak allocation measured with "
              "tracemalloc while compressing the first 128 KiB of each file "
              "(full-size tracing distorts timing and was therefore kept out "
              "of the timed path).")
    md.append("")
    header = ["File", "Original"] + ["%s" % a for a in algo_names]
    md.append("| " + " | ".join(header) + " |")
    md.append("|" + "|".join(["----"] * (len(algo_names) + 2)) + "|")

    def cell(v, orig):
        if v is None:
            return "NOT TESTED"
        return "%d (%.3f)" % (v, v / orig if orig else 1.0)

    for r in rows:
        cells = [r["file"], str(r["original"])]
        for an in algo_names:
            cells.append(cell(r.get(an), r["original"]))
        md.append("| " + " | ".join(cells) + " |")

    md.append("")
    md.append("## Compression / Decompression Speed")
    md.append("")
    md.append("| File | DE comp | DE decomp | ZIP comp | Brotli comp | Zstd comp | LZMA2 comp |")
    md.append("|------|---------|-----------|----------|-------------|-----------|------------|")
    for r in rows:
        md.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["file"],
            fmt_time(r.get("DE_ct", 0)), fmt_time(r.get("DE_dt", 0)),
            fmt_time(r.get("ZIP(deflate)_ct", 0)),
            fmt_time(r.get("Brotli_ct", 0)) if HAS_BROTLI else "-",
            fmt_time(r.get("Zstd_ct", 0)) if HAS_ZSTD else "-",
            fmt_time(r.get("LZMA2(xz)_ct", 0))))

    md.append("")
    md.append("## DivideEncode memory usage (tracemalloc peak)")
    md.append("")
    md.append("| File | Peak memory |")
    md.append("|------|-------------|")
    for r in rows:
        mem = r.get("DE_mem") or 0
        md.append("| %s | %s |" % (r["file"], fmt_mem(mem)))

    md_path = os.path.join(results_dir, "RESULTS.md")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md) + "\n")
    print("\nwrote %s" % csv_path)
    print("wrote %s" % md_path)
    return rows


if __name__ == "__main__":
    samples = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "samples")
    quick = "--quick" in sys.argv
    run_benchmark(samples, quick=quick)
