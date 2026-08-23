"""V2 M2 — split-stream entropy coding for LZ2 token streams.

Isolated research module (V2 track). V1 stays untouched; the canonical
Huffman construction is REUSED from the frozen, validated implementation in
divideencode_v1/huffman.py (build_code_lengths / canonical_codes).

Streams (each with its own canonical Huffman model):
    TYPES      LIT=0, MATCH=1, REP0=2, REP1=3, REP2=4
    LITERALS   raw byte values 0..255
    LENGTHS    length class symbol + raw extra bits
    DISTANCES  distance class symbol + raw extra bits

Class+extra-bits scheme (deflate/zstd-inspired): four symbols per extra-bit
level with doubling spans, so common short lengths/distances cost few bits.
Matches longer than MAX_CHUNK are split into consecutive chunks sharing the
same distance before coding.

Container layout (experimental M2 envelope; DE2 proper arrives in M3):
    varint(original_length)
    mode byte: 0 = RAW stored, 1 = ENTROPY coded
    MODE_RAW:
        original bytes verbatim (used when coding would not shrink input)
    MODE_ENTROPY:
        varint(token_count)                # 0 only legal when length is 0
        presence byte                      # bit0 types, bit1 literals,
                                           # bit2 lengths, bit3 distances,
                                           # bits4..7 reserved, must be 0
        per present section, fixed order TYPES, LITERALS, LENGTHS, DISTANCES:
            varint(section_len)            # bytes AFTER this varint
            table: varint(count) + count x (symbol byte, code-length byte)
            payload: MSB-first bits, zero-padded to a byte boundary

All failures raise EntropyError. Decoding consumes the container exactly;
trailing bytes are rejected.
"""

from bisect import bisect_right

from divideencode_v1.huffman import build_code_lengths, canonical_codes

from .lz2 import (
    MIN_MATCH,
    Config,
    LZ2Error,
    tokenize_tokens,
    encode_varint,
    decode_varint,
)

TYPE_LIT = 0
TYPE_MATCH = 1
TYPE_REP0 = 2                    # REP1 = 3, REP2 = 4
TYPE_MAX = 4

MODE_RAW = 0                     # stored verbatim (incompressible inputs)
MODE_ENTROPY = 1

MAX_CHUNK = 1 << 16              # split cap; huge runs stay single tokens
LEN_MAX = 1 << 17                # hard cap of the length class table
DIST_MAX = 1 << 20               # hard cap of the distance class table
MAX_CODE_LEN = 32                # matches validated V1 limit


class EntropyError(Exception):
    """Raised on malformed entropy-coded streams."""


def _build_classes(first_val, max_val):
    """Return (bases, extras): four symbols per bit level, spans doubling."""
    bases = []
    extras = []
    v = first_val
    bits = 0
    while True:
        done = False
        for _ in range(4):
            bases.append(v)
            extras.append(bits)
            v += 1 << bits
            if v > max_val:
                done = True
                break
        if done:
            break
        bits += 1
    return bases, extras


_LEN_BASES, _LEN_EXTRAS = _build_classes(MIN_MATCH, LEN_MAX)
_DIST_BASES, _DIST_EXTRAS = _build_classes(1, DIST_MAX)


class BitWriter:
    __slots__ = ("buf", "cur", "n")

    def __init__(self):
        self.buf = bytearray()
        self.cur = 0
        self.n = 0

    def write(self, value, nbits):
        if nbits == 0:
            return
        self.cur = (self.cur << nbits) | value
        self.n += nbits
        while self.n >= 8:
            self.n -= 8
            self.buf.append((self.cur >> self.n) & 0xFF)
        self.cur &= (1 << self.n) - 1

    def getvalue(self):
        out = bytes(self.buf)
        tail = self.n
        if tail:
            out += bytes(((self.cur << (8 - tail)) & 0xFF,))
        return out


