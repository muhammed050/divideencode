import heapq
import math
from collections import Counter

from .bitstream import BitWriter, BitReader, encode_varint, decode_varint
from .errors import CorruptedError

MAX_CODE_LEN = 32
_ENTROPY_GATE_MIN = 8192


def build_code_lengths(freq):
    items = [(f, s) for s, f in freq.items() if f > 0]
    if not items:
        return {}
    if len(items) == 1:
        return {items[0][1]: 1}
    heap = []
    tie = 0
    for f, s in items:
        heap.append((f, tie, [s]))
        tie += 1
    heapq.heapify(heap)
    depths = {s: 0 for _, s in items}
    while len(heap) > 1:
        f1, _, syms1 = heapq.heappop(heap)
        f2, _, syms2 = heapq.heappop(heap)
        for s in syms1:
            depths[s] += 1
        for s in syms2:
            depths[s] += 1
        tie += 1
        heapq.heappush(heap, (f1 + f2, tie, syms1 + syms2))
    if max(depths.values()) > MAX_CODE_LEN:
        scaled = {s: (f + 1) // 2 for s, f in freq.items() if f > 0}
        return build_code_lengths(scaled)
    return depths


def canonical_codes(pairs):
    pairs = sorted(pairs, key=lambda p: (p[1], p[0]))
    codes = {}
    code = 0
    prev_len = pairs[0][1]
    for sym, length in pairs:
        code <<= length - prev_len
        codes[sym] = (code, length)
        code += 1
        prev_len = length
    return codes


def serialize_table(lengths):
    pairs = sorted(lengths.items(), key=lambda p: (p[1], p[0]))
    out = bytearray(encode_varint(len(pairs)))
    for sym, length in pairs:
        out.append(sym)
        out.append(length)
    return bytes(out)


def parse_table(buf, pos, end):
    count, pos = decode_varint(buf, pos, end)
    if count <= 0 or count > 256 or pos + 2 * count > end:
        raise CorruptedError("invalid huffman table")
    pairs = []
    for _ in range(count):
        sym = buf[pos]
        length = buf[pos + 1]
        pos += 2
        if length < 1 or length > MAX_CODE_LEN:
            raise CorruptedError("invalid code length")
        pairs.append((sym, length))
    total = 0
    max_len = max(l for _, l in pairs)
    if count > 1:
        for _, l in pairs:
            total += 1 << (max_len - l)
        if total != (1 << max_len):
            raise CorruptedError("huffman table is not complete")
    return pairs, pos


def encode(data, freq=None):
    if not data:
        return b""
    n = len(data)
    if freq is None:
        freq = Counter(data)
    if n >= _ENTROPY_GATE_MIN:
        h = 0.0
        for c in freq.values():
            p = c / n
            h -= p * math.log2(p)
        if (n * h) / 8.0 + len(freq) * 2 + 8 >= n - (n >> 6):
            return None
    lengths = build_code_lengths(freq)
    codes = canonical_codes(list(lengths.items()))
    table = [None] * 256
    for sym, (c, l) in codes.items():
        table[sym] = (c, l)
    n = len(data)
    buf = bytearray()
    cur = 0
    nbits = 0
    append = buf.append
    if n >= 32768:
        pt = [None] * 65536
        for b1 in range(256):
            t1 = table[b1]
            if t1 is None:
                continue
            c1, l1 = t1
            base = b1 << 8
            for b2 in range(256):
                t2 = table[b2]
                if t2 is None:
                    continue
                c2, l2 = t2
                pt[base | b2] = ((c1 << l2) | c2, l1 + l2)
        i = 0
        limit = n - 1
        while i < limit:
            cl = pt[(data[i] << 8) | data[i + 1]]
            cur = (cur << cl[1]) | cl[0]
            nbits += cl[1]
            while nbits >= 8:
                nbits -= 8
                append((cur >> nbits) & 0xFF)
            cur &= (1 << nbits) - 1
            i += 2
        if i < n:
            c, l = table[data[i]]
            cur = (cur << l) | c
            nbits += l
            while nbits >= 8:
                nbits -= 8
                append((cur >> nbits) & 0xFF)
            cur &= (1 << nbits) - 1
    else:
        for c, l in map(table.__getitem__, data):
            cur = (cur << l) | c
            nbits += l
            while nbits >= 8:
                nbits -= 8
                append((cur >> nbits) & 0xFF)
            cur &= (1 << nbits) - 1
    tail = b""
    if nbits:
        tail = bytes(((cur << (8 - nbits)) & 0xFF,))
    return serialize_table(lengths) + bytes(buf) + tail


def decode(buf, pos, end, expected_len):
    if expected_len == 0:
        return b"", pos
    count, pos = decode_varint(buf, pos, end)
    if count <= 0 or count > 256 or pos + 2 * count > end:
        raise CorruptedError("invalid huffman table")
    pairs = []
    for _ in range(count):
        sym = buf[pos]
        length = buf[pos + 1]
        pos += 2
        if length < 1 or length > MAX_CODE_LEN:
            raise CorruptedError("invalid code length")
        pairs.append((sym, length))
    max_len = max(l for _, l in pairs)
    if len(pairs) > 1:
        total = 0
        for _, l in pairs:
            total += 1 << (max_len - l)
        if total != (1 << max_len):
            raise CorruptedError("huffman table is not complete")
    pairs.sort(key=lambda p: (p[1], p[0]))
    first_code = [0] * (max_len + 2)
    counts = [0] * (max_len + 2)
    syms_by_len = [[] for _ in range(max_len + 2)]
    code = 0
    prev_len = pairs[0][1]
    seen_len = set()
    for sym, length in pairs:
        code <<= length - prev_len
        if length not in seen_len:
            seen_len.add(length)
            first_code[length] = code
        syms_by_len[length].append(sym)
        counts[length] += 1
        code += 1
        prev_len = length
    reader = BitReader(buf, pos, end)
    out = bytearray()
    val = 0
    cur_len = 0
    while len(out) < expected_len:
        val = (val << 1) | reader.read_bit()
        cur_len += 1
        if cur_len > max_len:
            raise CorruptedError("invalid huffman code in stream")
        idx = val - first_code[cur_len]
        if idx >= 0 and idx < counts[cur_len]:
            out.append(syms_by_len[cur_len][idx])
            val = 0
            cur_len = 0
    return bytes(out), reader.pos
