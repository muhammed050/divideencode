"""DE2 LZ/TSE core (M1/M2): the heart of V2.

Design goals honored from dd.txt §5-§6, §11, §13:
- hash-based match finding over a configurable window (default 256 KiB)
- minimum match 4, lengths varint-coded (259+ supported naturally)
- bounded probing (max_chain), fast insertion, trimmed chains
- one-step lazy parsing with an explicit cost comparison
- repeat offsets rep0..rep3 with MRU updates
- SEPARATED token streams: literals / literal-lengths / match-lengths /
  distances -- entropy coding touches only the literal section today,
  and the framing leaves room to code every stream later without
  rewriting the parser.

Distance stream entries are varints with reserved values:
    0..3  -> rep0..rep3 (most-recently-used order maintained)
    >= 4  -> explicit distance (value - 3)

The decoder is a flat iterative state machine: strict bounds checks,
overlap-safe copies, exact stream accounting, no recursion.
"""
from ..bitstream import encode_varint as _wv, decode_varint as _rv
from ..errors import CorruptedError
from . import entropy

MIN_MATCH = 4
DEFAULT_WINDOW = 262144          # 256 KiB
MAX_CHAIN = 32                   # bounded probing
LAZY = True                      # one-step lazy parsing
CHAIN_HARD_LIMIT = 256           # per-chain storage trim


def _match_length(data, a, b, limit):
    """Length of common prefix of data[a:] and data[b:], capped."""
    l = 0
    while l + 8 <= limit and data[a + l:a + l + 8] == data[b + l:b + l + 8]:
        l += 8
    while l < limit and data[a + l] == data[b + l]:
        l += 1
    return l


def _find(data, i, n, reps, table, window, max_chain):
    """Best match starting at i. Returns (length, dist, rep_index|-1)."""
    best_len = 0
    best_dist = 0
    best_rep = -1
    limit = n - i
    if limit < MIN_MATCH:
        return 0, 0, -1

    # ---- repeat-offset candidates (cheap, checked first) ----
    di = data[i]
    for k in range(4):
        d = reps[k]
        if d > i or data[i - d] != di:
            continue
        l = _match_length(data, i - d, i, limit)
        if l > best_len:
            best_len = l
            best_dist = d
            best_rep = k
            if l >= limit:
                return l, d, k

    # ---- hash-chain candidate ----
    key = data[i:i + MIN_MATCH]
    chain = table.get(key)
    if chain is None:
        return best_len, best_dist, best_rep
    low = i - window
    tried = 0
    bl = best_len
    bd = 0
    for pos in reversed(chain):
        if pos < low or tried >= max_chain:
            break
        tried += 1
        if bl < limit and data[pos + bl] != data[i + bl]:
            continue
        l = _match_length(data, pos, i, limit)
        if l > bl:
            bl = l
            bd = i - pos
            if l >= limit:
                break
    if bl > best_len:
        return bl, bd, -1
    return best_len, best_dist, best_rep