class BitReader:
    __slots__ = ("buf", "start", "pos", "end", "cur", "n", "consumed")

    def __init__(self, buf, pos, end):
        self.buf = buf
        self.start = pos
        self.pos = pos
        self.end = end
        self.cur = 0
        self.n = 0
        self.consumed = 0

    def read(self, nbits):
        if nbits == 0:
            return 0
        while self.n < nbits:
            if self.pos >= self.end:
                raise EntropyError("bitstream exhausted")
            self.cur = (self.cur << 8) | self.buf[self.pos]
            self.pos += 1
            self.n += 8
        self.n -= nbits
        self.consumed += nbits
        return (self.cur >> self.n) & ((1 << nbits) - 1)


class Canon:
    """Canonical Huffman model on top of the validated V1 construction."""

    __slots__ = ("codes", "pairs", "max_len", "first_code", "counts",
                 "syms_by_len")

    def __init__(self, codelens):
        pairs = sorted((int(s), int(l)) for s, l in codelens.items())
        if not pairs:
            raise ValueError("empty alphabet")
        if pairs[-1][1] > MAX_CODE_LEN or pairs[0][1] < 1:
            raise ValueError("invalid code lengths")
        self.pairs = pairs
        by_len = sorted(pairs, key=lambda p: (p[1], p[0]))
        self.max_len = by_len[-1][1]
        self.codes = canonical_codes(pairs)

        max_len = self.max_len
        first_code = [0] * (max_len + 2)
        counts = [0] * (max_len + 2)
        syms_by_len = [[] for _ in range(max_len + 2)]
        code = 0
        prev_len = by_len[0][1]
        seen = set()
        for sym, length in by_len:
            code <<= length - prev_len
            if length not in seen:
                seen.add(length)
                first_code[length] = code
            syms_by_len[length].append(sym)
            counts[length] += 1
            code += 1
            prev_len = length
        self.first_code = first_code
        self.counts = counts
        self.syms_by_len = syms_by_len

    def encode_one(self, writer, sym):
        code, length = self.codes[sym]
        writer.write(code, length)

    def decode_one(self, reader):
        val = 0
        cur_len = 0
        while cur_len <= self.max_len:
            val = (val << 1) | reader.read(1)
            cur_len += 1
            idx = val - self.first_code[cur_len]
            if 0 <= idx < self.counts[cur_len]:
                return self.syms_by_len[cur_len][idx]
        raise EntropyError("invalid huffman code in stream")

    def serialize(self):
        out = bytearray(encode_varint(len(self.pairs)))
        for sym, length in sorted(self.pairs, key=lambda p: (p[1], p[0])):
            out.append(sym)
            out.append(length)
        return bytes(out)

    @staticmethod
    def parse(buf, pos, end):
        count, pos = decode_varint(buf, pos, end)
        if count <= 0 or count > 256 or pos + 2 * count > end:
            raise EntropyError("invalid entropy table header")
        codelens = {}
        pairs = []
        for _ in range(count):
            sym = buf[pos]
            length = buf[pos + 1]
            pos += 2
            if length < 1 or length > MAX_CODE_LEN:
                raise EntropyError("invalid code length")
            if sym in codelens:
                raise EntropyError("duplicate symbol in table")
            codelens[sym] = length
            pairs.append((sym, length))
        if count > 1:
            max_len = max(length for _, length in pairs)
            total = 0
            for _, length in pairs:
                total += 1 << (max_len - length)
            if total != (1 << max_len):
                raise EntropyError("huffman table is not complete")
        return Canon(codelens), pos


def _build_model(syms):
    freq = {}
    for s in syms:
        freq[s] = freq.get(s, 0) + 1
    return Canon(build_code_lengths(freq))


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

def _split_match(length, emit):
    """Emit one long match as consecutive <=MAX_CHUNK chunks (same distance).

    emit(chunk_length) is called by the caller-supplied closure so the split
    chunks land in the same streams as ordinary matches.
    """
    while length > MAX_CHUNK:
        take = MAX_CHUNK
        rest = length - take
        if rest < MIN_MATCH:
            take -= MIN_MATCH - rest
            rest = MIN_MATCH
        emit(take)
        length -= take
    emit(length)


