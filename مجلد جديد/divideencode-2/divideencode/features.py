"""Cheap per-node feature extraction and per-compression caching (P1).

All statistics are computed once per distinct byte string per compress()
call, using C-speed primitives (Counter, slicing, translate, regex).
Nothing here changes the compressed format; the caches only prevent
recomputation of identical work inside one search.

Heuristic thresholds live in scoring.py; this module only measures.
"""
import re
import sys
from collections import Counter

from .patterns import _RUN_RE

_WINDOW = 32768
_MEMO_MAX_ENTRIES = 1024

# translate table: 1 for "non printable / control", 0 for text-ish
_PRINTABLE_TBL = bytes(
    0 if (32 <= b < 127 or b in (9, 10, 13)) else 1
    for b in range(256)
)


class Features:
    __slots__ = (
        "n", "freq", "k", "H0", "max_freq", "zero_frac",
        "printable_frac", "run_bytes_frac", "big_run", "runs_per_kb",
        "rep4", "delta_eq_frac", "hi_zero2", "hi_zero4",
    )

    def __repr__(self):
        return ("Features(n=%d k=%d H0=%.3f zero=%.3f print=%.3f "
                "run=%.4f big_run=%s rpkb=%.1f rep4=%.4f dEq=%.4f "
                "hz2=%.3f hz4=%.3f)" % (
                    self.n, self.k, self.H0, self.zero_frac,
                    self.printable_frac, self.run_bytes_frac, self.big_run,
                    self.runs_per_kb, self.rep4, self.delta_eq_frac,
                    self.hi_zero2, self.hi_zero4))


def _windows(data):
    n = len(data)
    if n <= _WINDOW:
        return data, None
    return data[:_WINDOW], data[(n // 2):(n // 2) + _WINDOW]


def extract_features(data):
    f = Features()
    n = len(data)
    f.n = n
    if n == 0:
        f.freq = Counter()
        f.k = 0
        f.H0 = 0.0
        f.max_freq = 0
        f.zero_frac = 0.0
        f.printable_frac = 0.0
        f.run_bytes_frac = 0.0
        f.big_run = False
        f.runs_per_kb = 0.0
        f.rep4 = 0.0
        f.delta_eq_frac = 0.0
        f.hi_zero2 = 0.0
        f.hi_zero4 = 0.0
        return f

    freq = Counter(data)
    f.freq = freq
    f.k = len(freq)
    f.max_freq = max(freq.values())
    f.zero_frac = freq.get(0, 0) / n

    # entropy over <=256 symbols
    import math
    h = 0.0
    for c in freq.values():
        p = c / n
        h -= p * math.log2(p)
    f.H0 = h

    w1, w2 = _windows(data)

    # printable fraction over sampled windows
    total = len(w1) + (len(w2) if w2 else 0)
    nonp = w1.translate(_PRINTABLE_TBL).count(1)
    if w2:
        nonp += w2.translate(_PRINTABLE_TBL).count(1)
    f.printable_frac = 1.0 - nonp / total

    # run statistics over sampled windows
    run_bytes = 0
    runs = 0
    big_run = False
    for w in ((w1,) if w2 is None else (w1, w2)):
        for m in _RUN_RE.finditer(w):
            rl = m.end() - m.start()
            run_bytes += rl
            runs += 1
            if rl >= 32:
                big_run = True
    scanned = sum(len(w) for w in ((w1,) if w2 is None else (w1, w2)))
    f.run_bytes_frac = run_bytes / scanned
    f.big_run = big_run
    f.runs_per_kb = 1000.0 * runs / scanned

    # 4-gram repetition rate: build set from window1, probe window2 (or tail)
    step = 3
    probes_a = range(0, len(w1) - 3, step)
    gram_set = {bytes(w1[i:i + 4]) for i in probes_a}
    if w2:
        probe_positions = range(0, len(w2) - 3, step)
        hits = sum(1 for j in probe_positions
                   if bytes(w2[j:j + 4]) in gram_set)
        denom = len(probe_positions)
    else:
        half = max(1, (len(w1) - 3) // (2 * step))
        hits = 0
        seen = 0
        for j in range(0, len(w1) - 3, step):
            g = w1[j:j + 4]
            if seen < half:
                seen += 1
            elif g in gram_set:
                hits += 1
            else:
                pass
        denom = (len(w1) - 3) // step - half
    f.rep4 = hits / denom if denom > 0 else 0.0

    # adjacent-equality (delta suitability), sampled over window1
    s = w1
    if len(s) > 1:
        eq = sum(1 for a, b in zip(s, s[1:]) if a == b)
        f.delta_eq_frac = eq / (len(s) - 1)
    else:
        f.delta_eq_frac = 0.0

    # high-byte zero fractions for little-endian words (matches divide_transform)
    le = sys.byteorder == "little"
    usable2 = n & ~1
    if usable2 >= 2:
        hi_idx = 1 if le else 0
        f.hi_zero2 = data[hi_idx:usable2:2].count(0) / (usable2 // 2)
    else:
        f.hi_zero2 = 0.0
    usable4 = n & ~3
    if usable4 >= 4:
        top_idx = 3 if le else 0
        f.hi_zero4 = data[top_idx:usable4:4].count(0) / (usable4 // 4)
    else:
        f.hi_zero4 = 0.0
    return f


class EncodeContext:
    """Per-compress() state. Never shared across independent compress calls.

    - memo: (id(bytes), depth) -> encoded blob; strong refs keep ids unique.
    - freq_cache: id(bytes) -> Counter shared by huffman/dict escape.
    - feature_cache: id(bytes) -> Features.
    - stats: optional instrumentation counters (cheap when disabled).
    - work_used/work_limit: global search-work budget in input bytes fed
      through expensive encoders; None disables the limit.
    """

    def __init__(self, debug=False, work_limit=None):
        # values are (data_ref, payload) tuples so id() keys cannot be
        # reused while an entry lives
        self.memo = {}
        self.freq_cache = {}
        self.feature_cache = {}
        self.probe_cache = {}
        self.work_used = 0
        self.work_limit = work_limit
        self.stats = {
            "memo_hits": 0, "memo_misses": 0,
            "feature_time": 0.0, "feature_calls": 0,
            "predictions": [], "pruned": [],
        } if debug else None
        self.debug = debug

    def budget_ok(self):
        return self.work_limit is None or self.work_used < self.work_limit

    def features(self, data):
        entry = self.feature_cache.get(id(data))
        if entry is not None:
            return entry[1]
        import time
        t0 = time.perf_counter()
        fv = extract_features(data)
        if self.debug:
            self.stats["feature_calls"] += 1
            self.stats["feature_time"] += time.perf_counter() - t0
        self.feature_cache[id(data)] = (data, fv)
        return fv

    def freq_table(self, data):
        entry = self.freq_cache.get(id(data))
        if entry is not None:
            return entry[1]
        c = Counter(data)
        self.freq_cache[id(data)] = (data, c)
        return c

    def memo_get(self, key):
        hit = self.memo.get(key)
        if self.debug:
            self.stats["memo_hits" if hit is not None else "memo_misses"] += 1
        return None if hit is None else hit[1]

    def memo_put(self, key, data, blob):
        if len(self.memo) >= _MEMO_MAX_ENTRIES:
            self.memo.clear()
        self.memo[key] = (data, blob)
