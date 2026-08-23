"""Standalone M1 gate: >=100,000 randomized LZ2 round-trip cases.

Run:  python tests_v2\fuzz_100k.py
Writes machine-readable summary to benchmarks/results/v2_m1_fuzz.json.
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from v2 import tokenize, detokenize, Config, LZ2Error

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


def gen_periodic_records(rng, size):
    unit = ('{"k%d": %d, "name": "item_%d", "tags": ["a","b"], '
            '"ok": %s},\n')
    out = bytearray()
    i = 0
    while len(out) < size:
        out += (unit % (i % 7, i, i,
                        "true" if i % 3 == 0 else "false")).encode()
        i += 1
    return bytes(out[:size])


GENERATORS = (gen_pure_random, gen_low_alphabet, gen_biased_runs,
              gen_repetitive, gen_piecewise, gen_periodic_records)

SIZES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 16, 17, 31, 63, 64, 65, 100,
         127, 128, 129, 255, 256, 333, 511, 512, 1024, 2048, 4096)


def main():
    rng = random.Random(987654321)
    cfg_pool = []
    for window in (8, 64, 1024, 16384, 65536):
        for chain in (1, 4, 8, 16, 32, 64):
            for lazy in (0, 1):
                cfg_pool.append(Config(window=window, max_chain=chain,
                                       lazy=lazy))

    per_gen_fail = [0] * len(GENERATORS)
    per_gen_cases = [0] * len(GENERATORS)
    decode_errors = []
    mismatches = []
    rep_total = 0
    match_total = 0
    lit_total = 0
    max_size_seen = 0
    t0 = time.perf_counter()
    for case in range(N_CASES):
        gi = case % len(GENERATORS)
        gen = GENERATORS[gi]
        size = SIZES[rng.randrange(len(SIZES))]
        data = gen(rng, size)
        cfg = cfg_pool[case % len(cfg_pool)]
        per_gen_cases[gi] += 1
        try:
            blob, st = tokenize(data, cfg)
        except Exception as exc:  # noqa: BLE001 - any crash is a failure
            mismatches.append(("tokenize-crash", case, type(exc).__name__))
            continue
        try:
            out = detokenize(blob)
        except LZ2Error as exc:
            decode_errors.append(("decode-error", case, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001
            decode_errors.append(("decode-crash", case, type(exc).__name__))
            continue
        if out != data:
            mismatches.append(("mismatch", case, len(data)))
        else:
            rep_total += st.rep_count
            match_total += st.match_count
            lit_total += st.literal_count
            max_size_seen = max(max_size_seen, len(data))
        if (case + 1) % 15000 == 0:
            print("  progress %d/%d cases, %.1fs elapsed"
                  % (case + 1, N_CASES, time.perf_counter() - t0))
    elapsed = time.perf_counter() - t0
    total_fail = len(decode_errors) + len(mismatches)
    print("=" * 60)
    print("LZ2 ROUND-TRIP FUZZ: %d cases in %.1fs (%.0f cases/s)"
          % (N_CASES, elapsed, N_CASES / elapsed))
    print("failures: %d (decode=%d mismatch/crash=%d)"
          % (total_fail, len(decode_errors), len(mismatches)))
    for gi, g in enumerate(GENERATORS):
        print("  %-20s cases=%6d failures=%d"
              % (g.__name__, per_gen_cases[gi], per_gen_fail[gi]))
    print("tokens seen: literals=%d matches=%d reps=%d"
          % (lit_total, match_total, rep_total))
    print("max round-tripped size: %d bytes" % max_size_seen)
    print("VERDICT:", "PASS" if total_fail == 0 else "FAIL")
    print("=" * 60)

    os.makedirs(os.path.join(os.path.dirname(__file__), "..",
                             "benchmarks", "results"), exist_ok=True)
    with open(os.path.join(os.path.dirname(__file__), "..",
                           "benchmarks", "results", "v2_m1_fuzz.json"),
              "w") as fh:
        json.dump({
            "cases": N_CASES,
            "elapsed_s": round(elapsed, 2),
            "failures": total_fail,
            "decode_errors": len(decode_errors),
            "mismatches": len(mismatches),
            "literals": lit_total,
            "matches": match_total,
            "reps": rep_total,
            "max_size": max_size_seen,
            "verdict": "PASS" if total_fail == 0 else "FAIL",
            "seed": 987654321,
        }, fh, indent=2)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
