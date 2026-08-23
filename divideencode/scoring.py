"""Heuristic size predictors for strategy ranking (P2).

Every predictor returns an estimated FINAL serialized size (mode byte +
headers + payload) so values are directly comparable with actual
encode_node outputs.

These are HEURISTICS, not bounds: they rank candidates and gate obviously
hopeless branches. The only exact prediction is RAW. Calibration data is
collected via EncodeContext instrumentation (predicted vs actual) and the
ranking margins in strategies.py must absorb estimator error.
"""
import math
from collections import Counter

from .divide_transform import divide_transform
from .patterns import lz_probe, _lz_encode_core

_PROBE_BYTES = 16384
_DIVIDE_SAMPLE = 32768
_DICT_SAMPLE = 32768
_LZ_GATE_MIN = 49152
_MIN_LZ_LEN = 64
_MIN_DICT_LEN = 1024
_MIN_DIVIDE_LEN = 32


def _varint_len(n):
    l = 1
    while n >= 128:
        n >>= 7
        l += 1
    return l


def _huff_size(n, freq):
    """Huffman formula: payload floor + serialized table. Heuristic."""
    k = len(freq)
    bits = 0.0
    for c in freq.values():
        p = c / n
        bits -= p * math.log2(p)
    return 1 + _varint_len(k) + 2 * k + (n * bits + 7) // 8


def predict_raw(n):
    """Exact: mode byte + stored bytes."""
    return 1 + n


def predict_huff(feats):
    if feats.n == 0:
        return None
    return float(_huff_size(feats.n, feats.freq))


def predict_rle(feats):
    n = feats.n
    run_bytes = feats.run_bytes_frac * n
    lit = n - run_bytes
    runs_est = feats.runs_per_kb * n / 1000.0
    if runs_est < 1:
        runs_est = 1 if feats.run_bytes_frac > 0 else 0
    avg_run = run_bytes / runs_est if runs_est else 0
    chunks_per_run = max(1.0, avg_run / 130.0)
    run_cost = 2.0 * runs_est * chunks_per_run
    lit_cost = lit * (1.0 + 1.0 / 128.0)
    return 1 + lit_cost + run_cost


def probe_ratio(data, ctx):
    """Cached single-window LZ repeatability ratio."""
    key = ("lzprobe", id(data))
    cached = ctx.probe_cache.get(key)
    if cached is not None:
        return cached[1]
    r = lz_probe(data, _PROBE_BYTES)
    ctx.probe_cache[key] = (data, r)
    return r


def mid_probe_ratio(data):
    """Fallback window in the middle, mirrors the legacy dual-window gate."""
    n = len(data)
    mid = n >> 1
    w = bytes(data[mid:mid + _PROBE_BYTES])
    if len(w) < 1024:
        return None
    from .patterns import _lz_encode_core
    return len(_lz_encode_core(w)) / max(1, len(w))


def predict_lz(data, ctx):
    n = len(data)
    if n < _MIN_LZ_LEN:
        return None
    r = probe_ratio(data, ctx)
    if r >= 0.96 and n >= _LZ_GATE_MIN:
        rm = mid_probe_ratio(data)
        if rm is not None and rm < r:
            r = rm
        ctx.probe_cache[("lzprobe", id(data))] = (data, r)
    return 1 + n * r


def _delta_sample_stats(sample):
    prev = 0
    cnt = Counter()
    for b in sample:
        cnt[(b - prev) & 0xFF] += 1
        prev = b
    return cnt


def predict_delta(data, feats, ctx):
    n = feats.n
    if n == 0:
        return None
    sample = bytes(data[:_PROBE_BYTES])
    # local delta of the sample (kept here to avoid an import cycle with
    # strategies._delta_encode)
    prev = 0
    buf = bytearray(len(sample))
    for i, b in enumerate(sample):
        buf[i] = (b - prev) & 0xFF
        prev = b
    sn = len(sample)
    scale = n / sn if sn else 1.0
    child_factor = _child_factor(bytes(buf))
    return 1 + sn * child_factor * scale


