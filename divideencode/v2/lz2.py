"""V2 LZ2 tokenizer — M1 isolated experimental module.

Part of the V2 research track. Does NOT modify, import, or replace
DivideEncode V1 (frozen byte-exact in divideencode_v1/).

Tokens
------
LITERAL(byte)             one raw byte
MATCH(length, distance)   length >= MIN_MATCH, 1 <= distance <= window
REP(length, repeat_id)    repeat_id in {0,1,2} -> most-recent distance list

Repeat-offset semantics (zstd-style):
    last = [R0, R1, R2], most recent first.
    REP(i) uses last[i]; the list is NOT reordered on rep use.
    MATCH(distance d) inserts d at the front: [d, old0, old1].

Raw token stream format (intentionally simple; entropy coding is M2):
    varint(original_length)
    then groups of up to 8 tokens; each group is prefixed by one flags byte
    (bit k = 1 -> token k is a match/rep, bit k = 0 -> token k is a literal):
        literal:   1 raw byte
        match:     varint(length - MIN_MATCH), varint(distance)
        rep:       varint(length - MIN_MATCH), varint(0), byte(repeat_id)

Parsing strategy: greedy + configurable lazy lookahead over a 4-byte-keyed
hash-chain match finder with bounded chain depth and sliding window.
"""

MIN_MATCH = 4

DEFAULT_WINDOW = 1 << 16      # 64 KiB (parity with V1 LZ window)
DEFAULT_MAX_CHAIN = 32        # hash-chain candidates examined per position
DEFAULT_LAZY = 1              # 0 = greedy, 1 = lazy-1
DEFAULT_NICE_LENGTH = 96      # stop chain walk once a match this long is found


class LZ2Error(Exception):
    """Raised on malformed or truncated LZ2 streams."""


class Config:
    __slots__ = ("window", "max_chain", "lazy", "nice_length")

    def __init__(self, window=DEFAULT_WINDOW, max_chain=DEFAULT_MAX_CHAIN,
                 lazy=DEFAULT_LAZY, nice_length=DEFAULT_NICE_LENGTH):
        if window < MIN_MATCH:
            raise ValueError("window must be >= %d" % MIN_MATCH)
        if max_chain < 1:
            raise ValueError("max_chain must be >= 1")
        if lazy not in (0, 1):
            raise ValueError("lazy must be 0 (greedy) or 1 (lazy-1)")
        if nice_length < MIN_MATCH:
            raise ValueError("nice_length must be >= MIN_MATCH")
        self.window = window
        self.max_chain = max_chain
        self.lazy = lazy
        self.nice_length = nice_length


