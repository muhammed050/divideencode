"""Phase-0 baseline benchmark for the optimization project.

Runs compress/decompress over every sample file, records timing, sizes,
SHA-256 verification, strategy-level instrumentation and the chosen tree,
and persists results incrementally so partial progress survives interruption.

Usage: python benchmarks/baseline.py [output.json]
"""
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from divideencode import compress, decompress, decompress_with_trace
from divideencode import encoder as enc_mod
from divideencode import strategies as S
from divideencode import huffman
from divideencode import dictionary as dict_mod
from divideencode.strategies import render_trace

SAMPLES = [
    "App.java", "main.py", "program.c", "engine.cpp", "app.js", "app.ts",
    "lib.rs", "page.html", "feed.xml", "data.json", "big.json", "dump.sql",
    "server.log", "table.csv", "text_en.txt", "notes.md", "doc.pdf",
    "photo.jpg", "photo.png", "photo.webp", "archive.zip",
    "random_os.bin", "random_prng.bin", "sensor_i16.bin", "counters_u32.bin",
]

STATS = {}
NODE_DEPTH = {}


def _stat(key):
    return STATS.setdefault(key, {"calls": 0, "bytes": 0, "time": 0.0})


def timed(key, fn):
    def wrapper(data, *a, **kw):
        st = _stat(key)
        st["calls"] += 1
        st["bytes"] += len(data)
        t0 = time.perf_counter()
        try:
            return fn(data, *a, **kw)
        finally:
            st["time"] += time.perf_counter() - t0
    return wrapper


def install_instrumentation():
    orig = {
        "rle": S.rle_encode,
        "lz": S.lz_encode,
        "huff": huffman.encode,
        "dict_mine": dict_mod.build_dictionary,
        "dict_sub": dict_mod.substitute,
        "delta": S._delta_encode,
        "divide": S.divide_transform,
        "estimate": S.estimate_candidates,
    }
    S.rle_encode = timed("rle", orig["rle"])
    S.lz_encode = timed("lz", orig["lz"])
    huffman.encode = timed("huff", orig["huff"])
    dict_mod.build_dictionary = timed("dict_mine", orig["dict_mine"])
    S.substitute = timed("dict_sub", orig["dict_sub"])
    S._delta_encode = timed("delta", orig["delta"])
    S.divide_transform = timed("divide", orig["divide"])
    S.estimate_candidates = timed("estimate", orig["estimate"])

    node_orig = S.encode_node

    def node_wrapper(data, depth=S.DEFAULT_DEPTH, *args, **kwargs):
        NODE_DEPTH[depth] = NODE_DEPTH.get(depth, 0) + 1
        st = _stat("encode_node")
        st["calls"] += 1
        st["bytes"] += len(data)
        t0 = time.perf_counter()
        try:
            return node_orig(data, depth, *args, **kwargs)
        finally:
            st["time"] += time.perf_counter() - t0

    S.encode_node = node_wrapper
    enc_mod.encode_node = node_wrapper
    return orig


def bench_file(path):
    with open(path, "rb") as fh:
        data = fh.read()
    sha_in = hashlib.sha256(data).hexdigest()

    STATS.clear()
    NODE_DEPTH.clear()

    t0 = time.perf_counter()
    blob = compress(data)
    ct = time.perf_counter() - t0

    t0 = time.perf_counter()
    out = decompress(blob)
    dt = time.perf_counter() - t0

    ok = out == data
    sha_out = hashlib.sha256(out).hexdigest()
    if not ok:
        print("  !!! ROUNDTRIP FAILURE", flush=True)

    try:
        trace = render_trace(decompress_with_trace(blob)["trace"])
        tree_lines = trace.split("\n")
        if len(tree_lines) > 24:
            tree_lines = tree_lines[:24] + ["... (%d more)" % (len(tree_lines) - 24)]
        tree = "\n".join(tree_lines)
    except Exception as exc:
        tree = "trace unavailable: %r" % exc

    row = {
        "file": os.path.basename(path),
        "original": len(data),
        "compressed": len(blob),
        "ratio": round(len(blob) / len(data), 5) if data else 1.0,
        "ctime_s": round(ct, 4),
        "dtime_s": round(dt, 4),
        "roundtrip": ok,
        "sha_match": sha_in == sha_out,
        "sha256_original": sha_in,
        "encode_node_calls": STATS.get("encode_node", {}).get("calls", 0),
        "node_depth_histogram": {str(k): v for k, v in sorted(NODE_DEPTH.items(),
                                                               reverse=True)},
        "strategy_stats": {
            k: {"calls": v["calls"], "bytes": v["bytes"],
                "time_s": round(v["time"], 4)}
            for k, v in sorted(STATS.items())
        },
        "tree": tree,
    }
    return row


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "results", "baseline.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    samples_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "samples")

    rows = []
    if os.path.exists(out_path):
        with open(out_path) as fh:
            try:
                rows = json.load(fh)
            except ValueError:
                rows = []
    done = {r["file"] for r in rows}

    install_instrumentation()

    for name in SAMPLES:
        if name in done:
            print("skip (already measured): %s" % name, flush=True)
            continue
        path = os.path.join(samples_dir, name)
        if not os.path.isfile(path):
            print("MISSING sample: %s" % name, flush=True)
            continue
        print("=== %s ..." % name, flush=True)
        t0 = time.perf_counter()
        row = bench_file(path)
        rows.append(row)
        with open(out_path, "w") as fh:
            json.dump(rows, fh, indent=1)
        print("    %d -> %d B  c=%.2fs d=%.3fs  nodes=%d  ok=%s  (wall %.1fs)"
              % (row["original"], row["compressed"], row["ctime_s"],
                 row["dtime_s"], row["encode_node_calls"], row["roundtrip"],
                 time.perf_counter() - t0), flush=True)

    print("baseline complete: %d files -> %s" % (len(rows), out_path),
          flush=True)


if __name__ == "__main__":
    main()
