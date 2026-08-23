# LZ DEEP AUDIT — DivideEncode

Scope: `divideencode/patterns.py` (`_lz_encode_core`, `lz_encode`, `lz_probe`, `lz_decode`). Read-only.

---

## 1. Exact current implementation

| Property | Value | Evidence |
|---|---|---|
| Match finder | exact 4-byte-slice hash table: `dict[bytes(data[i:i+4])] -> list of positions` | `patterns.py:146-149` |
| Key type | **materialized `bytes` object per probe and per insertion** (no rolling hash) | `patterns.py:147,186,199` |
| Window | 65536 bytes (`low = i - WINDOW`) | `patterns.py:7,155` |
| Min match | 4 | `patterns.py:5` |
| Max match | 259 (len byte 0..255 + 4) | `patterns.py:6,177` |
| Candidate search | newest-first over chain, stop when `pos < low` or `tried >= MAX_CHAIN=32` or `l >= limit`; quick-reject via `data[pos+bl] != data[i+bl]` | `patterns.py:158-169` |
| Chain management | append per position; `del chain[:2048]` when >4096 | `patterns.py:170-172` |
| Parser | **greedy**, no lazy, no optimal, no repeat offsets (no rep0-3), no price model | whole function |
| Insertion after match | len ≤16 → insert every covered position (step 1); len >16 → step 4 from i+4 | `patterns.py:178-205` |
| Flags | 1 flag byte per 8 items, bit i = item i is a match; final partial group patched at end (`flags_pos=-1` sentinel) | `patterns.py:135-136,207-231` |
| Token format | match: `off_lo, off_hi, len-4` (3 B); literal: 1 raw B. Offsets LE, 1..65535 | `patterns.py:174-176`, `lz_decode:248-252` |
| Repeated-distance handling | **none** | — |
| Entropy coding of literals/lengths/distances/flags | **none — all fixed-width** | — |
| Allocation behavior | new `bytes` slice + dict ops per position; output bytearray; chains as Python lists of ints | — |

## 2. Complexity

**Match finding:** worst case O(n · MAX_CHAIN · L̄) where L̄ = mean verified match extension (Python-level byte compares in `_match_length`). Highly repetitive data saturates the 32-candidate cap with long extensions → ~O(32·n) byte comparisons plus O(n) dict operations. Typical text: dict op + ≤32 cheap rejects per position → dominated by constant-factor interpreter overhead (~1–2 µs/byte ⇒ 0.5–1 MB/s).

**Parsing strategy:** greedy only. No lazy step (deflate/zstd/lzma all use lazy/optimal). Cost visible on e.g. `</td><td>` boundaries where taking a 4-byte match blocks a 20-byte one.

**Entropy coding:** none inside LZ. A match costs exactly 3 B regardless of length (a 259-byte match and a 4-byte match cost the same — good); a literal costs exactly 1 B regardless of frequency ('e' costs as much as '€' — bad); distances cost exactly 2 B whether they are rep-offsets or random (bad). Flag bits cost 1/8 B each.

**Representation overhead floor for incompressible-ish regions:** literals ≈ n × 9/8 bits ≈ n×1.125 — LZ alone can *expand* literal-dense data up to +12.5%, which is why the dual-window 96% gates exist (`lz_encode`) and why predictors probe before committing.

## 3. Decoder

Byte-at-a-time overlap-safe copy (`out.append(out[src+k])` — correct for off<length runs but slow: pure-Python loop per byte). Validates: truncation at every read, `off ∈ [1,len(out)]`, no overrun past `expected_len`. Final size must match exactly.

## 4. What this LZ cannot express

- Any statistics of literals (order-0 Huffman exists only as a competing leaf, not combined)
- Length/distance distributions, repeat offsets (rep0 alone typically saves another 8–15% on JSON/code)
- Longer matches (>259) or larger windows (>64 KiB) — big.json's cross-record repeats beyond 64 KiB are unreachable
- Contextual flag cost
- Chunked/independent blocks (one stream, sequential)

Consequence: even a perfect search around this codec bottoms out near deflate-without-lazy quality, which matches the measured gaps (see BENCHMARK_AUDIT).
