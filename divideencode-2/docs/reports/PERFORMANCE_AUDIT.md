# PERFORMANCE AUDIT — Beam Search, Hot Paths, Bottlenecks, Memory

Scope: P0/P1/P2/P3 machinery (`strategies.py` search config, `features.py`, `scoring.py`), benchmark implementation, hot paths, memory. Read-only; all measurements taken on this machine without modifying code.

---

## 1. Beam-search architecture audit (P0–P3 state at HEAD)

Configuration (`strategies.py:36-41`): `SEARCH_MODE="beam"`, `BEAM_K=3` (transform expansions per node), `LEAF_K=2` (leaf encoders fully evaluated), `PRUNE_MARGIN=1.05`. `DEFAULT_DEPTH=3`.

| Question | Answer |
|---|---|
| Beam width | 3 transform children expanded per node; **not** a classical k-best beam over partial parses — it is per-node best-first pruning against the exact RAW upper bound |
| Branching factor | 7 strategy kinds; leaves {rle,huff,lz}, transforms {delta, divide×top3, dict} |
| Depth | ≤3 recursion levels (each level halves-ish candidate count via gates) |
| Candidate count / node | ≤2 leaves + ≤3 transforms + RAW |
| Predictor behavior | only `raw` is exact; huff/rle are formulas from features; lz uses cached 16 KiB probe ratio; delta sampled `_child_factor`; dict gated (`rep4>0.06 && (printable>0.60 ‖ zero>0.5)`) + 32 KiB mine probe; divide re-ranked from static top-6 via sampled transforms |
| Pruning | leaves: skip candidates with `pred > best×1.05` (continue); transforms: sorted ascending, **break** on first over-margin; `expand_divide` has a safe mid-cut after computing the quotient child (`head_len > len(best)` ⇒ remainder child never built) |
| Tie handling | stable sort by prediction (insertion order) |
| Speculative evaluation | quotient-child encoding before the head-length cut (partial waste) |
| Budget system | none — no time/evaluation budget, only structural limits |
| Duplicate work | memo `(id(data),depth)→blob`; probe caches for lz/dict predictions; freq cache shared huff/dict |
| Cache behavior | caches keyed by `id()` with strong refs in values; memo cleared **wholesale** at 1024 entries |

### Total candidate evaluations per input byte (measured)

| Mode | bytes seen / input | nodes | example |
|---|---|---|---|
| beam (HEAD) | **≈2.1×** | 4 | big.json: 890,902 B seen / 421,249 B |
| exhaustive | **≈52×** | 173 | big.json: 22,112,709 B seen |

### Why compression was extremely slow on App.java/app.js/app.ts/big.json (stale logs)

The published times (24.8 s / 9.8 s / 9.6 s / 43.7 s) are **exhaustive-era numbers**. At every one of 217–450 nodes the engine fully ran rle+huff+lz plus delta plus up-to-3 divides plus dict mining; pre-P0 the RLE `lit_start` bug made RLE alone emit O(n²) bytes (17.1 s of 25.8 s on App.java). At HEAD (beam) the same files measure **0.16 s / 0.15 s / 0.15 s / 1.67 s** with identical output sizes (see BENCHMARK_AUDIT). The premise "beam search makes compression extremely slow" is therefore obsolete; residual slowness is proportional probing (§3) plus interpreter constants.

### Computational model vs reference codecs

| Codec | Model | Passes over data |
|---|---|---|
| zlib | single greedy/lazy parse, fixed cost model, integrated Huffman | 1 |
| Brotli | block-wise, context-mixed literal modeling, built-in dictionary | 1 (+dict) |
| Zstd | greedy/lazy seq store + FSE/Huff per stream, split literal/match streams | 1 |
| LZMA2 | optimal parse via price model over binary range coder | ~2 |
| DE exhaustive | full encode of **every** codec combination per tree node | ~52× |
| DE beam (HEAD) | predicted-pruned subset per node | ~2.1× |

