"""DivideEncode V2 -- LZ + split-stream Huffman codec (M2/M3).

Payload layout inside the DE2 container (after build_header()):

  method byte: 0 = STORED raw bytes
               1 = LZE  (token streams below)
               2 = DELTA+LZE   payload is LZE(delta(data))
               3 = RLE+LZE     [varint rle_len] + LZE(rle_blob)
               4 = STRUCT+LZE  payload is LZE(struct_pack(data))

  LZE: [varint len(main_bytes)]
       [tbl_main]                       canonical Huffman, symbols:
                                          0..255   literal bytes
                                          256..284 match lengths (deflate
                                                     bases shifted: internal
                                                     length = len - 1)
       [tbl_dist]                       canonical Huffman, symbols:
                                          0        rep0
                                          1        rep1
                                          2..N     explicit distance codes
                                                     (deflate doubling rule,
                                                     extended to the window)
       [main_bytes]                     lit/length stream; length extra
                                         bits ride inline here
       [dist_bytes]                     distance stream; distance extra
                                         bits ride inline here

Deterministic: same input -> same container, always.
"""
import zlib

from .. import huffman as H
from .. import patterns as P
from ..bitstream import BitWriter, BitReader, encode_varint, decode_varint
from ..errors import CorruptedError
from .lz import tokenize, MIN_MATCH, DEFAULT_MAX_MATCH, DEFAULT_WINDOW, \
    LIT, MATCH, REP
from .container import build_header, parse_header
from .preproc import (delta_bytes, undelta_bytes,
                      struct_pack, struct_unpack,
                      plane_pack, plane_unpack,
                      plane_delta_pack, plane_delta_unpack,
                      word_split, word_join, pack_bits, unpack_bits,
                      plan_candidates, STRUCT_TEXT, WORD_DEFAULT)

METHOD_STORED = 0
METHOD_LZE = 1
METHOD_DELTA_LZE = 2
METHOD_RLE_LZE = 3
METHOD_STRUCT_LZE = 4
METHOD_WORD_LZE = 5
METHOD_PLANE_DELTA_LZE = 6
METHOD_HUF = 7

# ---- length codes (internal length = match length - 1, i.e. 3..258) ------
_LEN_BASE = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35,
             43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258)
_LEN_EXTRA = (0,) * 8 + (1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3,
                         4, 4, 4, 4, 5, 5, 5, 5, 0)

# sym -> (base_length, extra_bits); symbol value = 256 + index
LEN_SYMS = [(256 + i, _LEN_BASE[i], _LEN_EXTRA[i]) for i in range(29)]

# match length (4..259) -> (sym, code_len_of_extra, extra_value)
_LEN_MAP = {}
for _i in range(28):
    _lo, _hi = _LEN_BASE[_i], _LEN_BASE[_i + 1] - 1
    for _v in range(_lo, _hi + 1):
        _LEN_MAP[_v] = (256 + _i, _LEN_EXTRA[_i], _v - _lo)
_LEN_MAP[258] = (256 + 28, 0, 0)

# ---- distance codes -------------------------------------------------------
_DIST_BASE = [1, 2, 3, 4]
_DIST_EXTRA = [0, 0, 0, 0]
_b, _e = 5, 1
while _b <= DEFAULT_WINDOW and len(_DIST_BASE) < 64:
    _DIST_BASE.append(_b)
    _DIST_EXTRA.append(_e)
    _b += 1 << _e
    if _b > DEFAULT_WINDOW:
        break
    _DIST_BASE.append(_b)
    _DIST_EXTRA.append(_e)
    _b += 1 << _e
    _e += 1

REP0_SYM = 0
REP1_SYM = 1


def _dist_code(dist):
    """distance -> (sym, extra_value, extra_bits)."""
    lo, hi = 0, len(_DIST_BASE) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _DIST_BASE[mid] <= dist:
            lo = mid
        else:
            hi = mid - 1
    return 2 + lo, dist - _DIST_BASE[lo], _DIST_EXTRA[lo]


# ---- encoding --------------------------------------------------------------

def _build_codes(freq):
    lengths = H.build_code_lengths(freq)
    pairs = list(lengths.items())
    if not pairs:
        return {}, None
    codes = H.canonical_codes(pairs)
    dec = {(l, c): s for s, (c, l) in codes.items()}
    return codes, (_serialize_table(pairs), dec)


def _serialize_table(pairs):
    """Varint-symbol table (length symbols exceed one byte)."""
    pairs = sorted(pairs, key=lambda p: (p[1], p[0]))
    out = bytearray(encode_varint(len(pairs)))
    for sym, length in pairs:
        out += encode_varint(sym)
        out.append(length)
    return bytes(out)


