# COMPRESSION AUDIT — Transforms & Selection Intelligence

Scope: DELTA, DIVIDE, DICT, RLE and how strategies are chosen. Read-only.

---

## 1. Per-transform assessment

### DELTA (`strategies._delta_encode/_delta_decode`)
- Purpose: turn adjacent equality/gradual drift into zero bytes.
- Algorithm: `out[i] = (b - prev) & 0xFF` — **pure-Python per-byte loop**, O(n) with ~1 µs/B cost.
- Reversibility: exact. Memory: one extra n-sized buffer.
- Typical benefit: PRNG-LCG sample (random_prng.bin → 808 B in exhaustive — the single best DE result in the corpus, beating zlib), sensor_i16 low byte.
- Worst case: cost only (never expands: child can pick RAW).
- Composition: feeds any leaf; composes well with RLE/HUFF; LZ after delta rarely helps.
- Note: `scoring.predict_delta` duplicates this loop on a 16 KiB sample per node — the transform is cheap in C, expensive in Python, twice.

### DIVIDE (`divide_transform.py`)
- Purpose: split little-endian words into high/low planes so sparse high bytes compress separately.
- Algorithm: word size w∈{1,2,4} × divisor d∈{2..65536}; quotient stream + bit-packed remainder stream.
  Fast path only when d is a power of two ≥256 with k%8==0 (d ∈ {256, 65536}): pure slicing `_plane_split` O(n) C-speed. All other divisors: per-word `int.from_bytes` loop + `pack_bits`/`unpack_bits` per-value loops (~1–3 µs/word).
- Reversibility: exact; decoder validates wq/wcode/did, remainder range, word overflow.
- Benefit: counters_u32.bin → 9437 B (16×); sensor_i16.bin → 70900 B.
- Worst case: estimate_candidates pre-filters by static formula, so losing candidates are skipped; beam additionally re-ranks via sampled transforms.
- Composition: quotient/remainder children get full strategy choice (good design). But plane-split for d=4096 falls off the fast path (`k=12`, k%8≠0) — an avoidable 5–10× slowdown on a common case.
- Selection: `estimate_candidates` ranks by raw-size formula only; `predict_divide_candidates` re-ranks top-6 by sampled child entropy. Reasonable heuristics — but final selection still costs full encode attempts (beam caps at 3).

### DICT (`dictionary.py`)
- Purpose: replace frequent phrases with escape+id pairs.
- Algorithm: mine 8- and 12-byte phrases (≤65536 sampled positions each), keep count≥3, greedy non-overlap selection ≤255 sorted by count×len; rarest-byte escape; validation requires ≥1% gain on a 32 KiB prefix.
- Complexity: mining O(positions·slicing); overlap filter O(candidates×chosen); `substitute` is **O(n × |phrase-lens|) Python scan** (two lens), run twice per successful DICT (probe + expand).
- Reversibility: exact; decoder checks id ranges and total size.
- Known inconsistency (still present, dictionary.py:57-63): gain validated with escape chosen from the 32 KiB sample, then escape re-chosen from full data — validated gain doesn't bound final gain. Correctness unaffected; prediction accuracy affected.
- Benefit: big.json root node (n=255 entries); feed.xml/page.html.
- Composition: substituted stream gets full recursion (good). Escape+id doubles the cost of rare escape bytes; no MTF/varint ids.

### RLE (`patterns.py`)
- Regex `(.)\1{2,}` runs; control <128 = literal chunk(1..128), ≥128 = run(3..130).
- The Phase-0 `lit_start` bug is **fixed** at HEAD and guarded by `tests/test_rle.py` (14 cases incl. fuzz). Output bounded by n + n/128 + runs.
- Gate ≥16 KiB samples first 64 KiB (needs a ≥32-run or ≥2% run-bytes).
- Composes well with DELTA (zero streams).

## 2. Is transformation selected intelligently or through expensive search?

**Both.** The P1/P2 machinery (features, probes, predictors) is genuine intelligence and works: beam HEAD evaluates 2.1× input bytes vs 52× exhaustive on big.json with identical output. But:

1. Predictions are heuristics without calibrated error bounds; `PRUNE_MARGIN=1.05` absorbs them empirically. Measured casualties: data.json +5.6%, table.csv +8.4%, random_prng.bin **4.3× worse** than exhaustive (the winning DELTA→RLE chain is pruned because `predict_delta`'s sampled `_child_factor` underestimates the delta stream's post-RLE collapse).
2. Every prediction that involves probing pays real encoding work (LZ core on 16 KiB, divide on 32 KiB ×6, dict mining) — selection cost approaches execution cost.
3. There is no feedback loop: predicted-vs-actual errors are collected only in debug mode and never used to recalibrate.

The structural alternative (not implemented, per audit rules): a deterministic *classifier* choosing one pipeline from measured features, making selection O(features) instead of O(candidates × encode).
