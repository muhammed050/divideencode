import array
import sys

from .errors import CorruptedError

DIVISORS = (2, 3, 4, 5, 8, 16, 32, 64, 128, 256, 1024, 4096, 65536)
WORD_SIZES = (1, 2, 4)


def _word_max(data, w):
    if w == 1:
        return max(data)
    if w == 2:
        usable = len(data) & ~1
        if not usable:
            return 0
        a = array.array("H")
        a.frombytes(data[:usable])
        if sys.byteorder != "little":
            a.byteswap()
        return max(a)
    nw = len(data) // w
    best = 0
    for x in _iter_words(data, w):
        if x > best:
            best = x
    return best


def _iter_words(data, w):
    n = len(data)
    nw = n // w
    frombytes = int.from_bytes
    for i in range(nw):
        yield frombytes(data[i * w:i * w + w], "little")


def _plane_split(data, w, shift_bytes, n_words):
    end = n_words * w
    s = shift_bytes
    q = b"".join(data[c:end:w] for c in range(s, w))
    r = b"".join(data[c:end:w] for c in range(s))
    return q, r


def _plane_join(q_bytes, r_bytes, w, s, n_words):
    out = bytearray(n_words * w)
    for i in range(s):
        base = i * n_words
        out[i::w] = r_bytes[base:base + n_words]
    for i in range(s, w):
        base = (i - s) * n_words
        out[i::w] = q_bytes[base:base + n_words]
    return bytes(out)


def divide_transform(data, w, d):
    n = len(data)
    nw = n // w
    tail = bytes(data[nw * w:])
    k = d.bit_length() - 1
    if (1 << k) == d and k % 8 == 0 and k >= 8 and w > k // 8:
        qb, rb = _plane_split(data, w, k // 8, nw)
        wq = w - k // 8
        return {
            "word_size": w,
            "divisor": d,
            "n_words": nw,
            "wq": wq,
            "rbits": k,
            "tail": tail,
            "quotient_stream": qb,
            "remainder_stream": rb,
        }
    qs = []
    rs = []
    append_q = qs.append
    append_r = rs.append
    if w == 1:
        for b in data[:nw]:
            append_q(b // d)
            append_r(b % d)
    else:
        for i in range(nw):
            x = int.from_bytes(data[i * w:i * w + w], "little")
            append_q(x // d)
            append_r(x % d)
    qmax = max(qs) if qs else 0
    wq = max(1, (qmax.bit_length() + 7) // 8)
    rbits = k + 1 if (1 << k) != d else k
    rb = pack_bits(rs, rbits)
    qb = bytearray()
    for q in qs:
        qb += q.to_bytes(wq, "little")
    return {
        "word_size": w,
        "divisor": d,
        "n_words": nw,
        "wq": wq,
        "rbits": rbits,
        "tail": tail,
        "quotient_stream": bytes(qb),
        "remainder_stream": rb,
    }


def divide_reconstruct(q_bytes, r_bytes, w, d, wq, rbits, n_words, tail):
    if len(q_bytes) != n_words * wq:
        raise CorruptedError("divide quotient stream has wrong size")
    need_r = (n_words * rbits + 7) // 8
    if len(r_bytes) < need_r:
        raise CorruptedError("divide remainder stream truncated")
    limit = 1 << (w * 8)
    k = d.bit_length() - 1
    out = bytearray()
    if (1 << k) == d and k % 8 == 0 and k >= 8 and wq == w - k // 8:
        body = _plane_join(q_bytes, r_bytes, w, k // 8, n_words)
        out += body
        out += tail
        return bytes(out)
    idx = 0
    rs = unpack_bits(r_bytes, n_words, rbits)
    dd = d
    for r in rs:
        if r >= dd:
            raise CorruptedError("divide remainder out of range")
        q = int.from_bytes(q_bytes[idx:idx + wq], "little")
        idx += wq
        x = q * dd + r
        if x >= limit:
            raise CorruptedError("divide reconstructed word overflow")
        out += x.to_bytes(w, "little")
    out += tail
    return bytes(out)


def pack_bits(values, bits):
    total = len(values) * bits
    out = bytearray((total + 7) // 8)
    acc = 0
    cnt = 0
    idx = 0
    for v in values:
        acc |= v << cnt
        cnt += bits
        while cnt >= 8:
            out[idx] = acc & 0xFF
            acc >>= 8
            cnt -= 8
            idx += 1
    if cnt:
        out[idx] = acc & 0xFF
    return bytes(out)


def unpack_bits(buf, n_values, bits):
    vals = []
    acc = 0
    cnt = 0
    idx = 0
    blen = len(buf)
    mask = (1 << bits) - 1
    for _ in range(n_values):
        while cnt < bits:
            if idx >= blen:
                raise CorruptedError("bit stream exhausted")
            acc |= buf[idx] << cnt
            idx += 1
            cnt += 8
        vals.append(acc & mask)
        acc >>= bits
        cnt -= bits
    return vals


def estimate_candidates(data, top_k=3):
    n = len(data)
    cands = []
    for w in WORD_SIZES:
        if n < 4 * w:
            continue
        nw = n // w
        xmax = _word_max(data, w)
        if xmax == 0:
            continue
        for did, d in enumerate(DIVISORS):
            if d > xmax:
                continue
            qmax = xmax // d
            if qmax == 0:
                continue
            wq = max(1, (qmax.bit_length() + 7) // 8)
            rbits = (d - 1).bit_length()
            est = nw * wq + (nw * rbits + 7) // 8
            if est < n:
                cands.append((est, w, d))
    cands.sort()
    return [(w, d) for _, w, d in cands[:top_k]]