def _parse_table(buf, pos, end):
    count, pos = decode_varint(buf, pos, end)
    if not 0 < count < 1024:
        raise CorruptedError("invalid DE2 huffman table")
    pairs = []
    for _ in range(count):
        if pos >= end:
            raise CorruptedError("DE2 huffman table truncated")
        sym, pos = decode_varint(buf, pos, end)
        length = buf[pos]
        pos += 1
        if not 1 <= length <= 32:
            raise CorruptedError("invalid DE2 code length")
        pairs.append((sym, length))
    if count > 1:
        max_len = max(l for _, l in pairs)
        total = sum(1 << (max_len - l) for _, l in pairs)
        if total != (1 << max_len):
            raise CorruptedError("DE2 huffman table incomplete")
    return pairs, pos


def _lze_core(data):
    """LZE streams WITHOUT the method byte; None when LZE cannot apply."""
    tokens = tokenize(data)
    if not tokens:
        return None

    # frequency / plan pass
    main_freq = {}
    dist_freq = {}
    plan = []
    for tok in tokens:
        kind = tok[0]
        if kind == LIT:
            sym = tok[1]
            main_freq[sym] = main_freq.get(sym, 0) + 1
            plan.append((sym, 0, 0, 0))
        elif kind == MATCH:
            sym, nx, ev = _LEN_MAP[tok[1] - 1]
            main_freq[sym] = main_freq.get(sym, 0) + 1
            dsym, dev, dnx = _dist_code(tok[2])
            dist_freq[dsym] = dist_freq.get(dsym, 0) + 1
            plan.append((sym, nx, ev, (dsym, dev, dnx)))
        else:  # REP
            sym, nx, ev = _LEN_MAP[tok[1] - 1]
            main_freq[sym] = main_freq.get(sym, 0) + 1
            dist_freq[tok[2]] = dist_freq.get(tok[2], 0) + 1
            plan.append((sym, nx, ev, tok[2]))

    m_codes, m_td = _build_codes(main_freq)
    d_codes, d_td = _build_codes(dist_freq)
    if m_td is None or d_td is None:
        # no matches at all -> nothing for a distance table to describe
        return None
    m_table, _ = m_td
    d_table, _ = d_td

    mw = BitWriter()
    dw = BitWriter()
    for sym, nx, ev, extra in plan:
        code, ln = m_codes[sym]
        mw.write_bits(code, ln)
        if sym >= 256 and nx:
            mw.write_bits(ev, nx)
        if extra.__class__ is int and sym >= 256:
            # REP: distance symbol only
            dcode, dln = d_codes[extra]
            dw.write_bits(dcode, dln)
        elif sym >= 256:
            dsym, dev, dnx = extra
            dcode, dln = d_codes[dsym]
            dw.write_bits(dcode, dln)
            if dnx:
                dw.write_bits(dev, dnx)

    main_bytes = mw.getvalue()
    dist_bytes = dw.getvalue()
    out = bytearray()
    out += encode_varint(len(main_bytes))
    out += m_table
    out += d_table
    out += main_bytes
    out += dist_bytes
    return bytes(out)


def _huf_blob(data):
    """Order-0 canonical Huffman over raw bytes -> (est_len, blob_fn).

    blob_fn materialises the [table][bits] body only when the estimate
    wins, keeping the common LZE path free of extra bit-writing work."""
    from collections import Counter
    freq = Counter(data)
    lengths = H.build_code_lengths(freq)
    table = _serialize_table(lengths.items())
    est_bits = 0
    for sym, cnt in freq.items():
        est_bits += cnt * lengths[sym]
    est = len(table) + ((est_bits + 7) >> 3)

    def build():
        codes = H.canonical_codes(list(lengths.items()))
        bw = BitWriter()
        wb = bw.write_bits
        for b in data:
            c, l = codes[b]
            wb(c, l)
        return table + bw.getvalue()

    return est, build


def encode_payload(data):
    """Compress raw bytes into a method-prefixed payload (LZE / HUF /
    STORED -- whichever measures smallest)."""
    if not data:
        return bytes((METHOD_STORED,))
    stored = None
    core = _lze_core(data)
    if core is None:
        # no matches: pure literal stream; order-0 Huffman often wins big
        est, build = _huf_blob(data)
        stored = bytes((METHOD_STORED,)) + data
        if est + 1 < len(stored):
            return bytes((METHOD_HUF,)) + build()
        return stored

    best = bytes((METHOD_LZE,)) + core
    # order-0 Huffman alternative: cheap to estimate (Counter is C-speed),
    # and it beats LZE on literal-heavy low-entropy streams where LZ
    # matching pays more in symbols than it saves.
    est, build = _huf_blob(data)
    if est + 1 < len(best):
        best = bytes((METHOD_HUF,)) + build()
    if len(best) > 1 + len(data):
        best = bytes((METHOD_STORED,)) + data
    return best


