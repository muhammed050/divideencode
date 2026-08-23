from collections import Counter

from .errors import CorruptedError

MIN_PHRASE_LEN = 8
MAX_ENTRIES = 255
ESCAPE_LITERAL = 255


def _choose_escape(data, cnt=None):
    if cnt is None:
        cnt = Counter(data)
    best = 0
    best_count = None
    for v in range(256):
        c = cnt.get(v, 0)
        if best_count is None or c < best_count:
            best_count = c
            best = v
            if c == 0:
                break
    return best


def build_dictionary(data, full_freq=None):
    n = len(data)
    if n < MIN_PHRASE_LEN * 8:
        return None, None
    counts = Counter()
    for plen in (12, 8):
        if n < plen * 4:
            continue
        limit = min(n - plen + 1, 65536)
        end = n - plen + 1
        stride = max(1, end // limit)
        idxs = range(0, end, stride)
        counts.update(data[i:i + plen] for i in idxs)
    candidates = [(s, c) for s, c in counts.items() if c >= 3]
    if not candidates:
        return None, None
    candidates.sort(key=lambda t: -(t[1] * len(t[0])))
    chosen = []
    chosen_set = set()
    for s, _c in candidates:
        if len(chosen) >= MAX_ENTRIES:
            break
        skip = False
        for t in chosen_set:
            if s in t or t in s:
                skip = True
                break
        if not skip:
            chosen_set.add(s)
            chosen.append(s)
    if not chosen:
        return None, None
    chosen.sort(key=len, reverse=True)
    sample = bytes(data[:32768])
    escape = _choose_escape(sample)
    sub = substitute(sample, chosen, escape)
    if len(sub) > len(sample) - max(32, len(sample) // 100):
        return None, None
    escape = _choose_escape(data, full_freq)
    return chosen, escape


def substitute(data, entries, escape):
    lens = sorted({len(p) for p in entries}, reverse=True)
    ids = {p: i for i, p in enumerate(entries)}
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        matched = False
        for l in lens:
            if i + l <= n:
                pid = ids.get(data[i:i + l])
                if pid is not None:
                    out.append(escape)
                    out.append(pid)
                    i += l
                    matched = True
                    break
        if not matched:
            b = data[i]
            if b == escape:
                out.append(escape)
                out.append(ESCAPE_LITERAL)
            else:
                out.append(b)
            i += 1
    return bytes(out)


def expand(tokens, entries, escape, expected_len):
    out = bytearray()
    i = 0
    nt = len(tokens)
    while i < nt:
        b = tokens[i]
        if b == escape:
            if i + 1 >= nt:
                raise CorruptedError("dict token stream truncated")
            v = tokens[i + 1]
            i += 2
            if v == ESCAPE_LITERAL:
                out.append(escape)
            else:
                if v >= len(entries):
                    raise CorruptedError("dict id out of range")
                out += entries[v]
        else:
            out.append(b)
            i += 1
    if len(out) != expected_len:
        raise CorruptedError("dict expansion produced wrong size")
    return bytes(out)
