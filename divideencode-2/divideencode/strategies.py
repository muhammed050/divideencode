from . import huffman
from .bitstream import encode_varint, decode_varint
from .dictionary import build_dictionary, substitute
from .dictionary import expand as dict_expand
from .divide_transform import (DIVISORS, WORD_SIZES, divide_transform,
                               divide_reconstruct, estimate_candidates)
from .errors import CorruptedError
from .features import EncodeContext
from .patterns import rle_encode, rle_decode, lz_encode, lz_decode
from . import scoring

MODE_RAW = 0
MODE_DELTA = 1
MODE_RLE = 2
MODE_DICT = 3
MODE_DIVIDE = 4
MODE_HUFF = 5
MODE_LZ = 6

MODE_NAMES = {
    MODE_RAW: "RAW",
    MODE_DELTA: "DELTA",
    MODE_RLE: "RLE",
    MODE_DICT: "DICT",
    MODE_DIVIDE: "DIVIDE",
    MODE_HUFF: "HUFFMAN",
    MODE_LZ: "LZ",
}

MIN_LZ_LEN = 64
MIN_DICT_LEN = 1024
MIN_DIVIDE_LEN = 32
DEFAULT_DEPTH = 3

# --- search configuration (P3) -------------------------------------------
# SEARCH_MODE 'beam': feature-ranked pruning at every node.
# SEARCH_MODE 'exhaustive': legacy behaviour, every strategy everywhere.
SEARCH_MODE = "beam"
BEAM_K = 3          # transform expansions per node (beam width)
LEAF_K = 2          # non-raw leaf encoders fully evaluated per node
PRUNE_MARGIN = 1.05  # heuristic margin: prune only if pred > best * margin
# --------------------------------------------------------------------------

MAX_DICT_ENTRIES = 1020
MAX_SUB_LEN = 1 << 31

_WCODE = {w: i for i, w in enumerate(WORD_SIZES)}

LEAF_MODE = {"rle": MODE_RLE, "huff": MODE_HUFF, "lz": MODE_LZ}


def set_search_mode(mode="beam", k=None):
    """Configure the search engine. 'exhaustive' restores the legacy
    exhaustive search; 'beam' enables ranked pruning."""
    global SEARCH_MODE, BEAM_K
    if mode not in ("beam", "exhaustive"):
        raise ValueError("unknown search mode %r" % (mode,))
    SEARCH_MODE = mode
    if k is not None:
        if not 1 <= int(k) <= 8:
            raise ValueError("beam width must be in 1..8")
        BEAM_K = int(k)


def _delta_encode(data):
    out = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        out[i] = (b - prev) & 0xFF
        prev = b
    return bytes(out)


def _delta_decode(data):
    out = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        prev = (prev + b) & 0xFF
        out[i] = prev
    return bytes(out)


