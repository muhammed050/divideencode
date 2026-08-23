"""DE2 entropy coding (M2): canonical Huffman with a coder-agnostic seam.

- Static canonical Huffman, code lengths capped at MAX_CODE_LEN=15
  (frequency-halving recursion, same technique as V1 but tighter).
- Tables serialize as count + (symbol, length) pairs; completeness is
  verified on parse so invalid tables are rejected before decoding.
- Decoding is table-driven: one flat lookup over maxlen peeked bits maps
  straight to (symbol, length) -- no bit-at-a-time tree walking.
- Fast tables are cached by serialized-table signature inside a
  DecodeTables instance, so stable distributions across blocks reuse work.
- Bit accounting is exact: a decoded stream must consume every supplied
  byte up to the encoder's sub-byte padding (< 8 bits), otherwise the
  container is rejected.

encode_stream/decode_stream are the seam where rANS/FSE backends can be
swapped in later without touching the LZ parser.
"""
from collections import Counter

from ..errors import CorruptedError

MAX_CODE_LEN = 15


# ------------------------------------------------------------- varints ----

def _wvarint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _rvarint(buf, pos, end):
    result = 0
    shift = 0
    while True:
        if pos >= end:
            raise CorruptedError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 56:
            raise CorruptedError("varint overflow")


# ------------------------------------------------------------- encoder ----

