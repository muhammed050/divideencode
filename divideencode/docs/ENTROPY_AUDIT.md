# ENTROPY AUDIT — `divideencode/huffman.py`

## 1. Implementation facts

- **Model**: order-0 (single symbol frequency table per block), **static** per-block Huffman (two-pass: count, then encode). No adaptivity, no context.
- **Code construction**: heap-based Huffman with insertion-counter tie-breaking (`(f, tie, syms)`), producing deterministic code lengths; symbol lists merged as trees flattened into depth maps.
- **Length limiting**: `MAX_CODE_LEN = 32`; on violation, frequencies are halved (`(f+1)//2`) and the build recurses — converges but can distort the distribution slightly.
- **Canonical codes**: symbols sorted by `(length, symbol)`, standard MSB-first canonical assignment; identical reconstruction in decoder from the (sym,len) list alone. Decoder verifies Kraft equality (`Σ 2^(maxlen−len) == 2^maxlen`) → rejects incomplete/oversubscribed tables.
- **Table serialization**: varint pair-count + 1 byte sym + 1 byte length per used symbol ⇒ ≤513 B worst case, typically ~100–400 B for text (≈95 distinct chars × 2 B).
- **Encoder bit packing**: inline accumulator big-int shifts; for n ≥ 32768 a **65536-entry pairwise lookup table** is built (256×256 loop ≈ 65 k iterations) mapping two bytes to their combined code — amortizes Python overhead to ~0.5 symbol-ops/byte. This table is rebuilt on *every* `encode()` call even when the alphabet barely changes.
- **Decoder**: single-bit-at-a-time walk with per-length first-code/count tables; O(bits) with heavy Python overhead (~8 ops/symbol minimum).
- **Gate** (`_ENTROPY_GATE_MIN = 8192`): computes order-0 entropy H; skips mode if estimated size `n·H/8 + 2·|alphabet| + 8 ≥ n − n/64`.

## 2. Cost profile

- Table generation O(k log k), k ≤ 256 — negligible.
- Encoding: measured 2.76 s cumulative under cProfile across 325 calls on app.js (65 full-file passes worth of data); the pairwise-table rebuild and per-byte indexing dominate.
- Decoding: bit-at-a-time ⇒ ~10× slower than table-driven decode; acceptable at current file sizes.

## 3. Theoretical limitations of the current entropy model

The compressor's total knowledge about a byte is: its own frequency within one block. Explicitly unmodeled:

| Missing concept | Where it costs today |
|---|---|
| Byte context (order-1..n PPM/CM) | text_en.txt 52.6 % vs XZ 33.3 % |
| Positional/columnar structure | table.csv 33.3 % vs XZ 17.1 % |
| Structured records / field repetition | big.json 22.2 % vs XZ 12.0 %; feed.xml/page.html ~2× worse than Brotli |
| Numeric token modeling (digits as numbers) | dump.sql, counters (partially recovered by DELTA/DIVIDE luck) |
| Length/distance distributions of LZ tokens | every LZ leaf: flags+literals+distances stored raw |
| Match repetitions (rep-offsets) | repeated keys/tokens in JSON, source code |
| Local statistics / blocks | one Huffman table per node; no block splitting |
| Adaptive or semi-static higher-order coding | – |

Answer to "what information is the compressor failing to model?": **everything beyond order-0 byte frequencies inside a single contiguous stream** — i.e., all context, all structure, and the entire statistics of its own LZ output.

## 4. Verdict

The Huffman coder itself is correctly implemented (canonical, complete, bounded, validated) and reasonably optimized for pure Python. The limitation is architectural: it exists only as a *standalone leaf* competing in a strategy tree. It never codes LZ streams. In V2 the same effort spent on range/ANS coding of literal/length/distance alphabets subsumes this module entirely.
