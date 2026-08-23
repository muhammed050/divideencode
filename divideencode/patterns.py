import re

from .errors import CorruptedError

MIN_MATCH = 4
MAX_MATCH = 259
WINDOW = 65536
MAX_CHAIN = 32

_RUN_RE = re.compile(rb"(.)\1{2,}", re.DOTALL)

_RLE_GATE_MIN = 16384
_LZ_GATE_MIN = 49152


def rle_encode(data):
    n = len(data)
    if n >= _RLE_GATE_MIN:
        sample = data[:65536]
        run_bytes = 0
        big_run = False
        for m in _RUN_RE.finditer(sample):
            rl = m.end() - m.start()
            run_bytes += rl
            if rl >= 32:
                big_run = True
        if not big_run and run_bytes < len(sample) * 0.02:
            return None
    return _rle_encode_core(data)


def _rle_encode_core(data):
    out = bytearray()
    lit_start = 0

    def flush_literals(lit_end):
        j = lit_start
        while j < lit_end:
            chunk = lit_end - j
            if chunk > 128:
                chunk = 128
            out.append(chunk - 1)
            out.extend(data[j:j + chunk])
            j += chunk

    n = len(data)
    for m in _RUN_RE.finditer(data):
        start = m.start()
        flush_literals(start)
        lit_start = m.end()
        run_len = m.end() - start
        b = data[start]
        # A run must be emitted in chunks of 3..130 (control byte >= 128);
        # a leftover of 1-2 bytes cannot be encoded as a run, so push it
        # back into the literal stream instead.
        rem = run_len % 130
        if rem and rem < 3:
            run_len -= rem
            lit_start = start + run_len
        while run_len > 0:
            take = run_len if run_len <= 130 else 130
            out.append(take + 125)
            out.append(b)
            run_len -= take
    flush_literals(n)
    return bytes(out)


def rle_decode(buf, pos, end, expected_len):
    out = bytearray()
    while len(out) < expected_len:
        if pos >= end:
            raise CorruptedError("rle stream truncated")
        c = buf[pos]
        pos += 1
        if c >= 128:
            run = c - 125
            if pos >= end:
                raise CorruptedError("rle stream truncated")
            out += bytes((buf[pos],)) * run
            pos += 1
        else:
            count = c + 1
            if pos + count > end:
                raise CorruptedError("rle stream truncated")
            out += buf[pos:pos + count]
            pos += count
    if len(out) != expected_len:
        raise CorruptedError("rle produced wrong output size")
    return bytes(out), pos


def _match_length(data, pos_a, pos_b, max_len):
    l = 0
    while l < max_len and data[pos_a + l] == data[pos_b + l]:
        l += 1
    return l


def lz_probe(data, size=16384):
    """Cheap repeatability probe: core-encode one window, return its ratio."""
    n = len(data)
    if n == 0:
        return 1.0
    w = bytes(data[:min(n, size)])
    return len(_lz_encode_core(w)) / max(1, len(w))


def lz_encode(data, pre_approved=False):
    n = len(data)
    if not pre_approved:
        if n >= _LZ_GATE_MIN:
            ok = False
            s1 = bytes(data[:32768])
            if len(_lz_encode_core(s1)) < len(s1) * 0.96:
                ok = True
            else:
                mid = n >> 1
                s2 = bytes(data[mid:mid + 32768])
                if len(s2) >= 1024 and len(_lz_encode_core(s2)) < len(s2) * 0.96:
                    ok = True
            if not ok:
                return None
    return _lz_encode_core(bytes(data))


def _lz_encode_core(data):
    n = len(data)
    if n == 0:
        return b""
    out = bytearray()
    append = out.append
    out.append(0)
    flags_pos = 0
    flags = 0
    cnt = 0
    table = {}
    tget = table.get
    nm = MIN_MATCH
    i = 0
    while i < n:
        best_len = 0
        best_off = 0
        iend = i + nm
        if iend <= n:
            key = data[i:iend]
            chain = tget(key)
            if chain is None:
                table[key] = [i]
            else:
                limit = n - i
                if limit > MAX_MATCH:
                    limit = MAX_MATCH
                low = i - WINDOW
                tried = 0
                bl = 0
                bo = 0
                for pos in reversed(chain):
                    if pos < low or tried >= MAX_CHAIN:
                        break
                    tried += 1
                    if bl < limit and data[pos + bl] != data[i + bl]:
                        continue
                    l = _match_length(data, pos, i, limit)
                    if l > bl:
                        bl = l
                        bo = i - pos
                        if l >= limit:
                            break
                chain.append(i)
                if len(chain) > 4096:
                    del chain[:2048]
                if bl >= nm:
                    flags |= 1 << cnt
                    append(bo & 0xFF)
                    append((bo >> 8) & 0xFF)
                    append(bl - nm)
                    if bl <= 16:
                        stop = i + bl
                        ins_end = n - nm + 1
                        if stop > ins_end:
                            stop = ins_end
                        jj = i + 1
                        while jj < stop:
                            k2 = data[jj:jj + nm]
                            lst = tget(k2)
                            if lst is None:
                                table[k2] = [jj]
                            else:
                                lst.append(jj)
                            jj += 1
                    else:
                        ins_end = n - nm + 1
                        stop = i + bl
                        if stop > ins_end:
                            stop = ins_end
                        jj = i + nm
                        while jj < stop:
                            k2 = data[jj:jj + nm]
                            lst = tget(k2)
                            if lst is None:
                                table[k2] = [jj]
                            else:
                                lst.append(jj)
                            jj += nm
                    i += bl
                    cnt += 1
                    if cnt == 8:
                        out[flags_pos] = flags
                        flags = 0
                        cnt = 0
                        if i < n:
                            flags_pos = len(out)
                            append(0)
                        else:
                            flags_pos = -1
                    continue
        append(data[i])
        i += 1
        cnt += 1
        if cnt == 8:
            out[flags_pos] = flags
            flags = 0
            cnt = 0
            if i < n:
                flags_pos = len(out)
                append(0)
            else:
                flags_pos = -1
    if flags_pos >= 0:
        out[flags_pos] = flags
    return bytes(out)


def lz_decode(buf, pos, end, expected_len):
    out = bytearray()
    while len(out) < expected_len:
        if pos >= end:
            raise CorruptedError("lz stream truncated")
        flags = buf[pos]
        pos += 1
        for bit in range(8):
            if len(out) >= expected_len:
                break
            if flags & (1 << bit):
                if pos + 3 > end:
                    raise CorruptedError("lz stream truncated")
                off = buf[pos] | (buf[pos + 1] << 8)
                length = buf[pos + 2] + MIN_MATCH
                pos += 3
                if off < 1 or off > len(out):
                    raise CorruptedError("lz invalid back reference")
                if len(out) + length > expected_len:
                    raise CorruptedError("lz overrun of expected size")
                src = len(out) - off
                for k in range(length):
                    out.append(out[src + k])
            else:
                if pos >= end:
                    raise CorruptedError("lz stream truncated")
                out.append(buf[pos])
                pos += 1
    return bytes(out), pos
