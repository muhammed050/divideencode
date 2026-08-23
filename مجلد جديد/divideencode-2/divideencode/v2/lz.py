"""DivideEncode V2 -- LZ tokenizer (M1).

Produces a deterministic token stream from raw bytes. No entropy coding
and no container framing yet (M2 will add those).

Token kinds
-----------
LIT    (LIT, byte_value)
MATCH  (MATCH, length, distance)      explicit distance, 4 <= length
REP    (REP, length, rep_index)        reuse of a recent distance,
                                       rep_index 0 = most recent distance,
                                       rep_index 1 = the one before it

Determinism: tokenize() is a pure function of (data, parameters); all
candidate scans iterate fixed structures in fixed order.
"""
from ..errors import DivideEncodeError

MIN_MATCH = 4
DEFAULT_MAX_MATCH = 259
DEFAULT_WINDOW = 1 << 18          # 256 KiB
MAX_CHAIN = 32

LIT = 0
MATCH = 1
REP = 2


def _match_length(data, pos_a, pos_b, max_len):
    l = 0
    while l < max_len and data[pos_a + l] == data[pos_b + l]:
        l += 1
    return l


def _best_rep(data, i, reps, window, max_match):
    """Longest rep-offset match at position i; returns (length, rep_idx)."""
    n = len(data)
    limit = min(max_match, n - i)
    best_len = 0
    best_idx = -1
    for idx in range(len(reps)):
        d = reps[idx]
        if d <= 0 or d > i or d > window:
            continue
        src = i - d
        if best_len and best_len < limit and \
                data[src + best_len] != data[i + best_len]:
            continue
        # quick reject just past the current best, then extend
        l = 0
        while l < limit and data[src + l] == data[i + l]:
            l += 1
        if l > best_len:
            best_len = l
            best_idx = idx
            if l >= limit:
                break
    return best_len, best_idx


def tokenize(data, window_size=DEFAULT_WINDOW, max_chain=MAX_CHAIN,
             max_match=DEFAULT_MAX_MATCH, lazy=True):
    """Convert bytes into a deterministic token list."""
    if isinstance(data, bytearray):
        data = bytes(data)
    elif isinstance(data, memoryview):
        data = bytes(data)
    elif not isinstance(data, bytes):
        raise TypeError("tokenize expects bytes-like data")
    if window_size < MIN_MATCH:
        raise ValueError("window too small")

    n = len(data)
    tokens = []
    append = tokens.append
    head = {}
    tget = head.get
    reps = []                        # newest distance first
    low_water = 0                    # positions below this left the window
    i = 0
    while i < n:
        best_len = 0
        best_dist = 0
        best_rep_idx = -1          # >= 0: this candidate reuses reps[idx]

        # ---- repeat offsets first -------------------------------------
        if reps:
            rl, ri = _best_rep(data, i, reps, window_size, max_match)
            if rl >= MIN_MATCH:
                best_len = rl
                best_dist = reps[ri]
                best_rep_idx = ri

        # ---- hash-chain candidate --------------------------------------
        key = None
        if i + MIN_MATCH <= n:
            key = data[i:i + MIN_MATCH]
            chain = tget(key)
            if chain is None:
                head[key] = [i]
            else:
                low_water = i - window_size
                limit = min(max_match, n - i)
                tried = 0
                bl = best_len
                bd = best_dist
                for pos in reversed(chain):
                    if pos < low_water or tried >= max_chain:
                        break
                    tried += 1
                    dist = i - pos
                    if bl >= limit:
                        break          # cannot be beaten
                    if bl >= 1 and data[pos + bl] != data[i + bl]:
                        continue
                    l = _match_length(data, pos, i, limit)
                    if l > bl:
                        bl = l
                        bd = dist
                        if l >= limit:
                            break
                chain.append(i)
                if len(chain) > (max_chain << 3):
                    del chain[:-(max_chain << 2)]
                if bl > best_len:
                    best_len = bl
                    best_dist = bd
                    best_rep_idx = -1

        if best_len >= MIN_MATCH:
            if lazy and i + 1 + MIN_MATCH <= n:
                # one-step lookahead: if the next position clearly starts
                # a longer match, emit a literal here instead.
                nxt_key = data[i + 1:i + 1 + MIN_MATCH]
                nchain = tget(nxt_key)
                nl = 0
                if nchain:
                    limit = min(max_match, n - (i + 1))
                    for pos in reversed(nchain):
                        if i + 1 - pos > window_size:
                            break
                        if nl and data[pos + nl] != data[i + 1 + nl]:
                            continue
                        l = _match_length(data, pos, i + 1, limit)
                        if l > nl:
                            nl = l
                            if l >= limit:
                                break
                if nl > best_len:
                    append((LIT, data[i]))
                    i += 1
                    continue
            if best_rep_idx >= 0:
                idx = best_rep_idx
                if idx:
                    reps[0], reps[1] = reps[1], reps[0]
                append((REP, best_len, idx))
            else:
                if best_dist not in reps:
                    reps.insert(0, best_dist)
                    if len(reps) > 2:
                        reps.pop()
                else:
                    j = reps.index(best_dist)
                    if j:
                        reps[0], reps[j] = reps[j], reps[0]
                append((MATCH, best_len, best_dist))
            i += best_len
        else:
            append((LIT, data[i]))
            i += 1
    return tokens


def apply_tokens(tokens):
    """Reconstruct the original bytes from a token list (test helper)."""
    out = bytearray()
    reps = []
    for tok in tokens:
        kind = tok[0]
        if kind == LIT:
            out.append(tok[1])
            continue
        if kind == MATCH:
            _, length, dist = tok
            if dist < 1 or dist > len(out):
                raise DivideEncodeError("invalid match distance %d" % dist)
            src = len(out) - dist
            for k in range(length):
                out.append(out[src + k])
            if dist in reps:
                j = reps.index(dist)
                if j:
                    reps[0], reps[j] = reps[j], reps[0]
            else:
                reps.insert(0, dist)
                if len(reps) > 2:
                    reps.pop()
        else:  # REP
            _, length, idx = tok
            if idx >= len(reps):
                raise DivideEncodeError("rep offset %d unavailable" % idx)
            dist = reps[idx]
            src = len(out) - dist
            for k in range(length):
                out.append(out[src + k])
            if idx:
                reps[0], reps[1] = reps[1], reps[0]
    return bytes(out)


def token_stats(tokens):
    """Cheap summary used by benchmarks/tests (no format assumptions)."""
    lits = matches = reps = 0
    lit_bytes = 0
    covered = 0
    max_dist = 0
    for tok in tokens:
        if tok[0] == LIT:
            lits += 1
            lit_bytes += 1
        elif tok[0] == MATCH:
            matches += 1
            covered += tok[1]
            if tok[2] > max_dist:
                max_dist = tok[2]
        else:
            reps += 1
            covered += tok[1]
    return {
        "tokens": len(tokens),
        "literals": lits,
        "matches": matches,
        "reps": reps,
        "match_covered": covered,
        "max_distance": max_dist,
        "cost_estimate": lit_bytes * 1 + matches * 3 + reps * 2 +
            ((len(tokens) + 7) // 8),
    }


__all__ = [
    "LIT", "MATCH", "REP", "MIN_MATCH", "DEFAULT_WINDOW",
    "DEFAULT_MAX_MATCH", "MAX_CHAIN",
    "tokenize", "apply_tokens", "token_stats",
]