def decode_payload(payload, expected_len, pos, end):
    method = payload[pos]
    pos += 1
    if method == METHOD_STORED:
        if pos + expected_len > end:
            raise CorruptedError("stored block truncated")
        out_end = pos + expected_len
        if out_end != end:
            raise CorruptedError("trailing garbage in DE2 payload")
        return bytes(payload[pos:out_end]), out_end
    if method == METHOD_DELTA_LZE:
        inner, inner_end = decode_payload(payload, expected_len, pos, end)
        return undelta_bytes(inner), inner_end
    if method == METHOD_RLE_LZE:
        slen, pos = decode_varint(payload, pos, end)
        stream, stream_end = decode_payload(payload, slen, pos, end)
        out, rpos = P.rle_decode(stream, 0, len(stream), expected_len)
        if rpos != len(stream):
            raise CorruptedError("trailing garbage in RLE stream")
        if stream_end != end:
            raise CorruptedError("trailing garbage in DE2 payload")
        return bytes(out), end
    if method == METHOD_STRUCT_LZE:
        k = payload[pos] if payload[pos] else 1
        pos += 1
        if k == 1:
            packed_len = ((expected_len + 7) // 8) + expected_len
            packed, packed_end = decode_payload(payload, packed_len, pos, end)
            return struct_unpack(packed, expected_len), packed_end
        nw_total = (expected_len // k) * k + expected_len % k
        packed, packed_end = decode_payload(payload, nw_total, pos, end)
        return plane_unpack(packed, expected_len, k), packed_end
    if method == METHOD_PLANE_DELTA_LZE:
        k = payload[pos]
        pos += 1
        total = (expected_len // k) * k + expected_len % k
        packed, packed_end = decode_payload(payload, total, pos, end)
        return plane_delta_unpack(packed, expected_len, k), packed_end
    if method == METHOD_WORD_LZE:
        w = payload[pos]
        s = payload[pos + 1]
        wq = payload[pos + 2]
        flags = payload[pos + 3]
        pos += 4
        qlen, pos = decode_varint(payload, pos, end)
        len_q, pos = decode_varint(payload, pos, end)
        q_end = pos + len_q
        q_inner, _ = decode_payload(payload, qlen, pos, q_end)
        q_bytes = undelta_bytes(q_inner) if flags & 0x01 else q_inner
        pos = q_end
        len_r, pos = decode_varint(payload, pos, end)
        r_end = pos + len_r
        nw = expected_len // w
        r_packed, _ = decode_payload(payload, (nw * s + 7) // 8, pos, r_end)
        pos = r_end
        tail = bytes(payload[pos:end])
        r_values = unpack_bits(r_packed, nw, s)
        return word_join(q_bytes, r_values, tail, w, s, wq,
                         expected_len), end
    if method == METHOD_HUF:
        pairs, pos = _parse_table(payload, pos, end)
        dec = _dec_map(pairs)
        mr = BitReader(payload, pos, end)
        out = bytearray()
        append = out.append
        for _ in range(expected_len):
            append(_read_sym(mr, dec))
        if mr.pos != end:
            raise CorruptedError("trailing bits in HUF payload")
        return bytes(out), end
    if method != METHOD_LZE:
        raise CorruptedError("unknown DE2 method %d" % method)

    main_len, pos = decode_varint(payload, pos, end)
    m_pairs, pos = _parse_table(payload, pos, end)
    d_pairs, pos = _parse_table(payload, pos, end)
    if pos + main_len > end:
        raise CorruptedError("DE2 main stream truncated")
    main_end = pos + main_len
    m_dec = _dec_map(m_pairs)
    d_dec = _dec_map(d_pairs)

    mr = BitReader(payload, pos, main_end)
    dr = BitReader(payload, main_end, end)
    out = bytearray()
    reps = []
    append = out.append
    while len(out) < expected_len:
        sym = _read_sym(mr, m_dec)
        if sym < 256:
            append(sym)
            continue
        idx = sym - 256
        length = _LEN_BASE[idx]
        nx = _LEN_EXTRA[idx]
        if nx:
            length += mr.read_bits(nx)
        # internal length codes are shifted by one (MIN_MATCH == 4):
        # encoded value = match length - 1
        length += 1
        dsym = _read_sym(dr, d_dec)
        if dsym < 2:
            if dsym >= len(reps):
                raise CorruptedError("rep offset unavailable")
            dist = reps[dsym]
            if dsym:
                reps[0], reps[1] = reps[1], reps[0]
        else:
            di = dsym - 2
            dist = _DIST_BASE[di]
            dnx = _DIST_EXTRA[di]
            if dnx:
                dist += dr.read_bits(dnx)
            if dist > len(out):
                raise CorruptedError("distance beyond output")
            if dist in reps:
                j = reps.index(dist)
                if j:
                    reps[0], reps[j] = reps[j], reps[0]
            else:
                reps.insert(0, dist)
                if len(reps) > 2:
                    reps.pop()
        src = len(out) - dist
        for k in range(length):
            append(out[src + k])
    if len(out) != expected_len:
        raise CorruptedError("DE2 stream wrong size")
    if mr.pos != main_end or dr.pos != end:
        raise CorruptedError("trailing garbage in DE2 payload")
    return bytes(out), end


def _dec_map(table_pairs):
    """table pairs [(sym,length)] -> {(length, code): sym}."""
    pairs = sorted(table_pairs, key=lambda p: (p[1], p[0]))
    dec = {}
    code = 0
    prev = pairs[0][1]
    for sym, ln in pairs:
        code <<= ln - prev
        dec[(ln, code)] = sym
        code += 1
        prev = ln
    return dec


def _read_sym(reader, dec):
    code = 0
    ln = 0
    while True:
        code = (code << 1) | reader.read_bit()
        ln += 1
        if ln > 32:
            raise CorruptedError("invalid huffman code")
        sym = dec.get((ln, code))
        if sym is not None:
            return sym


# ---- container API ----------------------------------------------------------

def _method_payload(data, method, param=None):
    """Build one candidate payload; None when the method cannot apply."""
    if method == METHOD_STORED:
        return bytes((METHOD_STORED,)) + data
    if method == METHOD_LZE:
        return encode_payload(data)
    if method == METHOD_DELTA_LZE:
        return bytes((METHOD_DELTA_LZE,)) + encode_payload(delta_bytes(data))
    if method == METHOD_RLE_LZE:
        rle = P.rle_encode(data)
        if rle is None:
            return None
        return bytes((METHOD_RLE_LZE,)) + encode_varint(len(rle)) + \
            encode_payload(rle)
    if method == METHOD_STRUCT_LZE:
        k = param if param else 1
        if k == STRUCT_TEXT:
            packed = struct_pack(data)
        else:
            packed = plane_pack(data, k)
        return bytes((METHOD_STRUCT_LZE,)) + bytes((k,)) + \
            encode_payload(packed)
    if method == METHOD_PLANE_DELTA_LZE:
        return bytes((METHOD_PLANE_DELTA_LZE,)) + bytes((param,)) + \
            encode_payload(plane_delta_pack(data, param))
    if method == METHOD_WORD_LZE:
        w, s = param if param else WORD_DEFAULT
        q_bytes, r_values, tail, wq = word_split(data, w, s)
        # quotient streams drift slowly; mod-256 delta makes them trivially
        # compressible (flagged so the decoder undoes it)
        inner_q = encode_payload(delta_bytes(q_bytes))
        r_packed = pack_bits(r_values, s) if r_values else b""
        inner_r = encode_payload(r_packed)
        out = bytearray()
        out.append(METHOD_WORD_LZE)
        out += bytes((w, s, wq))
        out.append(0x01)                       # flags: bit0 = q is delta'd
        out += encode_varint(len(q_bytes))
        out += encode_varint(len(inner_q)) + inner_q
        out += encode_varint(len(inner_r)) + inner_r
        out += tail
        return bytes(out)
    raise ValueError("unknown method %r" % (method,))


def compress(data):
    if isinstance(data, bytearray):
        data = bytes(data)
    elif isinstance(data, memoryview):
        data = bytes(data)
    elif not isinstance(data, bytes):
        raise TypeError("compress expects bytes-like data")
    # classifier shortlist -> exact measurement of each fast candidate;
    # every candidate is a single deterministic pipeline (no search tree).
    best = None
    for method, param in plan_candidates(data):
        payload = _method_payload(data, method, param)
        if payload is not None and \
                (best is None or len(payload) < len(best)):
            best = payload
    stored = bytes((METHOD_STORED,)) + data
    if best is None or len(best) > len(stored):
        best = stored
    return build_header(len(data), zlib.crc32(data)) + best


def decompress(buf):
    orig_len, crc, pos = parse_header(buf)
    buf = bytes(buf)
    end = len(buf)
    out, _pos = decode_payload(buf, orig_len, pos, end)
    if len(out) != orig_len:
        raise CorruptedError("DE2 size mismatch")
    if zlib.crc32(out) != crc:
        raise CorruptedError("DE2 crc mismatch")
    return out


__all__ = ["compress", "decompress", "encode_payload", "decode_payload",
           "METHOD_STORED", "METHOD_LZE", "METHOD_DELTA_LZE",
           "METHOD_RLE_LZE", "METHOD_STRUCT_LZE"]