DE's search dimension is *codec composition*, which none of the references have — that is why naive exhaustive evaluation explodes, and why pruning (or a classifier, §5) is the right shape of fix.

## 2. Benchmark implementation audit

`benchmarks/benchmark.py`: times full `compress()` (features+probes+search+CRC+container — correct: measures what users pay) and full `decompress()`; asserts round-trip before recording; **tracemalloc peak probe runs after the timed region** on a 128 KiB prefix (does not contaminate timing, but doubles DE work per row). `baseline.py` monkey-patches strategy functions with timers — wrapper overhead is negligible, but note it patches `S.encode_node` and `enc_mod.encode_node` separately because the root binding bypasses attribute patching. Contamination risks found: none material in timing; the real issue is **staleness** (all retained result files predate beam mode — see BENCHMARK_AUDIT) and competitor coverage existing for only 6 files.

## 3. Hot paths (current beam HEAD)

1. `_lz_encode_core` full-buffer run whenever LZ wins (Python loop per position).
2. `predict_lz` → `lz_probe` = full LZ core over a 16 KiB window **per distinct node**.
3. `predict_delta` = Python delta loop over 16 KiB per node (duplicated `_delta_encode` logic).
4. `predict_divide_candidates` = up to 6 × `divide_transform` on 32 KiB samples per node + `_child_factor`.
5. `extract_features` = full-file `Counter(data)` per node (C-speed but O(n)); rep4 builds thousands of 4-byte slices; `delta_eq_frac` is a Python zip-loop over 32 KiB.
6. `build_dictionary` mining (≤128 K slices) + `substitute` O(n·lens) scan — executed twice per successful DICT (probe on prefix, then full).
7. `huffman.encode` pair-table = 65,536-entry build per call when n≥32 KiB.
8. `divide_transform` general path = per-word `int.from_bytes` loop; `pack_bits/unpack_bits` per-value loops.
9. `_delta_encode`/`_delta_decode` pure-Python byte loops.
10. Huffman decode = one `BitReader.read_bit()` method call **per output bit**.
11. `estimate_candidates._word_max` w=4 = Python loop over n/4 words.

## 4. Top 20 bottlenecks (ranked)

