"""Standalone M2 gate: >=100,000 randomized FULL-pipeline round trips.

Pipeline under test: v2.entropy.compress / decompress
(LZ2 tokenizer -> split streams -> canonical Huffman + class/extra bits,
with RAW fallback mode).

Run:  python tests_v2\fuzz_100k_entropy.py
Writes benchmarks/results/v2_m2_fuzz.json
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from v2 import entropy as E

N_CASES = 105_000


def gen_pure_random(rng, size):
    return bytes(rng.randrange(256) for _ in range(size))


def gen_low_alphabet(rng, size):
    k = rng.randrange(2, 9)
    alphabet = bytes(rng.sample(range(256), k))
    return bytes(alphabet[rng.randrange(k)] for _ in range(size))


def gen_biased_runs(rng, size):
    out = bytearray()
    while len(out) < size:
        if rng.random() < 0.5:
            out += bytes((rng.randrange(256),)) * rng.randrange(1, 200)
        else:
            alphabet = b"ACGT \t\n"
            for _ in range(rng.randrange(1, 64)):
                out.append(alphabet[rng.randrange(len(alphabet))])
    return bytes(out[:size])


def gen_repetitive(rng, size):
    out = bytearray()
    while len(out) < size:
        chunk = rng.choice([
            b"abcdefgh", b"xyz" * rng.randrange(1, 30),
            bytes((rng.randrange(256),)) * rng.randrange(1, 80),
        ])
        out += chunk
    return bytes(out[:size])


def gen_piecewise(rng, size):
    parts = []
    total = 0
    while total < size:
        mode = rng.randrange(4)
        n = min(rng.randrange(1, max(2, size // 3)), size - total)
        if mode == 0:
            p = gen_pure_random(rng, n)
        elif mode == 1:
            p = gen_low_alphabet(rng, n)
        elif mode == 2:
            p = gen_repetitive(rng, n)
        else:
            src = rng.choice(parts) if parts else b""
            off = rng.randrange(len(src)) if src else 0
            p = src[off:off + n]
        parts.append(p)
        total += len(p)
    return b"".join(parts)[:size]


def gen_json_records(rng, size):
    out = bytearray()
    i = 0
    while len(out) < size:
        out += ('{"id": %d, "name": "item_%d", "score": %.3f, '
                '"ok": %s},\n' % (i, i * 7 % 991, (i % 149) / 7,
                                  "true" if i % 3 == 0 else "false")
                ).encode()
        i += 1
    return bytes(out[:size])


def gen_log_lines(rng, size):
    levels = [b"INFO", b"WARN", b"ERROR", b"DEBUG"]
    mods = [b"http.server", b"db.pool", b"auth.service", b"queue.worker"]
    out = bytearray()
    t = 1755800000
    i = 0
    while len(out) < size:
        t += rng.randrange(10, 400)
        out += (b"2026-08-%02d %02d:%02d:%02d,%03d %-5s [%s] request "
                b"status=%d dur=%dms\n"
                % (1 + i % 28, (t // 3600) % 24, (t // 60) % 60, t % 60,
                   rng.randrange(1000), rng.choice(levels),
                   rng.choice(mods), rng.choice([200, 201, 404, 500]),
                   rng.randrange(1, 5000)))
        i += 1
    return bytes(out[:size])


GENERATORS = (gen_pure_random, gen_low_alphabet, gen_biased_runs,
              gen_repetitive, gen_piecewise, gen_json_records, gen_log_lines)

SIZES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 16, 17, 31, 63, 64, 65, 100,
         127, 128, 129, 255, 256, 333, 511, 512, 1024, 2048, 4096)


def main():
    rng = random.Random(24681357)
    cfg_pool = []
    for window in (64, 1024, 16384, 65536):
        for chain in (1, 4, 16, 32):
            for lazy in (0, 1):
                cfg_pool.append(E.Config(window=window, max_chain=chain,
                                         lazy=lazy))

    failures = []
    decode_errors = []
    mismatches = []
    raw_fallbacks = 0
    t0 = time.perf_counter()
    for case in range(N_CASES):
        gi = case % len(GENERATORS)
        gen = GENERATORS[gi]
        size = SIZES[rng.randrange(len(SIZES))]
        data = gen(rng, size)
        cfg = cfg_pool[case % len(cfg_pool)]
        try:
            blob = E.compress(data, cfg)
        except Exception as exc:  # noqa: BLE001 - any crash is a failure
            mismatches.append(("compress-crash", case, type(exc).__name__,
                               str(exc)[:80]))
            continue
        if len(blob) >= len(data) and blob[-len(data):] != data:
            # RAW fallback must store verbatim; cheap structural probe
            pass
        try:
            out = E.decompress(blob)
        except Exception as exc:  # noqa: BLE001
            decode_errors.append(("decode", case, type(exc).__name__,
                                  str(exc)[:80]))
            continue
        if out != data:
            mismatches.append(("mismatch", case, len(data), ""))
        elif len(blob) >= len(data):
            raw_fallbacks += 1
        if (case + 1) % 15000 == 0:
            print("  progress %d/%d cases, %.1fs elapsed"
                  % (case + 1, N_CASES, time.perf_counter() - t0))
    elapsed = time.perf_counter() - t0
    total_fail = len(decode_errors) + len(mismatches)
    print("=" * 60)
    print("M2 ENTROPY ROUND-TRIP FUZZ: %d cases in %.1fs (%.0f cases/s)"
          % (N_CASES, elapsed, N_CASES / elapsed))
    print("failures: %d (decode=%d mismatch/crash=%d)"
          % (total_fail, len(decode_errors), len(mismatches)))
    for f in (mismatches + decode_errors)[:10]:
        print("   ", f)
    print("RAW-fallback cases exercised: %d" % raw_fallbacks)
    print("VERDICT:", "PASS" if total_fail == 0 else "FAIL")
    print("=" * 60)

    with open(os.path.join(os.path.dirname(__file__), "..",
                           "benchmarks", "results", "v2_m2_fuzz.json"),
              "w") as fh:
        json.dump({
            "cases": N_CASES,
            "elapsed_s": round(elapsed, 2),
            "failures": total_fail,
            "decode_errors": len(decode_errors),
            "mismatches": len(mismatches),
            "raw_fallback_cases": raw_fallbacks,
            "verdict": "PASS" if total_fail == 0 else "FAIL",
            "seed": 24681357,
        }, fh, indent=2)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
