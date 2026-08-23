import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from v2 import tokenize, detokenize, Config, LZ2Error


def gen_pure_random(rng, size):
    return bytes(rng.randrange(256) for _ in range(size))


def gen_low_alphabet(rng, size, alphabet=None):
    alphabet = alphabet or bytes(rng.sample(range(256), rng.randrange(2, 9)))
    return bytes(alphabet[rng.randrange(len(alphabet))] for _ in range(size))


def gen_repetitive(rng, size):
    out = bytearray()
    while len(out) < size:
        chunk = rng.choice([
            b"abcdefgh", b"\x00" * rng.randrange(1, 64),
            b"xyz" * rng.randrange(1, 20),
        ])
        out += chunk
    return bytes(out[:size])


def gen_piecewise(rng, size):
    parts = []
    total = 0
    while total < size:
        mode = rng.randrange(4)
        n = rng.randrange(1, max(2, size // 4))
        if mode == 0:
            p = gen_pure_random(rng, min(n, size - total))
        elif mode == 1:
            p = gen_low_alphabet(rng, min(n, size - total))
        elif mode == 2:
            p = gen_repetitive(rng, min(n, size - total))
        else:
            src = rng.choice(parts) if parts else b""
            off = rng.randrange(len(src)) if src else 0
            p = src[off:off + n]
        parts.append(p)
        total += len(p)
    return b"".join(parts)[:size]


GENERATORS = (gen_pure_random, gen_low_alphabet, gen_repetitive, gen_piecewise)


class TestSeededFuzz(unittest.TestCase):
    """Fast seeded fuzz suite (CI-safe subset of the 100k standalone run)."""

    def test_2000_randomized_round_trips(self):
        rng = random.Random(20260822)
        cfg_pool = []
        for window in (8, 256, 4096, 65536):
            for chain in (1, 4, 32):
                for lazy in (0, 1):
                    cfg_pool.append(Config(window=window, max_chain=chain,
                                           lazy=lazy))
        failures = []
        reps_seen = 0
        matches_seen = 0
        for case in range(2000):
            gen = GENERATORS[case % len(GENERATORS)]
            size = rng.choice((1, 2, 3, 5, 17, 100, 333, 1024, 4096))
            data = gen(rng, size)
            cfg = cfg_pool[case % len(cfg_pool)]
            blob, st = tokenize(data, cfg)
            try:
                out = detokenize(blob)
            except LZ2Error as exc:
                failures.append(("decode-error", case, str(exc)))
                continue
            if out != data:
                failures.append(("mismatch", case, len(data)))
            reps_seen += st.rep_count
            matches_seen += st.match_count
        self.assertEqual(failures, [])
        self.assertGreater(matches_seen + reps_seen, 0)


if __name__ == "__main__":
    unittest.main()
