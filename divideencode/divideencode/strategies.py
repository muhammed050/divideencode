from . import huffman
from .bitstream import encode_varint, decode_varint
from .dictionary import build_dictionary, substitute
from .dictionary import expand as dict_expand
from .divide_transform import (DIVISORS, WORD_SIZES, divide_transform,
                               divide_reconstruct, estimate_candidates)
from .errors import CorruptedError
from .patterns import rle_encode, rle_decode, lz_encode, lz_decode

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

MAX_DICT_ENTRIES = 1020
MAX_SUB_LEN = 1 << 31

_WCODE = {w: i for i, w in enumerate(WORD_SIZES)}


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


def encode_node(data, depth=DEFAULT_DEPTH):
    best = None

    def consider(blob):
        nonlocal best
        if blob is None:
            return
        if best is None or len(blob) < len(best):
            best = blob

    consider(bytes((MODE_RAW,)) + bytes(data))

    if data:
        blob = rle_encode(data)
        if blob is not None:
            consider(bytes((MODE_RLE,)) + blob)
        blob = huffman.encode(data)
        if blob is not None:
            consider(bytes((MODE_HUFF,)) + blob)
        if len(data) >= MIN_LZ_LEN:
            blob = lz_encode(data)
            if blob is not None:
                consider(bytes((MODE_LZ,)) + blob)

        if depth > 0:
            consider(bytes((MODE_DELTA,)) + encode_node(_delta_encode(data), depth - 1))

            if len(data) >= MIN_DIVIDE_LEN:
                for w, d in estimate_candidates(data):
                    info = divide_transform(data, w, d)
                    qnode = encode_node(info["quotient_stream"], depth - 1)
                    rnode = encode_node(info["remainder_stream"], depth - 1)
                    head = bytearray()
                    head.append(MODE_DIVIDE)
                    head.append(_WCODE[w])
                    head.append(DIVISORS.index(d))
                    head.append(info["wq"])
                    head += info["tail"]
                    head += qnode
                    head += rnode
                    consider(bytes(head))

            if len(data) >= MIN_DICT_LEN:
                entries, escape = build_dictionary(data)
                if entries:
                    sub = substitute(data, entries, escape)
                    inner = encode_node(sub, depth - 1)
                    body = bytearray()
                    body.append(escape)
                    body += encode_varint(len(entries))
                    for e in entries:
                        body += encode_varint(len(e))
                        body += e
                    body += encode_varint(len(sub))
                    body += inner
                    consider(bytes([MODE_DICT]) + bytes(body))

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
