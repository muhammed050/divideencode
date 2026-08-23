"""DivideEncode V2 -- reversible preprocessing transforms + classifier (M3).

Three preprocessing pipelines feeding the DE2 LZE stage:

  DELTA   mod-256 sequential byte difference (numeric/binary streams)
  RLE     V1-style run-length blob (run-heavy data)
  STRUCT  character-class plane split with a packed bitmap
          (structured text: JSON / CSV / logs / markup)

The classifier proposes a SHORTLIST of candidate methods from cheap
O(n)-ish signals; the codec measures every shortlisted candidate exactly
(each candidate is a single fast deterministic pipeline -- no search
tree) and keeps the smallest result.
"""
import re

from .lz import DEFAULT_WINDOW

# ---- DELTA -----------------------------------------------------------------

def delta_bytes(data):
    out = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        out[i] = (b - prev) & 0xFF
        prev = b
    return bytes(out)


def undelta_bytes(data):
    out = bytearray(len(data))
    prev = 0
    for i, b in enumerate(data):
        prev = (prev + b) & 0xFF
        out[i] = prev
    return bytes(out)


# ---- STRUCT ----------------------------------------------------------------

_STRUCT_CHARS = frozenset(
    b" \t\n\r\v\f{}[]()<>;,.:\"'`|=+-*/%\\!?#&@$^~"
)
_STRUCT_TBL = bytes(1 if b in _STRUCT_CHARS else 0 for b in range(256))