def _entropy_body(type_syms, lit_syms, len_vals, dist_vals):
    """Serialize the ENTROPY part that follows the mode byte."""
    out = bytearray()
    out += encode_varint(len(type_syms))
    if not type_syms:
        return bytes(out)

    len_cls = []
    len_extra = []
    for v in len_vals:
        cls = bisect_right(_LEN_BASES, v) - 1
        len_cls.append(cls)
        len_extra.append(v - _LEN_BASES[cls])
    dist_cls = []
    dist_extra = []
    for v in dist_vals:
        cls = bisect_right(_DIST_BASES, v) - 1
        dist_cls.append(cls)
        dist_extra.append(v - _DIST_BASES[cls])

    canon_t = _build_model(type_syms)
    canon_l = _build_model(lit_syms) if lit_syms else None
    canon_len = _build_model(len_cls) if len_cls else None
    canon_d = _build_model(dist_cls) if dist_cls else None

    presence = 1
    if canon_l is not None:
        presence |= 1 << 1
    if canon_len is not None:
        presence |= 1 << 2
    if canon_d is not None:
        presence |= 1 << 3
    out.append(presence)

    def emit(model, payload):
        body = bytearray(model.serialize())
        body += payload
        out.extend(encode_varint(len(body)))
        out.extend(body)

    bw = BitWriter()
    for s in type_syms:
        canon_t.encode_one(bw, s)
    emit(canon_t, bw.getvalue())

    if canon_l is not None:
        bw = BitWriter()
        for s in lit_syms:
            canon_l.encode_one(bw, s)
        emit(canon_l, bw.getvalue())

    if canon_len is not None:
        bw = BitWriter()
        for i, cls in enumerate(len_cls):
            canon_len.encode_one(bw, cls)
            bw.write(len_extra[i], _LEN_EXTRAS[cls])
        emit(canon_len, bw.getvalue())

    if canon_d is not None:
        bw = BitWriter()
        for i, cls in enumerate(dist_cls):
            canon_d.encode_one(bw, cls)
            bw.write(dist_extra[i], _DIST_EXTRAS[cls])
        emit(canon_d, bw.getvalue())

    return bytes(out)


def entropy_encode(tokens, orig_len):
    """Encode a token list. Returns the full container (header+mode+body)."""
    type_syms = []
    lit_syms = []
    len_vals = []
    dist_vals = []

    def add_match(l, d):
        type_syms.append(TYPE_MATCH)
        len_vals.append(l)
        dist_vals.append(d)

    for tok in tokens:
        kind = tok[0]
        if kind == "L":
            type_syms.append(TYPE_LIT)
            lit_syms.append(tok[1])
        elif kind == "R":
            type_syms.append(TYPE_REP0 + tok[2])
            len_vals.append(tok[1])
        else:
            _split_match(tok[1], lambda l, d=tok[2]: add_match(l, d))

    body = _entropy_body(type_syms, lit_syms, len_vals, dist_vals)
    return encode_varint(orig_len) + bytes([MODE_ENTROPY]) + body


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------

def _decode_value(reader, canon, bases, extras, hi, what):
    s = canon.decode_one(reader)
    if s >= len(bases):
        raise EntropyError("%s class out of range" % what)
    nbits = extras[s]
    eb = reader.read(nbits) if nbits else 0
    v = bases[s] + eb
    if v > hi:
        raise EntropyError("%s out of range" % what)
    return v