def encode_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(buf, pos, end):
    result = 0
    shift = 0
    while True:
        if pos >= end:
            raise LZ2Error("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 56:
            raise LZ2Error("varint overflow")


class TokenStats:
    __slots__ = ("token_count", "literal_count", "match_count",
                 "rep_counts", "match_len_sum", "rep_len_sum",
                 "dist_sum", "dist_count")

    def __init__(self):
        self.token_count = 0
        self.literal_count = 0
        self.match_count = 0
        self.rep_counts = [0, 0, 0]
        self.match_len_sum = 0
        self.rep_len_sum = 0
        self.dist_sum = 0
        self.dist_count = 0

    @property
    def rep_count(self):
        return self.rep_counts[0] + self.rep_counts[1] + self.rep_counts[2]

    @property
    def avg_match_length(self):
        total = self.match_count + self.rep_count
        if not total:
            return 0.0
        return (self.match_len_sum + self.rep_len_sum) / total

    @property
    def avg_distance(self):
        return self.dist_sum / self.dist_count if self.dist_count else 0.0

    def as_dict(self):
        return {
            "token_count": self.token_count,
            "literal_count": self.literal_count,
            "match_count": self.match_count,
            "rep_match_count": self.rep_count,
            "rep0": self.rep_counts[0],
            "rep1": self.rep_counts[1],
            "rep2": self.rep_counts[2],
            "avg_match_length": round(self.avg_match_length, 3),
            "avg_distance": round(self.avg_distance, 2),
        }


def _extend(data, src, dst, limit):
    l = 0
    while l < limit and data[src + l] == data[dst + l]:
        l += 1
    return l


def _search(data, i, cfg, head, prev, last, extra_pos=-1):
    """Best normal match + best rep match at position i.

    extra_pos: optional additional candidate examined first (used by lazy
    matching so position i+1 can match against not-yet-linked position i).
    Returns (norm_len, norm_dist, rep_len, rep_id).
    """
    n = len(data)
    limit = n - i
    low = i - cfg.window

    best_len = 0
    best_dist = 0

    if extra_pos >= 0 and extra_pos >= low:
        if best_len < limit and data[extra_pos + best_len] == data[i + best_len]:
            l = _extend(data, extra_pos, i, limit)
            if l > best_len:
                best_len = l
                best_dist = i - extra_pos

    key = data[i:i + 4]
    pos = head.get(key, -1)
    tried = 0
    max_chain = cfg.max_chain
    nice = cfg.nice_length
    get_prev = prev.__getitem__
    while pos >= 0 and pos >= low and tried < max_chain:
        tried += 1
        if best_len < limit and data[pos + best_len] == data[i + best_len]:
            l = _extend(data, pos, i, limit)
            if l > best_len:
                best_len = l
                best_dist = i - pos
                if l >= nice or l >= limit:
                    break
        pos = get_prev(pos)

    rep_len = 0
    rep_id = -1
    for rid in range(3):
        d = last[rid]
        if d <= 0 or d > i:
            continue
        rl = _extend(data, i - d, i, limit)
        if rl > rep_len and rl >= MIN_MATCH:
            rep_len = rl
            rep_id = rid
    return best_len, best_dist, rep_len, rep_id


def tokenize_tokens(data, cfg=None):
    """Run the LZ parse. Returns (tokens, TokenStats).

    Tokens: ("L", byte) | ("M", length, distance) | ("R", length, rep_id).
    """
    cfg = cfg or Config()
    n = len(data)
    stats = TokenStats()
    tokens = []
    if n == 0:
        return tokens, stats

    head = {}
    get_head = head.get
    prev = [-1] * n
    last = [-1, -1, -1]
    ins_limit = n - MIN_MATCH + 1     # positions below this hold a full key

    i = 0
    while i < n:
        blen, bdist, rlen, rid = _search(data, i, cfg, head, prev, last)

        use_rep = rid >= 0 and rlen >= blen
        mlen = rlen if use_rep else blen

        emit_literal = mlen < MIN_MATCH
        if not emit_literal and cfg.lazy and mlen < cfg.nice_length \
                and i + 1 < n:
            nl, nd, nr, nrid = _search(data, i + 1, cfg, head, prev, last,
                                       extra_pos=i)
            n_mlen = nr if (nrid >= 0 and nr >= nl) else nl
            if n_mlen > mlen:
                emit_literal = True

        if emit_literal:
            key_ok = i < ins_limit
            if key_ok:
                prev[i] = get_head(data[i:i + MIN_MATCH], -1)
                head[data[i:i + MIN_MATCH]] = i
            tokens.append(("L", data[i]))
            i += 1
            stats.token_count += 1
            stats.literal_count += 1
            continue

        if use_rep:
            tokens.append(("R", mlen, rid))
            stats.match_len_sum += mlen
            stats.rep_len_sum += mlen
            stats.rep_counts[rid] += 1
        else:
            tokens.append(("M", mlen, bdist))
            stats.match_len_sum += mlen
            stats.dist_sum += bdist
            stats.dist_count += 1
            last[2] = last[1]
            last[1] = last[0]
            last[0] = bdist
        stats.token_count += 1
        stats.match_count += 1

        stop = min(i + mlen, ins_limit)
        step = 1 if mlen <= 16 else 2
        j = i
        while j < stop:
            key = data[j:j + MIN_MATCH]
            prev[j] = get_head(key, -1)
            head[key] = j
            j += step
        i += mlen

    return tokens, stats


def serialize_raw(tokens, orig_len):
    """Serialize a token list into the M1 raw stream format."""
    out = bytearray(encode_varint(orig_len))
    if not tokens:
        return bytes(out)

    append = out.append
    append(0)
    flags_pos = len(out) - 1
    flags = 0
    cnt = 0

    def close_group():
        nonlocal flags, cnt, flags_pos
        out[flags_pos] = flags
        flags = 0
        cnt = 0
        flags_pos = len(out)
        append(0)

    for tok in tokens:
        if tok[0] == "L":
            if cnt == 8:
                close_group()
            append(tok[1])
            cnt += 1
        else:
            if cnt == 8:
                close_group()
            flags |= 1 << cnt
            out += encode_varint(tok[1] - MIN_MATCH)
            if tok[0] == "R":
                out += encode_varint(0)
                append(tok[2])
            else:
                out += encode_varint(tok[2])
            cnt += 1
    if cnt:
        out[flags_pos] = flags
    return bytes(out)


def tokenize(data, cfg=None):
    """Tokenize *data*. Returns (serialized_stream, TokenStats)."""
    n = len(data)
    tokens, stats = tokenize_tokens(data, cfg)
    return serialize_raw(tokens, n), stats


def detokenize(stream):
    """Reconstruct original data from a serialized LZ2 stream."""
    buf = stream
    end = len(buf)
    n_out, pos = decode_varint(buf, 0, end)
    out = bytearray()
    last = [-1, -1, -1]
    append = out.append
    while len(out) < n_out:
        if pos >= end:
            raise LZ2Error("truncated lz2 stream")
        flags = buf[pos]
        pos += 1
        for bit in range(8):
            if len(out) >= n_out:
                break
            if (flags >> bit) & 1:
                lcode, pos = decode_varint(buf, pos, end)
                length = lcode + MIN_MATCH
                dcode, pos = decode_varint(buf, pos, end)
                if dcode == 0:
                    if pos >= end:
                        raise LZ2Error("truncated rep token")
                    rid = buf[pos]
                    pos += 1
                    if rid > 2:
                        raise LZ2Error("invalid repeat id")
                    dist = last[rid]
                    if dist <= 0:
                        raise LZ2Error("rep references unused distance")
                else:
                    dist = dcode
                    if dist > len(out):
                        raise LZ2Error("invalid back reference distance")
                    last[2] = last[1]
                    last[1] = last[0]
                    last[0] = dist
                if len(out) + length > n_out:
                    raise LZ2Error("match overruns declared output size")
                src = len(out) - dist
                for k in range(length):
                    append(out[src + k])
            else:
                if pos >= end:
                    raise LZ2Error("truncated literal")
                append(buf[pos])
                pos += 1
    if pos != end:
        raise LZ2Error("trailing garbage after lz2 stream")
    return bytes(out)


def analyze(data, cfg=None):
    """Tokenize and return (stream, stats_dict). Convenience wrapper."""
    stream, stats = tokenize(data, cfg)
    return stream, stats.as_dict()