def struct_pack(data):
    """Split into [bitmap][structural plane][value plane]."""
    n = len(data)
    tbl = _STRUCT_TBL
    bitmap = bytearray((n + 7) // 8)
    s_part = bytearray()
    v_part = bytearray()
    s_append = s_part.append
    v_append = v_part.append
    acc = 0
    nbits = 0
    bit_idx = 0
    byte_pos = 0
    for b in data:
        if tbl[b]:
            s_append(b)
            acc |= 1 << nbits
        else:
            v_append(b)
        nbits += 1
        if nbits == 8:
            bitmap[byte_pos] = acc
            byte_pos += 1
            acc = 0
            nbits = 0
    if nbits:
        bitmap[byte_pos] = acc
    return bytes(bitmap) + bytes(s_part) + bytes(v_part)


def struct_unpack(packed, expected_len):
    bitmap_len = (expected_len + 7) // 8
    if bitmap_len > len(packed):
        raise ValueError("struct bitmap truncated")
    bitmap = packed[:bitmap_len]
    ns = sum(bin(x).count("1") for x in bitmap[:bitmap_len])
    nv = expected_len - ns
    s_part = packed[bitmap_len:bitmap_len + ns]
    v_part = packed[bitmap_len + ns:]
    if len(s_part) != ns or len(v_part) != nv:
        raise ValueError("struct planes truncated")
    tbl = _STRUCT_TBL
    out = bytearray(expected_len)
    si = 0
    vi = 0
    for i in range(expected_len):
        if bitmap[i >> 3] >> (i & 7) & 1:
            out[i] = s_part[si]
            si += 1
        else:
            out[i] = v_part[vi]
            vi += 1
    return bytes(out)


# ---- stride planes (numeric/binary STRUCT variant) -------------------------

def plane_pack(data, k):
    """Split into k interleaved planes; returns concatenated planes."""
    return b"".join(data[c::k] for c in range(k))


def plane_unpack(packed, expected_len, k):
    out = bytearray(expected_len)
    pos = 0
    for c in range(k):
        ln = (expected_len - c + k - 1) // k
        out[c::k] = packed[pos:pos + ln]
        pos += ln
    if pos != len(packed):
        raise ValueError("plane payload truncated")
    return bytes(out)


def plane_lengths(n, k):
    return [(n - c + k - 1) // k for c in range(k)]


def plane_delta_pack(data, k):
    """Plane split, then an independent mod-256 delta per plane."""
    packed = plane_pack(data, k)
    lens = plane_lengths(len(data), k)
    parts = []
    pos = 0
    for ln in lens:
        parts.append(delta_bytes(packed[pos:pos + ln]))
        pos += ln
    return b"".join(parts)


def plane_delta_unpack(packed, expected_len, k):
    lens = plane_lengths(expected_len, k)
    planes = []
    pos = 0
    for ln in lens:
        planes.append(undelta_bytes(packed[pos:pos + ln]))
        pos += ln
    if pos != len(packed):
        raise ValueError("plane payload truncated")
    out = bytearray(expected_len)
    for c in range(k):
        out[c::k] = planes[c]
    return bytes(out)


# ---- word split (DIVIDE-lite numeric transform) ----------------------------

from ..divide_transform import pack_bits, unpack_bits


def word_split(data, w, s):
    """Split LE words of width w into quotient stream + packed remainders.

    Returns (q_bytes, r_values, tail, wq)."""
    n = len(data)
    nw = n // w
    tail = data[nw * w:]
    frombytes = int.from_bytes
    step = max(1, nw // 4096)
    mq = 0
    for i in range(0, nw, step):
        v = frombytes(data[i * w:i * w + w], "little")
        q = v >> s
        if q > mq:
            mq = q
    wq = max(1, (mq.bit_length() + 7) // 8)
    qb = bytearray(nw * wq)
    rs = []
    rap = rs.append
    mask = (1 << s) - 1
    for i in range(nw):
        v = frombytes(data[i * w:i * w + w], "little")
        qb[i * wq:(i + 1) * wq] = (v >> s).to_bytes(wq, "little")
        rap(v & mask)
    return bytes(qb), rs, tail, wq


def word_join(q_bytes, r_values, tail, w, s, wq, expected_len):
    out = bytearray()
    frombytes = int.from_bytes
    for i in range(len(r_values)):
        q = frombytes(q_bytes[i * wq:(i + 1) * wq], "little")
        out += ((q << s) | r_values[i]).to_bytes(w, "little")
    out += tail
    if len(out) != expected_len:
        raise ValueError("word join size mismatch")
    return bytes(out)


# ---- classifier --------------------------------------------------------------

_RUN_RE = re.compile(rb"(.)\1{2,}", re.DOTALL)


def classify(data):
    """Cheap signals -> dict used to shortlist candidate methods.

    Only inspects sampled windows for expensive statistics; exact
    measurement of shortlisted candidates happens in the codec.
    """
    n = len(data)
    if n == 0:
        return {"n": 0}
    window = data[:65536]
    # printable fraction
    nonp = 0
    for b in window:
        if not (32 <= b < 127 or b in (9, 10, 13)):
            nonp += 1
    printable = 1.0 - nonp / len(window)

    # run statistics on the window
    run_bytes = 0
    big_run = False
    for m in _RUN_RE.finditer(window):
        rl = m.end() - m.start()
        run_bytes += rl
        if rl >= 32:
            big_run = True
    run_frac = run_bytes / len(window)

    # high-byte zero fractions (little-endian numeric planes)
    usable2 = n & ~1
    hz2 = data[1:usable2:2].count(0) / max(1, usable2 // 2)
    usable4 = n & ~3
    hz4 = data[3:usable4:4].count(0) / max(1, usable4 // 4)

    # low-cardinality check for strided planes (sampled)
    def _distinct(start, step):
        sample = data[start:start + 64 * step:step]
        return len(set(sample))

    dist2 = _distinct(1, 2) if usable2 >= 2 else 256
    dist4 = _distinct(3, 4) if usable4 >= 4 else 256

    # byte-delta structure score: fraction of tiny zigzag deltas
    sample = window[:16384]
    prev = 0
    small = 0
    for b in sample:
        d = (b - prev) & 0xFF
        prev = b
        zz = ((d << 1) ^ (d >> 7)) & 0xFF
        if zz < 16:
            small += 1
    bd8 = small / max(1, len(sample))

    return {
        "n": n,
        "printable": printable,
        "run_frac": run_frac,
        "big_run": big_run,
        "hi_zero2": hz2,
        "hi_zero4": hz4,
        "dist2": dist2,
        "dist4": dist4,
        "bd8_small": bd8,
    }


METHOD_STORED = 0
METHOD_LZE = 1
METHOD_DELTA = 2
METHOD_RLE = 3
METHOD_STRUCT = 4
METHOD_WORD = 5
METHOD_PLANE_DELTA = 6

STRUCT_TEXT = 1        # char-class split
# stride values 2 / 4 double as struct variants for numeric planes
WORD_DEFAULT = (2, 7)  # (word width, shift) for the DIVIDE-lite candidate


def plan_candidates(data):
    """Ordered shortlist of (method, param) pairs worth measuring.

    param meaning per method:
      METHOD_STRUCT: STRUCT_TEXT (char classes) or stride 2/4 (planes)
      others: unused (None)
    """
    f = classify(data)
    n = f["n"]
    cands = [(METHOD_LZE, None), (METHOD_STORED, None)]

    # structure present? otherwise skip heavy numeric candidates entirely
    # (big speed win on incompressible / already-compressed inputs)
    structured = (
        f["bd8_small"] > 0.30 or
        f["hi_zero4"] > 0.30 or f["hi_zero2"] > 0.45 or
        f["dist2"] <= 24 or f["dist4"] <= 12 or
        f["run_frac"] >= 0.02 or f["printable"] > 0.90)

    # DELTA: always measured for binary data -- its gains come from
    # repetition inside the delta stream (e.g. PRNG periods), which no
    # cheap scalar signal reliably predicts.
    if n >= 16384 and f["printable"] < 0.85:
        cands.insert(0, (METHOD_DELTA, None))

    # PLANE split: interleaved numeric channels (u16/u32 words)
    if n >= 16384 and f["printable"] < 0.85 and structured:
        if f["hi_zero4"] > 0.30 or f["dist4"] <= 12:
            cands.insert(0, (METHOD_PLANE_DELTA, 4))
            cands.insert(0, (METHOD_STRUCT, 4))
        if f["hi_zero2"] > 0.45 or f["dist2"] <= 24:
            cands.insert(0, (METHOD_PLANE_DELTA, 2))
            cands.insert(0, (METHOD_STRUCT, 2))

    # RLE: obvious run dominance
    if n >= 64 and structured and (f["big_run"] or f["run_frac"] >= 0.02):
        cands.insert(0, (METHOD_RLE, None))

    # DIVIDE-lite: sub-byte remainder packing for low-cardinality u16/u32
    if n >= 16384 and f["printable"] < 0.85 and structured and (
            f["dist2"] <= 24 or f["hi_zero2"] > 0.45):
        cands.insert(0, (METHOD_WORD, WORD_DEFAULT))

    # STRUCT text mode: repetitive human-readable text
    if n >= 8192 and f["printable"] > 0.90 and f["run_frac"] <= 0.30:
        cands.insert(0, (METHOD_STRUCT, STRUCT_TEXT))

    # de-dup on (method, param), preserve priority order
    seen = set()
    ordered = []
    for item in cands:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


__all__ = [
    "delta_bytes", "undelta_bytes",
    "struct_pack", "struct_unpack",
    "plane_pack", "plane_unpack",
    "word_split", "word_join",
    "classify", "plan_candidates",
    "METHOD_STORED", "METHOD_LZE", "METHOD_DELTA", "METHOD_RLE",
    "METHOD_STRUCT", "METHOD_WORD", "STRUCT_TEXT", "WORD_DEFAULT",
    "DEFAULT_WINDOW",
]
