# COMPRESSION AUDIT — Transforms & Strategy Search (V1)

## 1. Transform inventory

### DELTA (`strategies._delta_encode/_decode`)
- Purpose: turn slowly-varying byte sequences into low-entropy streams.
- Algorithm: byte-wise prefix difference, mod 256.
- Complexity O(n), memory 2n transient; reversible exactly.
- Benefit: strong on sensor_i16/counters *after* DIVIDE splits planes (sensor_i16 quotient → DELTA → HUFF); near-useless alone on text/code but still **always evaluated** (unconditional recursion) — pure wasted time when it loses to RAW.
- Composition: good with DIVIDE/HUFF; poor with LZ (delta destroys matches).

### DIVIDE (`divide_transform.py`)
- Purpose: split fixed-width little-endian words into high part (quotient by d) + low part (remainder mod d), compressing each plane separately. This is the project's signature idea and its genuine competitive advantage.
- Algorithm: for power-of-two d with k=bits(d) divisible by 8 and w>k/8: pure byte-plane slicing (`_plane_split`, fast). General case: Python integer div/mod per word + bit-packing of remainders (`pack_bits`).
- Complexity: fast path ~O(n); slow path O(n_words · w) with per-word int conversions; `estimate_candidates` adds O(Σ n/w) scans plus a sort.
- Reversibility: exact — decoder validates q-size, r-size, remainder range < d, word overflow ≥ 2^(8w).
- Benefit: **measured wins**: counters_u32.bin 9437 B vs XZ 21084 B (−55 %); sensor_i16.bin 70900 B vs Brotli 89271 B (−21 %); random_prng.bin via DELTA→DICT→LZ chain 808 B vs XZ 1200 B. These are the only corpus files where DE beats all competitors.
- Worst case: candidate estimator filters to top-3 by static size estimate; wrong estimates waste two full subtree encodes each. On text-like data zero candidates pass the `est < n` filter (app.js: 0 divide calls).
- Composition: excellent with DELTA+HUFF/LZ on numeric binaries; irrelevant for text.

### DICT (`dictionary.py`)
- Purpose: dictionary substitution of frequent phrases (8/12-byte windows sampled ≤65536 positions), ≤255 entries, escape-coded.
- Complexity: mining O(sampled positions × Counter); substitution O(n × #distinct-lengths) with slice-hash probes — measured 3.88 s cumulative (29 % of profiled app.js compression!) despite DICT almost never winning outside random_prng.bin.
- Reversibility: exact; decoder validates entry count ≤1020, ids in range, output size equality.
- Benefit: narrow — helps only PRNG-ish data where LZ fails but short tokens recur (random_prng.bin: 808 B, best in corpus). The viability gate (≥1 % saving on first 32 KiB sample) is evaluated *after* full mining, so cost is paid regardless.
- Worst case: escape byte collision handling doubles escape-class bytes; entries may be suboptimal due to greedy longest-first selection.

### RLE (`patterns.rle_encode`)
- Packard-style control codes: runs ≤130 (c≥128), literal chunks ≤128.
- Gate: for n≥16 KB, regex run-density sample on first 64 KB; skip unless big run or >2 % run bytes.
- Benefit: all-zero/repeated test data (tests assert ratio <1 %); negligible on real corpus (never selected in benchmark trees except implicitly through children of DIVIDE tails).
- Reversibility: exact; decoder accepts run codes 126–127 that encoder never emits (harmless asymmetry).

## 2. Are transforms chosen intelligently?

No — selection is purely **exhaustive size-based search** (`encode_node` keeps the smallest serialization). There is no classifier, no feature extraction, no cost model predicting which transform suits the data; every node pays full price for every codec. Consequences:

- app.js spends ~29 % of profiled time inside DICT machinery that never appears in any winning tree;
- DELTA recursion runs unconditionally at every internal node even when it obviously inflates (text);
- the search does find good answers when they exist within its vocabulary (single-leaf winners dominate), but pays orders of magnitude more than needed to find them.

## 3. Strategy-search ("beam") audit

- Terminology note: there is no beam. This is an **exhaustive depth-limited search over strategy trees**: width = all candidates (up to ~12 subtrees/node), depth ≤3, pruning only by size gates (`MIN_LZ_LEN=64`, `MIN_DIVIDE_LEN=32`, `MIN_DICT_LEN=1024`) and `estimate_candidates` top-3.
- Candidate count per input byte (measured): app.js 325 tree nodes / 83 KB ≈ 4 nodes per KB; each node runs up to 4 leaf codecs over its full range ⇒ ~70 LZ passes + ~65 Huffman passes over the data per file.
- No memoization: identical byte ranges appearing as children of different candidates are re-encoded from scratch; no result cache keyed by (bytes, depth).
- Speculative evaluation: none — every candidate fully serialized before comparison (serialization itself is the cost).
- Budget system: none (no time/effort budget, no early exit once a good-enough candidate exists).
- Tie handling: strict `<` keeps the first-found (RAW-biased).

## 4. Why big.json / data.json / server.log take 30–60 s

Multiplicative stack-up on ~400 KB inputs:
1. many internal nodes (large files produce deep DIVIDE/DICT subtrees with multiple candidates);
2. each node re-runs LZ (Python hash-chain match finder ~30 µs/B) + Huffman (with 65 k-entry pairwise table rebuild per call) + RLE + DICT mining;
3. DICT mining/substitution is O(expensive) even when gated out late;
4. zero cross-node caching ⇒ total bytes processed ≈ 50–70× file size for LZ alone;
5. pure-Python inner loops everywhere (~50–200× slower than C per operation).

zlib/Brotli/Zstd do one pass with C kernels and entropy-coded output; DE does dozens of passes with Python kernels and raw-output codecs. That is the entire gap.
