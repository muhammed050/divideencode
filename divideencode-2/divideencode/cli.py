import argparse
import hashlib
import os
import sys
import time

from . import __version__
from .decoder import decompress, decompress_with_trace
from .encoder import compress, recursive_compress
from .errors import DivideEncodeError
from .strategies import render_trace, set_search_mode


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def cmd_compress(args):
    if not os.path.isfile(args.input):
        print("error: input file not found: %s" % args.input, file=sys.stderr)
        return 2
    with open(args.input, "rb") as fh:
        data = fh.read()
    if args.exhaustive:
        set_search_mode("exhaustive")
    else:
        set_search_mode("beam", k=args.k)
    t0 = time.perf_counter()
    blob = compress(data, depth=args.depth)
    dt = time.perf_counter() - t0
    with open(args.output, "wb") as fh:
        fh.write(blob)
    orig = len(data)
    comp = len(blob)
    ratio = (comp / orig) if orig else 1.0
    saved = 100.0 * (1 - ratio) if orig else 0.0
    print("original:     %d bytes" % orig)
    print("compressed:   %d bytes" % comp)
    print("ratio:        %.4f" % ratio)
    print("space saved:  %.2f%%" % saved)
    print("time:         %.3f s (%.3f MB/s)" % (
        dt, (orig / 1048576.0) / dt if dt > 0 else float("inf")))
    if args.sha:
        print("sha256(in):   %s" % _sha256(data))
    return 0


def cmd_decompress(args):
    if not os.path.isfile(args.input):
        print("error: input file not found: %s" % args.input, file=sys.stderr)
        return 2
    with open(args.input, "rb") as fh:
        blob = fh.read()
    t0 = time.perf_counter()
    try:
        data = decompress(blob)
    except DivideEncodeError as exc:
        print("decompression failed: %s" % exc, file=sys.stderr)
        return 1
    dt = time.perf_counter() - t0
    with open(args.output, "wb") as fh:
        fh.write(data)
    print("restored:     %d bytes" % len(data))
    print("time:         %.3f s" % dt)
    if args.sha:
        print("sha256(out):  %s" % _sha256(data))
    return 0


def cmd_inspect(args):
    if not os.path.isfile(args.input):
        print("error: input file not found: %s" % args.input, file=sys.stderr)
        return 2
    with open(args.input, "rb") as fh:
        blob = fh.read()
    try:
        info = decompress_with_trace(blob)
    except DivideEncodeError as exc:
        print("inspect failed: %s" % exc, file=sys.stderr)
        return 1
    orig = info["original_size"]
    comp = info["compressed_size"]
    print("original size:       %d bytes" % orig)
    print("compressed size:     %d bytes" % comp)
    ratio = (comp / orig) if orig else 1.0
    print("compression ratio:   %.4f" % ratio)
    print("space saved:         %.2f%%" % (100.0 * (1 - ratio) if orig else 0.0))
    print("stored crc32:        %08x" % info["stored_crc32"])
    print("strategy tree:")
    print(render_trace(info["trace"]))
    return 0


def cmd_recursive(args):
    if not os.path.isfile(args.input):
        print("error: input file not found: %s" % args.input, file=sys.stderr)
        return 2
    with open(args.input, "rb") as fh:
        data = fh.read()
    final, sizes = recursive_compress(data, max_passes=args.max_passes)
    for i, size in enumerate(sizes):
        label = "original" if i == 0 else "pass %d" % i
        delta = ""
        if i > 0:
            d = size - sizes[i - 1]
            delta = "  (%+d)" % d
        print("%-10s %10d bytes%s" % (label, size, delta))
    if len(sizes) > 1 and sizes[-1] < sizes[0]:
        print("result: recursive compression reduced total size by %d bytes"
              % (sizes[0] - sizes[-1]))
    else:
        print("result: no further reduction possible")
    return 0


def cmd_benchmark(args):
    from benchmarks.benchmark import run_benchmark

    target = args.path
    if not os.path.isdir(target):
        print("error: benchmark path must be a directory", file=sys.stderr)
        return 2
    run_benchmark(target, results_dir=args.results_dir, quick=args.quick)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="divideencode",
        description="DivideEncode experimental lossless compressor")
    parser.add_argument("--version", action="version",
                        version="divideencode %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("compress", help="compress a file")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--depth", type=int, default=3,
                   help="max recursion depth of strategy nesting (default 3)")
    p.add_argument("--exhaustive", action="store_true",
                   help="force legacy exhaustive strategy search")
    p.add_argument("--k", type=int, default=3,
                   help="beam width in beam mode (default 3)")
    p.add_argument("--sha", action="store_true", help="print sha256 of input")
    p.set_defaults(func=cmd_compress)

    p = sub.add_parser("decompress", help="decompress a .de file")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--sha", action="store_true",
                   help="print sha256 of restored output")
    p.set_defaults(func=cmd_decompress)

    p = sub.add_parser("inspect", help="show container statistics")
    p.add_argument("input")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("recursive", help="iterative re-compression experiment")
    p.add_argument("input")
    p.add_argument("--max-passes", type=int, default=16)
    p.set_defaults(func=cmd_recursive)

    p = sub.add_parser("benchmark", help="run benchmark suite on a directory")
    p.add_argument("path")
    p.add_argument("--results-dir", default=None)
    p.add_argument("--quick", action="store_true")
    p.set_defaults(func=cmd_benchmark)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except DivideEncodeError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