def encode(data, window=DEFAULT_WINDOW, max_chain=MAX_CHAIN, lazy=LAZY):
    """bytes -> DE2 LZ frame (separated streams, see module docstring)."""
    n = len(data)
    literals = bytearray()
    ll_out = bytearray()
    ml_out = bytearray()
    dist_out = bytearray()
    num_matches = 0
    reps = [1, 2, 4, 8]
    table = {}
    tget = table.get
    i = 0
    lit_start = 0
    nm = MIN_MATCH

    while i < n:
        best_len, best_dist, best_rep = _find(
            data, i, n, reps, table, window, max_chain)

        # ---- lazy one-step: prefer literal now if a longer match follows
        if lazy and best_len >= nm and i + 1 <= n - nm \
                and best_len < n - i:
            nl, nd, nr = _find(data, i + 1, n, reps, table,
                               window, max_chain)
            if nl > best_len:
                if n - i >= nm:
                    key = data[i:i + nm]
                    chain = tget(key)
                    if chain is None:
                        table[key] = [i]
                    else:
                        chain.append(i)
                i += 1
                continue

        if best_len >= nm:
            # ---- emit token ----
            literals += data[lit_start:i]
            ll_out += _wv(i - lit_start)
            ml_out += _wv(best_len - nm)
            if best_rep >= 0:
                dist_out.append(best_rep)
                if best_rep:
                    reps.pop(best_rep)
                    reps.insert(0, best_dist)
            else:
                dist_out += _wv(best_dist + 3)
                reps.pop()
                reps.insert(0, best_dist)
            num_matches += 1
            # ---- index covered interior positions ----
            stop = i + best_len
            ins_end = stop
            max_ins = n - nm + 1
            if ins_end > max_ins:
                ins_end = max_ins
            step = 1 if best_len <= 8 else (2 if best_len <= 32 else 4)
            j = i + 1
            while j < ins_end:
                k2 = data[j:j + nm]
                ch2 = tget(k2)
                if ch2 is None:
                    table[k2] = [j]
                else:
                    ch2.append(j)
                    if len(ch2) > CHAIN_HARD_LIMIT:
                        del ch2[:CHAIN_HARD_LIMIT // 2]
                j += step
            i = stop
            lit_start = i
        else:
            # ---- literal ----
            if n - i >= nm:
                key = data[i:i + nm]
                chain = tget(key)
                if chain is None:
                    table[key] = [i]
                else:
                    chain.append(i)
                    if len(chain) > CHAIN_HARD_LIMIT:
                        del chain[:CHAIN_HARD_LIMIT // 2]
            i += 1

    literals += data[lit_start:]

    frame = bytearray()
    frame += _wv(num_matches)
    frame += _wv(len(literals))
    frame += entropy.encode_stream(bytes(literals))
    frame += _wv(len(ll_out))
    frame += entropy.encode_stream(bytes(ll_out))
    frame += _wv(len(ml_out))
    frame += entropy.encode_stream(bytes(ml_out))
    frame += _wv(len(dist_out))
    frame += entropy.encode_stream(bytes(dist_out))
    return bytes(frame)


def decode(blob, pos, end, raw_len, tables):
    """Decode an LZ frame. Returns (data, new_pos)."""
    num_matches, pos = _rv(blob, pos, end)
    lit_count, pos = _rv(blob, pos, end)
    literals, pos = entropy.decode_stream(blob, pos, end, lit_count, tables)

    lls = []
    mls = []
    ds = []
    ap_ll = lls.append
    ap_ml = mls.append
    ap_d = ds.append
    slen, pos = _rv(blob, pos, end)
    sblob, pos = entropy.decode_stream(blob, pos, end, slen, tables)
    p = 0
    for _ in range(num_matches):
        v, p = _rv(sblob, p, slen)
        ap_ll(v)
    slen, pos = _rv(blob, pos, end)
    sblob, pos = entropy.decode_stream(blob, pos, end, slen, tables)
    p = 0
    for _ in range(num_matches):
        v, p = _rv(sblob, p, slen)
        ap_ml(v)
    slen, pos = _rv(blob, pos, end)
    sblob, pos = entropy.decode_stream(blob, pos, end, slen, tables)
    p = 0
    for _ in range(num_matches):
        v, p = _rv(sblob, p, slen)
        ap_d(v)
    if pos != end:
        raise CorruptedError("lz frame has excess data")

    out = bytearray()
    reps = [1, 2, 4, 8]
    li = 0
    for t in range(num_matches):
        ll = lls[t]
        if ll:
            if li + ll > lit_count:
                raise CorruptedError("lz literal run exceeds pool")
            out += literals[li:li + ll]
            li += ll
        ml = mls[t] + MIN_MATCH
        e = ds[t]
        olen = len(out)
        if e <= 3:
            d = reps[e]
            if d < 1 or d > olen:
                raise CorruptedError("lz invalid repeat distance")
            if e:
                reps.pop(e)
                reps.insert(0, d)
        else:
            d = e - 3
            if d < 1 or d > olen:
                raise CorruptedError("lz invalid distance")
            reps.pop()
            reps.insert(0, d)
        if olen + ml > raw_len:
            raise CorruptedError("lz overrun of block size")
        src = olen - d
        if d >= ml:
            out += out[src:src + ml]
        else:
            while ml > 0:
                chunk = d if d < ml else ml
                base = len(out) - d
                out += out[base:base + chunk]
                ml -= chunk

    if li > lit_count:
        raise CorruptedError("lz literal pool not fully consumed")
    out += literals[li:]
    if len(out) != raw_len:
        raise CorruptedError("lz produced wrong block size")
    return bytes(out), pos