def predict_divide_candidates(data, feats, static_pairs, top_k=3):
    """Re-rank static DIVIDE pairs using a sampled transform measurement."""
    n = feats.n
    if n < _MIN_DIVIDE_LEN or not static_pairs:
        return []
    sample_n = min(n, _DIVIDE_SAMPLE)
    sample = bytes(data[:sample_n])
    scored = []
    for w, d in static_pairs[:6]:
        info = divide_transform(sample, w, d)
        nw_full = n // w
        q_payload = nw_full * info["wq"]
        r_payload = (nw_full * info["rbits"] + 7) // 8
        # child potential per stream: entropy of sampled stream vs raw
        q_child = _stream_child_estimate(info["quotient_stream"],
                                         sample_n // w, info["wq"])
        r_child = _stream_child_estimate(info["remainder_stream"],
                                         sample_n // w, info["rbits"])
        est = 4 + len(info["tail"]) + q_child * q_payload + \
            r_child * r_payload
        scored.append((est, w, d))
    scored.sort()
    return [(est, (w, d)) for est, w, d in scored[:top_k]]


def _stream_child_estimate(stream_sample, n_words, width_bytes):
    """Return a multiplicative factor in (0..1]: predicted final size of a
    transformed stream per payload byte."""
    return _child_factor(stream_sample)


def feats_lz_worthwhile(sample):
    """Cheap zero-run / repetition sniff to skip pointless LZ probes on
    streams that are obviously literal-dense."""
    zeros = sample.count(0)
    return zeros >= len(sample) * 0.10 or \
        len(set(sample)) <= 96


def _child_factor(sample):
    """Predicted final size of a child subtree per payload byte:
    min(raw, huff, lz-probe) measured on the sample itself."""
    sn = len(sample)
    if sn == 0:
        return 1.0
    cnt = Counter(sample)
    huff = _huff_size(sn, cnt) - 1  # drop mode byte; compare payload scales
    raw = sn
    best = min(raw, huff)
    if sn >= _MIN_LZ_LEN and feats_lz_worthwhile(sample):
        best = min(best, len(_lz_encode_core(bytes(sample))))
    factor = best / sn
    return min(1.0, factor)


def dict_gate(feats):
    """Cheap pre-mining gate. Heuristic conditions, individually tunable."""
    return (feats.rep4 > 0.06 and
            (feats.printable_frac > 0.60 or feats.zero_frac > 0.5))


def predict_dict(data, feats, ctx):
    n = feats.n
    if n < _MIN_DICT_LEN or not dict_gate(feats):
        return None
    key = ("dictpred", id(data))
    cached = ctx.probe_cache.get(key)
    if cached is not None:
        return cached[1]
    from .dictionary import build_dictionary
    prefix = bytes(data[:_DICT_SAMPLE])
    entries, escape = build_dictionary(prefix,
                                       full_freq=ctx.freq_table(prefix))
    if not entries:
        ctx.probe_cache[key] = (data, None)
        return None
    from .dictionary import substitute
    pn = len(prefix)
    sub_prefix = substitute(prefix, entries, escape)
    scale = n / pn
    entry_bytes = sum(len(e) + 1 for e in entries) + _varint_len(len(entries))
    sub_est = len(sub_prefix) * scale
    inner_factor = _child_factor(sub_prefix)
    total = 1 + 1 + entry_bytes + _varint_len(int(sub_est)) + \
        sub_est * inner_factor
    result = (total, len(entries))
    ctx.probe_cache[key] = (data, result)
    return result


def predict_all(data, feats, ctx, static_divide_pairs=()):
    preds = {
        "raw": predict_raw(feats.n),
        "huff": predict_huff(feats),
        "rle": predict_rle(feats),
        "lz": predict_lz(data, ctx),
        "delta": predict_delta(data, feats, ctx),
    }
    d = predict_dict(data, feats, ctx)
    preds["dict"] = None if d is None else d[0]
    for est, pair in predict_divide_candidates(data, feats,
                                               static_divide_pairs):
        preds[("divide",) + tuple(pair)] = est
    return preds