def encode_node(data, depth=DEFAULT_DEPTH, ctx=None):
    if ctx is None:
        ctx = EncodeContext()
    memo_key = (id(data), depth)
    hit = ctx.memo_get(memo_key)
    if hit is not None:
        return hit
    best = None
    exhaustive = SEARCH_MODE == "exhaustive"

    feats = ctx.features(data)
    static_pairs = estimate_candidates(data) if data else []
    preds = scoring.predict_all(data, feats, ctx, static_pairs)

    def record(strategy, blob_len):
        if ctx.debug and strategy in preds and preds[strategy] is not None:
            ctx.stats["predictions"].append({
                "strategy": str(strategy),
                "predicted": round(preds[strategy], 1),
                "actual": blob_len,
                "error": round(blob_len - preds[strategy], 1),
            })

    def pruned(strategy, why):
        if ctx.debug:
            ctx.stats["pruned"].append({"strategy": str(strategy),
                                        "why": why})

    def consider(blob):
        nonlocal best
        if blob is None:
            return
        if best is None or len(blob) < len(best):
            best = blob

    # RAW first: exact safe upper bound for all later pruning decisions.
    consider(bytes((MODE_RAW,)) + bytes(data))
    if ctx.debug:
        record("raw", 1 + len(data))

    if data:
        n = len(data)

        # ---------- leaf strategies ----------
        leaf_runners = {
            "rle": lambda: rle_encode(data),
            "huff": lambda: huffman.encode(data, freq=ctx.freq_table(data)),
            "lz": (lambda: lz_encode(data)) if n >= MIN_LZ_LEN else None,
        }
        if exhaustive:
            for name in ("rle", "huff", "lz"):
                runner = leaf_runners[name]
                if runner is None:
                    continue
                blob = runner()
                if blob is not None:
                    consider(bytes((LEAF_MODE[name],)) + blob)
                    record(name, 1 + len(blob))
        else:
            ranked = sorted(
                (name for name in ("rle", "huff", "lz")
                 if leaf_runners[name] is not None),
                key=lambda nm: (preds.get(nm) if preds.get(nm) is not None
                                else float("inf")))
            ran = 0
            for name in ranked:
                pred = preds.get(name)
                if ran >= LEAF_K:
                    break
                if pred is None or pred > len(best) * PRUNE_MARGIN:
                    if pred is None:
                        pruned(name, "no-prediction")
                    else:
                        pruned(name, "over-margin")
                    continue
                ran += 1
                blob = leaf_runners[name]()
                if blob is not None:
                    consider(bytes((LEAF_MODE[name],)) + blob)
                    record(name, 1 + len(blob))

        # ---------- transforms ----------
        if depth > 0:

            def expand_delta():
                dblob = encode_node(_delta_encode(data), depth - 1, ctx)
                return bytes((MODE_DELTA,)) + dblob

            def expand_divide(w, d):
                info = divide_transform(data, w, d)
                qnode = encode_node(info["quotient_stream"], depth - 1, ctx)
                head_len = 4 + len(info["tail"]) + len(qnode) + 1
                if not exhaustive and best is not None and \
                        head_len > len(best):
                    # safe cut: any encoded remainder node is >= 1 byte,
                    # so this candidate can no longer win.
                    return None
                rnode = encode_node(info["remainder_stream"], depth - 1, ctx)
                head = bytearray()
                head.append(MODE_DIVIDE)
                head.append(_WCODE[w])
                head.append(DIVISORS.index(d))
                head.append(info["wq"])
                head += info["tail"]
                head += qnode
                head += rnode
                return bytes(head)

            def expand_dict():
                entries, escape = build_dictionary(
                    data, full_freq=ctx.freq_table(data))
                if not entries:
                    return None
                sub = substitute(data, entries, escape)
                inner = encode_node(sub, depth - 1, ctx)
                body = bytearray()
                body.append(escape)
                body += encode_varint(len(entries))
                for e in entries:
                    body += encode_varint(len(e))
                    body += e
                body += encode_varint(len(sub))
                body += inner
                return bytes([MODE_DICT]) + bytes(body)

            if exhaustive:
                dblob = expand_delta()
                consider(dblob)
                record("delta", len(dblob))
                if n >= MIN_DIVIDE_LEN:
                    for w, d in static_pairs:
                        blob = expand_divide(w, d)
                        if blob is not None:
                            consider(blob)
                            record(("divide", w, d), len(blob))
                if n >= MIN_DICT_LEN:
                    blob = expand_dict()
                    if blob is not None:
                        consider(blob)
                        record("dict", len(blob))
            else:
                options = [("delta", preds.get("delta"), expand_delta, ())]
                if n >= MIN_DIVIDE_LEN:
                    for w, d in static_pairs:
                        options.append((
                            ("divide", w, d),
                            preds.get(("divide", w, d)),
                            expand_divide, (w, d)))
                if n >= MIN_DICT_LEN:
                    options.append(("dict", preds.get("dict"),
                                    expand_dict, ()))

                def opt_key(o):
                    return o[1] if o[1] is not None else float("inf")

                options.sort(key=opt_key)
                expanded = 0
                for key, pred, fn, args in options:
                    if expanded >= BEAM_K:
                        break
                    if pred is None or pred > len(best) * PRUNE_MARGIN:
                        if pred is None:
                            pruned(key, "no-prediction")
                        else:
                            pruned(key, "over-margin")
                        break  # sorted ascending: everything after is worse
                    expanded += 1
                    blob = fn(*args)
                    if blob is not None:
                        consider(blob)
                        record(key, len(blob))
                    else:
                        pruned(key, "empty")

    ctx.memo_put(memo_key, data, best)
    return best


