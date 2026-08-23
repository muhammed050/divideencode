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


def _delta_bytes(sample):
    prev = 0
    buf = bytearray(len(sample))
    for i, b in enumerate(sample):
        buf[i] = (b - prev) & 0xFF
        prev = b
    return bytes(buf)


def _child_factor_deep(sample):
    """_child_factor plus up to TWO extra lookahead levels.

    Single-level estimates cannot see wins like DELTA -> DELTA -> LZ,
    where a transformed stream looks locally incompressible until a
    second difference aligns its structure. We therefore also measure
    the first- and second-difference views (and their 2-byte plane
    halves) with the same cheap primitives and keep the most optimistic
    (still evidence-based) estimate.
    """
    sn = len(sample)
    if sn == 0:
        return 1.0
    best = _child_factor(sample)
    # deep lookahead only pays on larger nodes; small streams resolve in
    # one level and the extra views/probes dominated their encode time.
    if best >= 0.95 and sn >= 8192:
        half = sample[:sn // 2]
        view = half
        for _level in range(2):
            view = _delta_bytes(view)
            vn = len(view)
            if vn < 512:
                break
            cand = _child_factor(view)
            pa = _child_factor(view[0::2])
            pb = _child_factor(view[1::2])
            cand = min(cand, 0.5 * pa + 0.5 * pb)
            if cand < best:
                best = cand
            if best < 0.5:
                break
    return best


def predict_delta(data, feats, ctx):
    n = feats.n
    if n == 0:
        return None
    sample = bytes(data[:_PROBE_BYTES])
    # local delta of the sample (kept here to avoid an import cycle with
    # strategies._delta_encode)
    buf = _delta_bytes(sample)
    sn = len(sample)
    scale = n / sn if sn else 1.0
    child_factor = _child_factor_deep(buf)
    return 1 + sn * child_factor * scale


def predict_divide_candidates(data, feats, static_pairs, top_k=3):
    """Re-rank static DIVIDE pairs using a sampled transform measurement."""
    n = feats.n
    if n < 16384 or n < _MIN_DIVIDE_LEN or not static_pairs:
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
    """Predicted final size of a child subtree per payload byte.

    Cheap variant: min(raw, sampled-huff, lz-probe). The LZ contribution
    uses the cached single-window probe instead of a full core encode --
    a full encode here cost more than the prediction saved. No alphabet/
    zero pre-gate: match richness is invisible to such tests and the
    probe itself is only a few ms.
    """
    sn = len(sample)
    if sn == 0:
        return 1.0
    cnt = Counter(sample)
    huff = _huff_size(sn, cnt) - 1  # drop mode byte; compare payload scales
    raw = sn
    best = min(raw, huff)
    if sn >= _MIN_LZ_LEN:
        probe = lz_probe(sample, min(sn, 16384))
        if probe < 1.0:
            best = min(best, probe * sn)
    factor = best / sn
    return min(1.0, factor)


def dict_gate(feats):
    """Cheap pre-mining hint. Heuristic conditions, individually tunable.

    NOTE: this is only a RANKING hint now, not a hard gate -- binary
    streams (e.g. delta-coded PRNG) can win big through DICT chains that
    no printable/rep-rate test would ever allow. Mining itself validates
    its own gain on a sample before committing.
    """
    return (feats.rep4 > 0.06 and
            (feats.printable_frac > 0.60 or feats.zero_frac > 0.5)) or \
        feats.zero_frac > 0.5 or feats.rep4 > 0.30


def mine_dictionary(data, ctx):
    """Mined (entries, escape) for data, shared between the predictor and
    the real expansion so mining never runs twice per distinct node."""
    key = ("dictmine", id(data))
    cached = ctx.probe_cache.get(key)
    if cached is not None:
        return cached[1]
    # small nodes were already mined in full by predict_dict (its sample
    # covers the whole input); reuse that result instead of re-mining.
    short = len(data) <= _DICT_SAMPLE
    if short:
        pre = ctx.probe_cache.get(("dictpred", id(data)))
        if pre is not None and pre[1] is not None:
            ctx.probe_cache[key] = (data, pre[1][2])
            return pre[1][2]
    from .dictionary import build_dictionary
    entries, escape = build_dictionary(data, full_freq=ctx.freq_table(data))
    ctx.probe_cache[key] = (data, (entries, escape))
    return entries, escape


def predict_dict(data, feats, ctx):
    n = feats.n
    # Mining runs at ~30-40 ms per call; below 16 KiB no plausible dict
    # gain repays it, and per-node mining dominated small-file encodes.
    if n < 16384 or not dict_gate(feats):
        return None
    key = ("dictpred", id(data))
    cached = ctx.probe_cache.get(key)
    if cached is not None:
        return cached[1]
    if not budget_hint(ctx):
        return None
    prefix = bytes(data[:_DICT_SAMPLE])
    # mine on the sample for the ESTIMATE (cheap); full mining happens in
    # mine_dictionary() when the option is actually expanded -- and for
    # small nodes the sample IS the whole input, so the result is shared.
    from .dictionary import build_dictionary
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
    result = (total, len(entries), (tuple(entries), escape))
    ctx.probe_cache[key] = (data, result)
    return result


def budget_hint(ctx):
    """True when the search-work budget still has room (or is disabled)."""
    lim = getattr(ctx, "work_limit", None)
    return lim is None or ctx.work_used < lim


def predict_all(data, feats, ctx, static_divide_pairs=(), expansions=True):
    preds = {
        "raw": predict_raw(feats.n),
        "huff": predict_huff(feats),
        "rle": predict_rle(feats),
        "lz": predict_lz(data, ctx),
    }
    if expansions:
        preds["delta"] = predict_delta(data, feats, ctx)
        d = predict_dict(data, feats, ctx)
        preds["dict"] = None if d is None else d[0]
        for est, pair in predict_divide_candidates(data, feats,
                                                   static_divide_pairs):
            preds[("divide",) + tuple(pair)] = est
    else:
        preds["delta"] = None
        preds["dict"] = None
    return preds
