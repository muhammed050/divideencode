"""DE2 single-pass feature scanner (M0).

One scan per block produces a reusable FeatureSet consumed by the
classifier and by codec selection. No predictor performs its own scan.

All heavy counting uses C-speed primitives (bytes.count, bytes.translate,
re.finditer, Counter). Python-level loops only touch small samples.
"""
import re
from collections import Counter
from math import log2

_SAMPLE = 65536
_GRAM = 8
_GRAM_STEP = 5
_MONO_SAMPLES = 2048
_DELTA_SAMPLES = 8192

_RUN_RE = re.compile(rb"(.)\1{2,}", re.DOTALL)

_ASCII_TBL = bytes(0 if b < 128 else 1 for b in range(256))
_PRINTABLE_TBL = bytes(0 if (32 <= b < 127 or b in (9, 10, 13)) else 1
                       for b in range(256))
_DIGIT_TBL = bytes(0 if 48 <= b < 58 else 1 for b in range(256))

_JSON_CHARS = b'"{}[]:'
_CSV_CHARS = b",;\t|"
_XML_CHARS = b"<>/"


class FeatureSet:
    __slots__ = (
        "n", "k", "h0", "max_freq",
        "zero_frac", "run_bytes_frac", "big_run",
        "ascii_frac", "printable_frac", "digit_frac",
        "newline_per_kb", "delim_per_kb",
        "json_score", "csv_score", "xml_score",
        "match_density", "mono32", "delta_ratio",
    )

    def __repr__(self):
        return ("FeatureSet(n=%d k=%d h0=%.3f zero=%.3f run=%.4f big=%s "
                "print=%.3f digit=%.3f nl/kb=%.1f dl/kb=%.1f "
                "json=%.3f csv=%.3f xml=%.3f match=%.3f mono=%.3f "
                "dratio=%.3f)" % (
                    self.n, self.k, self.h0, self.zero_frac,
                    self.run_bytes_frac, self.big_run,
                    self.printable_frac, self.digit_frac,
                    self.newline_per_kb, self.delim_per_kb,
                    self.json_score, self.csv_score, self.xml_score,
                    self.match_density, self.mono32, self.delta_ratio))


def _empty_features(n=0):
    f = FeatureSet()
    f.n = n
    f.k = 0
    f.h0 = 0.0
    f.max_freq = 0
    f.zero_frac = 0.0
    f.run_bytes_frac = 0.0
    f.big_run = False
    f.ascii_frac = 1.0
    f.printable_frac = 1.0
    f.digit_frac = 0.0
    f.newline_per_kb = 0.0
    f.delim_per_kb = 0.0
    f.json_score = 0.0
    f.csv_score = 0.0
    f.xml_score = 0.0
    f.match_density = 0.0
    f.mono32 = 0.0
    f.delta_ratio = 0.0
    return f


def scan_features(data):
    """Single pass over `data` (bytes-like). Returns a reusable FeatureSet."""
    n = len(data)
    if n == 0:
        return _empty_features(0)

    f = FeatureSet()
    f.n = n

    # --- histogram: entropy estimate + unique count (one C-speed count) ---
    hist = Counter(data)
    f.k = len(hist)
    f.max_freq = max(hist.values())
    h = 0.0
    for c in hist.values():
        p = c / n
        h -= p * log2(p)
    f.h0 = h

    # --- byte-class ratios via translate+count (C-speed) ---
    f.ascii_frac = 1.0 - data.translate(_ASCII_TBL).count(1) / n
    f.printable_frac = 1.0 - data.translate(_PRINTABLE_TBL).count(1) / n
    f.digit_frac = 1.0 - data.translate(_DIGIT_TBL).count(1) / n

    # --- scalar byte counts (C-speed) ---
    zeros = data.count(0)
    newlines = data.count(10)
    delims = sum(data.count(c) for c in _CSV_CHARS)
    json_chars = sum(data.count(c) for c in _JSON_CHARS)
    xml_chars = sum(data.count(c) for c in _XML_CHARS)

    f.zero_frac = zeros / n
    f.newline_per_kb = newlines * 1000.0 / n
    f.delim_per_kb = delims * 1000.0 / n

    # --- run density over one sample window ---
    sample = data[:min(n, _SAMPLE)]
    run_bytes = 0
    big_run = False
    for m in _RUN_RE.finditer(sample):
        rl = m.end() - m.start()
        run_bytes += rl
        if rl >= 32:
            big_run = True
    sn = len(sample)
    f.run_bytes_frac = run_bytes / sn
    f.big_run = big_run

    # --- repetition / match density: gram-set probe between two windows ---
    w1 = data[:min(n, 32768)]
    mid = n >> 1
    w2 = data[mid:mid + min(n, 32768)]
    grams = {bytes(w1[i:i + _GRAM])
             for i in range(0, max(0, len(w1) - _GRAM), _GRAM_STEP)}
    probes = range(0, max(0, len(w2) - _GRAM), _GRAM_STEP)
    hits = sum(1 for j in probes if bytes(w2[j:j + _GRAM]) in grams)
    nprobes = len(probes)
    f.match_density = hits / nprobes if nprobes else (1.0 if f.k <= 2 else 0.0)

    # --- structure markers (normalized per KB) ---
    per_kb = 1000.0 / n
    f.json_score = json_chars * per_kb
    f.csv_score = delims * per_kb
    f.xml_score = xml_chars * per_kb

    # --- numeric monotonicity proxy on sampled little-endian u32 words ---
    usable = min(n & ~3, _MONO_SAMPLES * 4)
    if usable >= 8:
        vals = [int.from_bytes(data[i:i + 4], "little")
                for i in range(0, usable, 4)]
        pairs = list(zip(vals, vals[1:]))
        inc = sum(1 for a, b in pairs if b >= a)
        dec = sum(1 for a, b in pairs if b <= a)
        f.mono32 = max(inc, dec) / len(pairs)
    else:
        f.mono32 = 0.0

    # --- delta smoothness: entropy drop of the HIGH-byte plane of a
    # u16 zigzag-delta sample (sensor-style signals collapse there;
    # text/random/media do not)
    sn = min(n & ~1, _DELTA_SAMPLES * 2)
    if sn >= 64 and f.k > 16:
        hi = bytearray()
        prev = int.from_bytes(data[0:2], "little")
        for i in range(2, sn, 2):
            x = int.from_bytes(data[i:i + 2], "little")
            sd = (x - prev) & 0xFFFF
            if sd >= 0x8000:
                sd -= 0x10000
            hi.append((((sd << 1) ^ (sd >> 16)) & 0xFFFF) >> 8)
            prev = x
        hd = 0.0
        cnt = Counter(hi)
        m = len(hi)
        for c in cnt.values():
            p = c / m
            hd -= p * log2(p)
        f.delta_ratio = max(0.0, f.h0 - hd)
    else:
        f.delta_ratio = 0.0

    return f