def decode_node(buf, pos, end, expected_len, trace=None):
    if pos >= end:
        raise CorruptedError("unexpected end of stream")
    mode = buf[pos]
    pos += 1

    def finish(extra=None, children=None):
        if trace is not None:
            node = {
                "mode": MODE_NAMES.get(mode, str(mode)),
                "out_size": expected_len,
                "children": children if children is not None else [],
            }
            if extra:
                node.update(extra)
            trace.append(node)

    if mode == MODE_RAW:
        if expected_len and pos + expected_len > end:
            raise CorruptedError("raw block truncated")
        data = bytes(buf[pos:pos + expected_len])
        finish()
        return data, pos + expected_len

    if mode == MODE_DELTA:
        kids = []
        raw, pos = decode_node(buf, pos, end, expected_len,
                               kids if trace is not None else None)
        finish(children=kids)
        return _delta_decode(raw), pos

    if mode == MODE_RLE:
        data, pos = rle_decode(buf, pos, end, expected_len)
        finish()
        return data, pos

    if mode == MODE_HUFF:
        data, pos = huffman.decode(buf, pos, end, expected_len)
        finish()
        return data, pos

    if mode == MODE_LZ:
        data, pos = lz_decode(buf, pos, end, expected_len)
        finish()
        return data, pos

    if mode == MODE_DIVIDE:
        if pos + 3 > end:
            raise CorruptedError("divide header truncated")
        wcode = buf[pos]
        did = buf[pos + 1]
        wq = buf[pos + 2]
        pos += 3
        if wcode >= len(WORD_SIZES):
            raise CorruptedError("invalid word size code")
        if did >= len(DIVISORS):
            raise CorruptedError("invalid divisor id")
        if not 1 <= wq <= 8:
            raise CorruptedError("invalid quotient width")
        w = WORD_SIZES[wcode]
        d = DIVISORS[did]
        n_words = expected_len // w
        tail_len = expected_len % w
        if pos + tail_len > end:
            raise CorruptedError("divide tail truncated")
        tail = bytes(buf[pos:pos + tail_len])
        pos += tail_len
        rbits = (d - 1).bit_length()
        q_expected = n_words * wq
        r_expected = (n_words * rbits + 7) // 8
        q_kids, r_kids = [], []
        tr = trace is not None
        q_bytes, pos = decode_node(buf, pos, end, q_expected, q_kids if tr else None)
        r_bytes, pos = decode_node(buf, pos, end, r_expected, r_kids if tr else None)
        data = divide_reconstruct(q_bytes, r_bytes, w, d, wq, rbits,
                                  n_words, tail)
        finish({"word_size": w, "divisor": d},
               children=(q_kids + r_kids) if tr else None)
        return data, pos

    if mode == MODE_DICT:
        if pos >= end:
            raise CorruptedError("dict header truncated")
        escape = buf[pos]
        pos += 1
        count, pos = decode_varint(buf, pos, end)
        if count > MAX_DICT_ENTRIES:
            raise CorruptedError("too many dict entries")
        entries = []
        for _ in range(count):
            elen, pos = decode_varint(buf, pos, end)
            if elen > end - pos:
                raise CorruptedError("dict entry truncated")
            entries.append(bytes(buf[pos:pos + elen]))
            pos += elen
        sub_len, pos = decode_varint(buf, pos, end)
        if sub_len > MAX_SUB_LEN:
            raise CorruptedError("substituted stream too large")
        s_kids = []
        sub, pos = decode_node(buf, pos, end, sub_len,
                               s_kids if trace is not None else None)
        data = dict_expand(sub, entries, escape, expected_len)
        finish({"entries": len(entries)},
               children=s_kids if trace is not None else None)
        return data, pos

    raise CorruptedError("unknown block mode %d" % mode)


def render_trace(trace, indent=0):
    lines = []
    for node in trace:
        pad = "  " * indent
        bits = []
        for key, label in (("word_size", "w"), ("divisor", "d"), ("entries", "n")):
            if key in node:
                bits.append("%s=%s" % (label, node[key]))
        suffix = " (" + ", ".join(bits) + ")" if bits else ""
        lines.append("%s%s%s -> %d B" % (pad, node["mode"], suffix,
                                         node["out_size"]))
        lines.extend(render_trace(node.get("children", ()), indent + 1))
    return "\n".join(lines)