| # | Location | Operation | Est. cost | Why expensive | Future solution | Impact | Difficulty |
|---|---|---|---|---|---|---|---|
| 1 | patterns.py:127-232 | LZ inner loop (dict ops, slice keys, chain walk) | ~1–2 µs/B | interpreter + per-position bytes alloc | new tokenizer w/ u32 hash array (numpy/array) or C ext | 3–10× comp speed | med-high |
| 2 | scoring.py:70-78 + patterns.lz_probe | 16 KiB LZ probe per node | O(16K·chain) per node | real encode as prediction | share probe result with leaf decision; cheaper signature (hash-count) | 20-40% total | med |
| 3 | scoring.py:105-129 | sampled delta loop per node | O(16K) py/node | duplicated logic | numpy diff / reuse `_delta_encode` | 5-15% | low |
| 4 | scoring.py:132-154 | 6 sampled divides per node | O(6·32K/w) py | full transform for ranking | rank on features only; sample 8 KiB | 10-30% on numeric | med |
| 5 | dictionary.py:67-92 | substitute linear scan (×2 incl. expand) | O(n·2) py | per-byte lens loop | regex alternation of phrases (single C pass) | DICT nodes 5-20× | low |
| 6 | dictionary.py:25-64 | phrase mining + overlap filter | 128K slices + O(c²) | generator of slices, quadratic filter | strided Counter on ints; suffix-array-lite | gated nodes 2-5× | med |
| 7 | huffman.py:107-120 | 65,536-entry pair table per call | O(64K) per encode ≥32K | rebuilt every call | cache keyed by code table | 5-10% on text | trivial |
| 8 | divide_transform.py:76-104 | general-path word loop | O(n/w) py | per-word int.from_bytes | fast path for all pow-2 d (incl. 4096); struct.unpack bulk | numeric 3-8× | low |
| 9 | divide_transform.py:137-173 | pack/unpack_bits per value | O(n/w) py | accumulator loop | chunked int.from_bytes / numpy bitunpack | numeric 2-4× | med |
| 10 | strategies.py:65-80 | delta encode/decode loops | O(n) py | per-byte | int.from_bytes+numpy diff | sensor/bin 5-10× | low |
| 11 | huffman.py:191-204 + bitstream.py:71 | bit-at-a-time decode | ~0.5-1 µs/bit | method call per bit | canonical table-lookup decode (peek 8 bits) | dec 5-15× | med |
| 12 | patterns.py:256-257 | LZ decode byte-at-a-time copy | O(len) py | append per byte | bytearray slicing for non-overlap; memoryview ring | dec 2-6× | low |
| 13 | features.py:71 | full-file Counter per node | O(n) per node | whole buffer counted | count on 64 KiB window only (H0 estimate suffices) | 10-25% | trivial |
| 14 | features.py:110-131 | rep4 gram-set build | O(win/3) slices | per-slice bytes objects | hash update via int.from_words | 5-10% | low |
| 15 | divide_transform.py:22-27 | `_word_max` w=4 loop | O(n/4) py | per-word loop | array('I') + max | 5% | trivial |
| 16 | features.py:208-211 | memo clear-on-full | pathological thrash >1024 live nodes | wholesale clear | LRU instead | rare spikes | trivial |
| 17 | features.py:158-200 | id()-keyed caches pin all intermediates | memory (§5) | strong refs keep buffers alive | weakref + explicit key objects | peak RAM ÷ 3-10 | med |
| 18 | encoder.py:33-43 | recursive_compress re-runs full search/pass | ×passes | whole pipeline per pass | compose containers natively or drop feature | niche | low |
| 19 | scoring.py:193-219 vs strategies.py:191-206 | dict mined twice (probe + expand) | 2× mining | no result sharing between predict & expand | return mined dict via ctx | DICT nodes 2× | low |
| 20 | strategies.py:142-162 | leaf ranking sorts + lambda per node | O(k log k) small const | per-node closure creation | hoist; minor | 1-3% | trivial |

## 5. Memory audit

**Peak compression memory** is dominated not by any single structure but by **cache pinning**: `feature_cache`, `freq_cache`, `probe_cache`, and `memo` all store `(data_ref, value)` tuples, keeping *every distinct intermediate buffer ever created during the search* alive until `compress()` returns (delta copies, divide q/r streams, dict substitutions, samples). For big.json exhaustive that summed to tens of MB; beam reduces the number of intermediates ~25× but the pinning pattern remains.

Other consumers:
- LZ hash table: one `bytes` key object (~53 B overhead) per unique 4-gram + list-of-int chains (trimmed at 4096→2048) ⇒ ~2–6 MB per MB of text.
- Huffman pair table: 65,536-slot list ≈ 0.5 MB transient per encode call ≥32 KiB.
- Beam state: only the incumbent blob per node is retained (best-of, not k-best) ⇒ modest.
- DIVIDE/DICT/DELTA: one extra n-sized buffer each, transiently several simultaneously at one level.
- Decoder: output bytearray grown by append (LZ/RLE byte loops); `divide_reconstruct` validates child sizes *before* allocating, so malformed input cannot force oversized allocations; worst attacker leverage ≈ input-size-bounded.
- Unnecessary copies found: `bytes(data)` normalization at entry, repeated `data[:N]` sample materializations, `_plane_split` joins building then copying again in `_plane_join`.

tracemalloc peaks are recorded by `benchmark.py` (128 KiB prefix) but no retained artifact holds them — recommend capturing them in the next benchmark run.