def build_code_lengths(freq):
    items = [(c, s) for s, c in freq.items() if c > 0]
    if not items:
        return {}
    if len(items) == 1:
        return {items[0][1]: 1}
    import heapq
    heap = []
    tie = 0
    for c, s in items:
        heap.append((c, tie, [s]))
        tie += 1
    heapq.heapify(heap)
    depths = {s: 0 for _, s in items}
    while len(heap) > 1:
        c1, _, syms1 = heapq.heappop(heap)
        c2, _, syms2 = heapq.heappop(heap)
        for s in syms1:
            depths[s] += 1
        for s in syms2:
            depths[s] += 1
        tie += 1
        heapq.heappush(heap, (c1 + c2, tie, syms1 + syms2))
    if max(depths.values()) > MAX_CODE_LEN:
        scaled = {s: (c + 1) // 2 for s, c in freq.items() if c > 0}
        return build_code_lengths(scaled)
    return depths


def serialize_table(lengths):
    pairs = sorted(lengths.items(), key=lambda p: (p[1], p[0]))
    out = bytearray(_wvarint(len(pairs)))
    for sym, length in pairs:
        out.append(sym)
        out.append(length)
    return bytes(out)


def encode_stream(data):
    """bytes -> framed coded stream.

    Layout: flag u8 (0=raw stored, 1=huffman);
    huffman: table_len varint, table bytes, data_len varint, coded bytes.
    """
    n = len(data)
    if n == 0:
        return b"\x00"
    lengths = build_code_lengths(Counter(data))
    pairs = sorted(lengths.items(), key=lambda p: (p[1], p[0]))
    codes = {}
    code = 0
    prev_len = pairs[0][1]
    for sym, length in pairs:
        code <<= length - prev_len
        codes[sym] = (code, length)
        code += 1
        prev_len = length

    table_blob = serialize_table(lengths)
    buf = bytearray()
    cur = 0
    nbits = 0
    append = buf.append
    get = codes.__getitem__
    for b in data:
        c, l = get(b)
        cur = (cur << l) | c
        nbits += l
        while nbits >= 8:
            nbits -= 8
            append((cur >> nbits) & 0xFF)
        cur &= (1 << nbits) - 1
    if nbits:
        append((cur << (8 - nbits)) & 0xFF)

    # adaptive: keep raw storage when huffman does not pay for itself
    huff_size = 1 + len(_wvarint(len(table_blob))) + len(table_blob) \
        + len(_wvarint(len(buf))) + len(buf)
    if huff_size >= 1 + n:
        return b"\x00" + bytes(data)

    out = bytearray()
    out.append(1)
    out += _wvarint(len(table_blob))
    out += table_blob
    out += _wvarint(len(buf))
    out += buf
    return bytes(out)


# ------------------------------------------------------------- decoder ----

class DecodeTables:
    """Per-decompress-call cache mapping serialized table -> fast table."""

    def __init__(self):
        self.cache = {}

    def get(self, table_blob):
        entry = self.cache.get(table_blob)
        if entry is None:
            entry = _build_fast_table(table_blob)
            self.cache[table_blob] = entry
        return entry


def _parse_pairs(table_blob):
    end = len(table_blob)
    count, pos = _rvarint(table_blob, 0, end)
    if count <= 0 or count > 256 or pos + 2 * count > end:
        raise CorruptedError("invalid huffman table")
    pairs = []
    for _ in range(count):
        sym = table_blob[pos]
        length = table_blob[pos + 1]
        pos += 2
        if length < 1 or length > MAX_CODE_LEN:
            raise CorruptedError("invalid huffman code length")
        pairs.append((sym, length))
    maxlen = pairs[-1][1]
    if count > 1:
        acc = 0
        for _, l in pairs:
            acc += 1 << (maxlen - l)
        if acc != (1 << maxlen):
            raise CorruptedError("huffman table not complete")
    elif pairs[0][1] != 1:
        raise CorruptedError("single-symbol table must have code length 1")
    return pairs


def _build_fast_table(table_blob):
    pairs = _parse_pairs(table_blob)  # sorted ascending by length
    maxlen = pairs[-1][1]
    size = 1 << maxlen
    ftbl = [None] * size
    code = 0
    prev_len = pairs[0][1]
    for sym, length in pairs:
        code <<= length - prev_len
        width = 1 << (maxlen - length)
        lo = code * width
        item = (sym, length)
        for v in range(lo, lo + width):
            ftbl[v] = item
        code += 1
        prev_len = length
    mask = size - 1
    return ftbl, maxlen, mask


def decode_stream(blob, pos, end, expected_len, tables):
    """Inverse of encode_stream. Returns (data, new_pos)."""
    if expected_len == 0:
        if pos >= end:
            raise CorruptedError("entropy stream truncated")
        flag = blob[pos]
        if flag != 0:
            raise CorruptedError("unexpected non-empty entropy stream")
        return b"", pos + 1
    if pos >= end:
        raise CorruptedError("entropy stream truncated")
    flag = blob[pos]
    pos += 1
    if flag == 0:
        if pos + expected_len > end:
            raise CorruptedError("raw entropy stream truncated")
        return bytes(blob[pos:pos + expected_len]), pos + expected_len
    if flag != 1:
        raise CorruptedError("unknown entropy stream type %d" % flag)

    tlen, pos = _rvarint(blob, pos, end)
    if tlen > end - pos:
        raise CorruptedError("huffman table truncated")
    table_blob = bytes(blob[pos:pos + tlen])
    pos += tlen
    dlen, pos = _rvarint(blob, pos, end)
    if dlen > end - pos:
        raise CorruptedError("huffman payload truncated")

    ftbl, maxlen, mask = tables.get(table_blob)
    data_end = pos + dlen
    limit_bits = 8 * dlen

    out = bytearray()
    append = out.append
    acc = 0
    nbits = 0
    p = pos
    remaining = expected_len
    consumed = 0
    while remaining:
        while nbits < maxlen and p < data_end:
            acc = (acc << 8) | blob[p]
            p += 1
            nbits += 8
        if nbits >= maxlen:
            idx = (acc >> (nbits - maxlen)) & mask
        else:
            # end of input: zero-pad the peek window
            idx = (acc << (maxlen - nbits)) & mask
        sym, l = ftbl[idx]
        if l > nbits:
            raise CorruptedError("truncated huffman code")
        consumed += l
        if consumed > limit_bits:
            raise CorruptedError("huffman overrun")
        append(sym)
        remaining -= 1
        nbits -= l
        acc &= (1 << nbits) - 1
    if limit_bits - consumed >= 8:
        raise CorruptedError("excess data after huffman stream")
    return bytes(out), data_end