def entropy_decode(blob):
    buf = bytes(blob)
    end = len(buf)
    try:
        orig_len, pos = decode_varint(buf, 0, end)
        if pos >= end:
            raise EntropyError("truncated container")
        mode = buf[pos]
        pos += 1
        if mode == MODE_RAW:
            if end - pos != orig_len:
                raise EntropyError("stored payload size mismatch")
            return buf[pos:]
        if mode != MODE_ENTROPY:
            raise EntropyError("unknown container mode %d" % mode)
        tcount, pos = decode_varint(buf, pos, end)
    except LZ2Error as exc:
        raise EntropyError(str(exc)) from None

    if tcount == 0:
        if orig_len != 0:
            raise EntropyError("declared output without tokens")
        if pos != end:
            raise EntropyError("trailing garbage after empty stream")
        return b""

    if pos >= end:
        raise EntropyError("truncated container")
    presence = buf[pos]
    pos += 1
    if presence & 0xF0:
        raise EntropyError("reserved presence bits set")
    if not presence & 1:
        raise EntropyError("types section missing")

    sections = []
    for idx in range(4):
        if not presence & (1 << idx):
            sections.append(None)
            continue
        slen, pos = decode_varint(buf, pos, end)
        sec_end = pos + slen
        if sec_end > end:
            raise EntropyError("section overruns container")
        canon, payload_pos = Canon.parse(buf, pos, sec_end)
        reader = BitReader(buf, payload_pos, sec_end)
        sections.append((canon, reader))
        pos = sec_end

    if pos != end:
        raise EntropyError("trailing garbage after last section")

    sec_t, sec_l, sec_len, sec_d = sections
    if sec_t is None:
        raise EntropyError("types section missing")

    out = bytearray()
    last = [-1, -1, -1]
    append = out.append

    for _ in range(tcount):
        typ = sec_t[0].decode_one(sec_t[1])
        if typ == TYPE_LIT:
            if sec_l is None:
                raise EntropyError("literal section missing")
            append(sec_l[0].decode_one(sec_l[1]))
            continue
        if typ == TYPE_MATCH:
            if sec_len is None or sec_d is None:
                raise EntropyError("match sections missing")
            length = _decode_value(sec_len[1], sec_len[0], _LEN_BASES,
                                   _LEN_EXTRAS, min(LEN_MAX, orig_len),
                                   "match length")
            dist = _decode_value(sec_d[1], sec_d[0], _DIST_BASES,
                                 _DIST_EXTRAS, DIST_MAX, "distance")
            if dist < 1 or dist > len(out):
                raise EntropyError("invalid back reference distance")
            if len(out) + length > orig_len:
                raise EntropyError("match overruns declared output size")
            src = len(out) - dist
            for k in range(length):
                append(out[src + k])
            last[2] = last[1]
            last[1] = last[0]
            last[0] = dist
            continue
        if typ <= TYPE_MAX:
            rid = typ - TYPE_REP0
            if sec_len is None:
                raise EntropyError("length section missing")
            length = _decode_value(sec_len[1], sec_len[0], _LEN_BASES,
                                   _LEN_EXTRAS, min(LEN_MAX, orig_len),
                                   "rep length")
            dist = last[rid]
            if dist <= 0:
                raise EntropyError("rep references unused distance")
            if len(out) + length > orig_len:
                raise EntropyError("rep overruns declared output size")
            src = len(out) - dist
            for k in range(length):
                append(out[src + k])
            continue
        raise EntropyError("invalid token type symbol")

    if len(out) != orig_len:
        raise EntropyError("reconstructed size mismatch")

    for sec in sections:
        if sec is None:
            continue
        reader = sec[1]
        capacity = (reader.end - reader.start) * 8
        leftover = capacity - reader.consumed
        if leftover < 0 or leftover >= 8:
            raise EntropyError("entropy section padding violation")

    return bytes(out)


def compress(data, cfg=None):
    cfg = cfg or Config()
    data = bytes(data)
    tokens, _stats = tokenize_tokens(data, cfg)
    ent = entropy_encode(tokens, len(data))
    if len(ent) < len(data):
        return ent
    # incompressible: store verbatim (mode byte makes this self-describing)
    return encode_varint(len(data)) + bytes([MODE_RAW]) + data


def decompress(blob):
    return entropy_decode(bytes(blob))
