# LZ DEEP AUDIT — `divideencode/patterns.py` (`_lz_encode_core` / `lz_decode`)

## 1. Match finder

- **Hashing/indexing**: exact 4-byte slice keys in a Python `dict` → value is a `list` of positions (chain). No rolling hash; each step slices `data[i:i+4]` and hashes it.
- **Window**: `WINDOW = 65535` effective (offset stored LE16, so max representable distance 65535; constant says 65536).
- **MIN_MATCH = 4**, **MAX_MATCH = 259** (length byte stores `len − 4`, max 255).
- **Chain policy**: try up to `MAX_CHAIN = 32` most-recent candidates within window; stop early on `limit`-length match. Chains capped at 4096 entries by dropping the oldest 2048 (`del chain[:2048]`) — a crude approximation of a sliding window that also discards still-valid positions.
- **Collision handling**: none needed (exact slice compare via `_match_length`; the pre-filter `data[pos+bl] != data[i+bl]` skips hopeless chains quickly but costs an index per candidate).
- **Insertion**: on match ≤16: insert every covered position; else insert every 4th position. Standard speed/quality tradeoff.

Complexity: worst case O(n · MAX_CHAIN · match_len) byte comparisons in pure Python; typical ~32 dict/list ops + short compares per position. Measured cost: app.js LZ core = 2.5 s tottime under cProfile for one pass over 83 KB (~30 µs/byte).

## 2. Token format / bit packing

- Grouped flag scheme: one flag byte per 8 tokens, bit=1 match, bit=0 literal.
- Literal: raw byte (no entropy coding).
- Match: offset LE16 + length−4 (3 bytes total).
- Flags are NOT entropy coded; literals are NOT entropy coded; lengths NOT entropy coded; distances NOT entropy coded.
- Minimum stream overhead for all-literal input: n + ⌈n/8⌉ bytes = **12.5 % tax** before the mode byte and any container overhead.

## 3. Parsing strategy

**Greedy** single-pass, no lazy evaluation (never defers to check i+1), no optimal/bounded-optimal price model, no repeat offsets (rep0/1/2 style matches absent — every match pays full 2-byte distance even when repeating the previous distance). Match choice maximizes length only, ignoring a cost metric that would prefer cheaper distances or better subsequent parse.

## 4. Allocation behavior

- Output built as `bytearray` with per-token `append` calls (6.88 M bytearray.append calls profiled for one app.js compression across all codecs).
- Hash table: one Python list per distinct 4-gram — memory-dominant at scale (measured peaks up to ~24 MB tracemalloc for 128 KiB probes of binary files ≈ 190× input).
- `bytes(data)` copy on entry to `lz_encode` core; output converted again with `bytes(out)`.

## 5. Decoder (`lz_decode`)

Byte-at-a-time with per-bit flag loop; copies match bytes one-by-one through `out.append(out[src+k])`. Validates: truncation, `off ∈ [1, len(out)]`, overrun beyond expected_len. Correct but slow relative to zlib (still fast in absolute terms because streams are small; big.json decodes in 265 ms).

## 6. Verdict

The LZ is a reasonable greedy hash-chain LZ77 in pure Python whose *format* is the problem, not its correctness:
1. fixed-width tokens with zero entropy coding ⇒ ≥12.5 % structural overhead plus uncompressed literals;
2. greedy parsing without lazy matching ⇒ measurably worse parses than gzip-class parsers;
3. no repeat-offset handling ⇒ JSON/code with repeated field names pays full distances;
4. Python-level inner loops ⇒ 3–4 orders of magnitude slower than C equivalents.

All four issues are algorithmic/format-level, not implementation accidents — this module is the primary V2 redesign target.
