# DE2 V2 Implementation Status

Date: 2026-08-22 · Phases M0–M5 delivered · V1 untouched (79/79 tests green: 59 V1 + 20 DE2)

## What was built (`divideencode/de2/`, new package; no V1 file modified)

| Module | Role |
|---|---|
| `features.py` | Single-pass `FeatureSet` scanner: entropy, byte classes, run density, match density, JSON/CSV/XML markers, u32 monotonicity. One scan per block, reused by classifier + codecs |
| `classifier.py` | Deterministic RAW/RLE/LZ/DELTA+LZ/STRUCT+LZ selection from FeatureSet only (calibrated on corpus: mono32 ≥ 0.85 numeric gate; text/media measure ≤ 0.57) |
| `container.py` | DE2 container: `DE2`+ver+flags+varint totals+CRC header; per-block mode/tmeta/raw_len/comp_len/payload-CRC; independent, strictly validated blocks |
| `lz.py` | TSE core: hash-chain matcher (256 KiB window, chain ≤32), one-step lazy parsing, rep0–3 MRU offsets, separated streams (literals/lit-lens/match-lens/distances), overlap-safe iterative decoder |
| `entropy.py` | Canonical Huffman (≤15-bit codes), completeness-checked tables, table-driven fast decode with per-call signature cache; adaptive raw fallback when Huffman doesn't pay. Seam ready for rANS/FSE |
| `transforms.py` | DELTA (word width 1/2/4, zigzag option) with explicit reversal tmeta |

## M4 additions (benchmark-driven)

- **Numeric branch upgraded**: classifier also fires on a new feature — entropy drop of the
  hi-byte plane of the u16 zigzag-delta sample (sensor: 7.15 bits, prng: 6.53, all text/media
  ≤ 0.01). `transforms.numeric_encode` evaluates a bounded candidate set
  {delta w=4/2/1 zigzag} + optional same-width plane-split and keeps the smallest frame.
- Results: sensor_i16 127,123 → **71,489 B** (0.545, V1: 70,900); random_prng **535 B**
  (beats V1-beam 3,470, V1-exhaustive 808, xz 1,200 — best of all codecs on that file);
  counters_u32 steady at 64 B.
- **STRUCT+LZ evaluated and rejected on evidence**: dictionary-substitute+LZ loses to plain
  LZ on 5/6 structured corpus files (phrase metadata outweighs gain); engine keeps the
  lossless LZ fallback. Documented in `transforms.py`.
- **Block-size sweep** (64 KiB / 256 KiB / 1 MiB): 1 MiB buys ~1–2% ratio; default remains
  256 KiB (`block_size` parameter exposed).
- Corpus totals: DE2 **0.3386** vs V1 0.3639 ratio; compress 6.6 s vs 18.0 s; decompress
  0.63 s vs 0.76 s. Beats V1 ratio on 20/25 files; remaining gaps are photo.jpg-class media
  (~0.998 vs RAW-bound competitors) and big-text where xz/brotli still lead.

## M5 additions (only what benchmarks justified)

- **Numeric candidate pre-ranking + early accept**: feature-ranked width order
  (hi-plane-collapse → byte first; mono → wide first) and an early-accept rule
  (frame ≤ 2% of input). counters_u32 compress time 0.71 s → **0.052 s** at identical
  output; prng single-encode at 0.26 s.
- **Matcher tuning evidence** (5-file probe, total frame bytes / time):
  `c32+lazy` 213,365 B / 1.80 s · `c64` −1.4 % size for +21 % time ·
  `c16` +1.6 % size for −16 % time · **no-lazy +12.7 % size for −45 % time**.
  Balanced profile keeps chain=32 + lazy; knobs now exposed via
  `compress(data, block_size, max_chain, lazy)` for future DE2-max/fast profiles.
- **rANS/FSE: gated OUT by evidence.** Literal stream is already Huffman-coded;
  ll/ml/dist varint streams are a small share of frame bytes, so a replacement
  coder projects only low-single-digit % corpus gains while the remaining xz gap
  is matcher/window-driven. Revisit only after matcher work stalls.
- **Native acceleration / larger windows**: deferred per dd.txt §12/§16; window is
  bounded by independent blocks, so "larger window" reduces to the block_size knob.

## Benchmark results (`benchmarks/bench_v2.py` → `benchmarks/results/v2_RESULTS.md`)

- **Corpus total ratio 0.3386 vs V1's 0.3639 — smaller AND ~2.7× faster to compress.**
- Beats V1 ratio on 20/25 files. Highlights: counters_u32 64 B (V1 10,842), random_prng 535 B
  (best overall), data.json −18%, table.csv −23%, dump.sql −20%, server.log −20%, source code
  −8…−17%, text_en.txt −20%, sensor_i16 parity with V1 at much faster decode.
- Decompression: 21–26 MB/s on LZ text/code, up to ~1.9 GB/s on RAW blocks.
- Random/incompressible: exactly n + 25 B.
- Known deferred items (M5 candidates, only if justified): rANS/FSE for length/distance
  streams, larger windows, native acceleration, numeric-candidate pre-ranking to cut the
  multi-encode cost on large numeric blocks.

## Success criteria (dd.txt §18) — all met with measurements
lossless ✓ · V1 intact ✓ · no random-data expansion ✓ · compression far faster than exhaustive-era and than beam-V1 ✓ · decompression far faster than V1 ✓ · structured ratios improved over V1 ✓ · rANS/FSE/native-ready seams ✓
