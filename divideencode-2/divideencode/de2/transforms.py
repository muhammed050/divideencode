"""DE2 reversible transforms (M3/M4).

Every transform returns (payload_bytes, tmeta_bytes) where tmeta carries
an explicit reversal recipe consumed verbatim by the decoder. Unknown
tmeta shapes are hard errors in the decoder.

tmeta grammar:
    b"D" + wcode + zzflag              word zigzag-delta, width WORD_SIZES[wcode]
    b"C" + wcode + zzflag + scode      that delta, then a byte-plane
                                       de-interleave of width WORD_SIZES[scode]

DELTA: words of width w (little-endian); first word stored as-is, each
following entry is the modular difference against its predecessor,
zigzag-mapped so small signed movements stay small. Trailing bytes
(len % w) are appended verbatim.

PLANE SPLIT: de-interleave into sw byte-planes concatenated in order;
the decoder re-interleaves using the block's original length. Helps when
low bytes carry entropy while high bytes are near-constant.

`numeric_encode` evaluates a small bounded candidate set (three delta
widths plus at most one split variant) and keeps the smallest LZ frame --
deterministic, no search explosion.
"""
import array
import sys

from ..errors import CorruptedError
from . import lz

WORD_SIZES = (1, 2, 4)
_TYPECODES = {2: "H", 4: "I"}


def _mask(w):
    return (1 << (8 * w)) - 1


def _words(data, w):
    nw = len(data) // w
    if w == 1:
        return list(data[:nw])
    a = array.array(_TYPECODES[w])
    a.frombytes(bytes(data[:nw * w]))
    if sys.byteorder != "little":
        a.byteswap()
    return a


def _unwords(vals, w):
    if w == 1:
        return bytes(bytearray(vals))
    a = array.array(_TYPECODES[w], vals)
    if sys.byteorder != "little":
        a.byteswap()
    return a.tobytes()


def _delta_core(data, w, zigzag):
    vals = _words(data, w)
    nw = len(vals)
    tail = bytes(data[nw * w:])
    mask = _mask(w)
    out = bytearray()
    if nw:
        prev = int(vals[0])
        out += prev.to_bytes(w, "little")
        half = 1 << (8 * w - 1)
        for j in range(1, nw):
            x = int(vals[j])
            d = (x - prev) & mask
            if zigzag:
                sd = d - (1 << (8 * w)) if d >= half else d
                d = ((sd << 1) ^ (sd >> (8 * w))) & mask
            out += d.to_bytes(w, "little")
            prev = x
    return bytes(out) + tail


def _undelta_core(payload, w, zigzag):
    vals = _words(payload, w)
    nw = len(vals)
    tail = payload[nw * w:]
    mask = _mask(w)
    out = bytearray()
    if nw:
        prev = int(vals[0])
        out += prev.to_bytes(w, "little")
        for j in range(1, nw):
            d = int(vals[j])
            if zigzag:
                sd = (d >> 1) ^ -(d & 1)
                prev = (prev + sd) & mask
            else:
                prev = (prev + d) & mask
            out += prev.to_bytes(w, "little")
    return bytes(out) + bytes(tail)


def _plane_split(data, sw):
    usable = (len(data) // sw) * sw
    parts = [data[i:usable:sw] for i in range(sw)]
    return b"".join(parts) + bytes(data[usable:])


def _plane_join(data, sw, orig_len):
    usable = (orig_len // sw) * sw
    per = usable // sw
    out = bytearray(orig_len)
    for i in range(sw):
        out[i:usable:sw] = data[i * per:(i + 1) * per]
    out[usable:] = data[sw * per:]
    return bytes(out)


def delta_encode(data, w=4, zigzag=True):
    """Simple single-stage form -> (transformed bytes, tmeta)."""
    if w not in WORD_SIZES:
        raise ValueError("invalid delta word width %r" % (w,))
    return _delta_core(data, w, zigzag), \
        b"D" + bytes((WORD_SIZES.index(w), 1 if zigzag else 0))


def numeric_encode(data, mono=None, hi_gain=None):
    """Bounded multi-candidate numeric pre-transform (M4/M5).

    Tries zigzag-delta widths from a feature-ranked candidate order
    (mono streams -> wide words first; hi-plane-collapse streams ->
    byte width first), keeping the smallest LZ frame; appends a
    same-width plane-split attempt for widths >= 2. An early-accept
    rule stops as soon as a frame lands under 2% of the input --
    no realistic competitor beats that by enough to pay for another
    encode. Returns (frame, tmeta).
    """
    if hi_gain is not None and hi_gain >= 2.0:
        order = (1, 4, 2)     # byte-delta exposes LCG-style structure
    elif mono is not None and mono >= 0.85:
        order = (4, 2, 1)     # monotonic counters: wide words win
    else:
        order = (2, 4, 1)     # smooth signals: u16 zigzag + split

    n = len(data)
    accept_at = max(64, n // 50)
    best_frame = None
    best_tmeta = None
    best_w = None
    for w in order:
        frame = lz.encode(_delta_core(data, w, True))
        if best_frame is None or len(frame) < len(best_frame):
            best_frame = frame
            best_tmeta = b"D" + bytes((WORD_SIZES.index(w), 1))
            best_w = w
        if len(best_frame) <= accept_at:
            return best_frame, best_tmeta
    if best_w >= 2:
        transformed = _delta_core(data, best_w, True)
        frame = lz.encode(_plane_split(transformed, best_w))
        if len(frame) < len(best_frame):
            best_frame = frame
            best_tmeta = b"C" + bytes((WORD_SIZES.index(best_w), 1,
                                       WORD_SIZES.index(best_w)))
    return best_frame, best_tmeta


def transform_decode(payload, tmeta, orig_len):
    """Inverse of any tmeta produced by delta_encode/numeric_encode."""
    if not tmeta:
        raise CorruptedError("missing transform metadata")
    kind = tmeta[0:1]
    if kind == b"D":
        if len(tmeta) != 3:
            raise CorruptedError("bad delta tmeta")
        wcode, zz = tmeta[1], tmeta[2]
        if wcode >= len(WORD_SIZES):
            raise CorruptedError("bad delta word code")
        return _undelta_core(payload, WORD_SIZES[wcode], bool(zz))
    if kind == b"C":
        if len(tmeta) != 4:
            raise CorruptedError("bad composite tmeta")
        wcode, zz, scode = tmeta[1], tmeta[2], tmeta[3]
        if wcode >= len(WORD_SIZES) or scode >= len(WORD_SIZES):
            raise CorruptedError("bad composite codes")
        joined = _plane_join(payload, WORD_SIZES[scode], orig_len)
        return _undelta_core(joined, WORD_SIZES[wcode], bool(zz))
    raise CorruptedError("unknown transform kind %r" % (kind,))


def delta_decode(payload, tmeta):
    """Backwards-compatible single-stage inverse."""
    return transform_decode(payload, tmeta, len(payload))


# STRUCT+LZ: benchmark evidence (2026-08-22) showed dictionary substitution
# plus LZ loses to plain LZ on 5/6 structured corpus files because phrase
# metadata outweighs the gain; the engine therefore maps STRUCT+LZ blocks
# to plain LZ (still lossless).
