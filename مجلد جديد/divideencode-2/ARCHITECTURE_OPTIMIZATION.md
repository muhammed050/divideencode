# DivideEncode — Architecture Optimization Plan

Scope: analysis only. No production code modified.
Working copy: `F:\ff\divideencode-2`. Evidence below was measured on this copy
(Python 3.12, Windows) against `samples/App.java` (100,425 B → 5,526 B,
26.7 s wall-clock compression at depth=3).

---

## 1. Current architecture

```
encoder.compress(data)
 └─ strategies.encode_node(data, depth=3)          # returns best blob, caller prepends mode byte
     ├─ leaf candidates evaluated LOCALLY (no recursion):
     │    ├─ MODE_RAW      : bytes((0,)) + data                      (always)
     │    ├─ MODE_RLE      : patterns.rle_encode                     (always; internal gate n≥16 KiB)
     │    ├─ MODE_HUFFMAN  : huffman.encode                          (always; internal entropy gate n≥8 KiB)
     │    └─ MODE_LZ       : patterns.lz_encode                      (if n ≥ 64; internal gate n≥48 KiB)
     ├─ if depth > 0, recursive children (each child = full encode_node(depth-1)):
     │    ├─ MODE_DELTA   : 1 child   on _delta_encode(data)
     │    ├─ MODE_DIVIDE  : ≤3 candidates × 2 streams (quotient+remainder)
     │    │                 candidate set from divide_transform.estimate_candidates(top_k=3)
     │    └─ MODE_DICT    : 1 child   on substitute(data, mined entries, escape)   (n ≥ 1024)
     └─ best = shortest blob; everything else is discarded
decoder.decode_node mirrors the modes; container adds "DE1", version, varint length, crc32.
```

Key modules:

| File | Role | Hot spots |
|---|---|---|
| `strategies.py` | search / mode dispatch (`encode_node`) | the explosion lives here |
| `patterns.py` | RLE + LZ codecs | `_rle_encode_core`, `_lz_encode_core` |
| `huffman.py` | order-0 Huffman | `Counter(data)` per node, bit loop |
| `divide_transform.py` | word/divisor split, `estimate_candidates` | per-word Python loops on slow path |
| `dictionary.py` | phrase mining + substitution | mining Counter, char-wise `substitute` |

Gates that already exist (added by recent low-level optimizations):

- RLE: if `n ≥ 16384`, regex-scan first 64 KiB; skip unless run bytes ≥ 2 % or a run ≥ 32.
- LZ: if `n ≥ 49152`, probe-encode first 32 KiB (and possibly middle 32 KiB); skip unless probe < 96 % of input.
- Huffman: if `n ≥ 8192`, compute `Counter`; skip if `n·H₀/8 + table + 8 ≥ n − n/64`.
- DIVIDE: `estimate_candidates` ranks `(w, d)` by static size model and returns top 3.

---

## 2. Current search tree

Fan-out per **internal** node (depth > 0):

- 4 local leaf evaluations: RAW, RLE, HUFFMAN, LZ — each is a *full encoder run over the whole node*;
- child subtrees: DELTA(1) + DIVIDE(≤ 3×2 = 6) + DICT(1) = up to **8 recursive children**, each of which again runs all four leaves locally.

Worst-case node count at depth D: `1 + 8 + 8² + … + 8^D` → 585 nodes at depth 3.

Measured on App.java (depth=3), instrumented wrappers, no profiler distortion:

| metric | value |
|---|---|
| `encode_node` calls | **217** |
| depth histogram | {3: 1, 2: 8, 1: 40, 0: 168} |
| bytes fed through encode_node | **5,446,160 B ≈ 54× input** |
| rle_encode calls / bytes / time | 217 / 5.45 MB / **17.53 s** |
| lz_encode calls / bytes / time | 217 / 5.45 MB / 2.10 s |
| huffman.encode calls / time | 217 / 1.94 s |
| build_dictionary calls | 49 (≈ 3.8 s cumulative under cProfile) |
| delta_encode / divide_transform / estimate_candidates | 49 / 59 / 49 calls, ≈ 0.43 s combined |

Even depth-0 leaves pay for RLE+HUFF+LZ: 168 leaves ⇒ ~500 full encoder invocations on
small transformed streams.

What the search actually chose for App.java (root-level candidate sizes):

| strategy | result size | note |
|---|---|---|
| **LZ** | **5,515 B** | winner — final tree is a single LZ node |
| DELTA subtree | 6,261 B | entire subtree explored for +13 % loss |
| DIVIDE w=2 d=256 | 7,822 B | plane-split path |
| DICT | 8,598 B | 255 entries mined |
| DIVIDE w=2 d=128 | 10,645 B | slow-path divisor, bit-packed remainders |
| DIVIDE w=4 d=128 | 11,674 B | slow path |
| HUFFMAN | 52,281 B | |
| RAW | 100,425 B | baseline |
| RLE | **172,268,242 B** | see §4.1 — encoder bug |

The entire 8-way recursive exploration beneath the root produced nothing better
than what plain LZ produced in one pass. The deep tree exists for other input
classes (sensor_i16 → DELTA/DIVIDE, logs → RLE, JSON → DICT), but nothing in the
current architecture checks whether deep exploration is *promising* before paying
for it.

---

## 3. Profiling interpretation

Two independent measurements agree:

1. cProfile (47.9 s with overhead): `flush_literals` 155,395 calls, tottime 14.1 s;
   `bytearray.extend` called **27.3 M times**, 13.4 s. Everything else is far behind.
2. Wrapper timing without profiler (26.2 s total): `rle_encode` = 17.53 s (**67 %**),
   then dict-mine ~1–1.5 s, LZ 2.10 s, Huffman 1.94 s, delta/divide/estimate ≈ 0.43 s.

Interpretation:

- The dominant cost is not the search *count* per se — it is one pathological
  encoder (RLE, §4.1) executed at every node, plus uniform re-scanning of every
  node by every strategy with no cheap rejection beforehand.
- dd.txt's older numbers (RLE 2.1 s) predate this state; gates were added to
  compensate for symptoms while the core defect remained.
- LZ/Huffman gates are themselves expensive: the LZ "gate" probe-encodes 32 KiB
  slices with the *full* matcher — for a 100 KB file the probes cost roughly half
  of just encoding the whole thing. Gates must be cheaper than what they guard.
- `estimate_candidates` (0.15 s for 49 calls) is cheap only because it estimates;
  it cannot see how compressible the quotient/remainder streams will be for real
  children (its top pick w=2/d=256 yields 7,822 B vs LZ's 5,515 B).

---

## 4. Root cause of performance explosion

### 4.1 CRITICAL BUG — `_rle_encode_core` never advances `lit_start` (correctness + performance)

`patterns.py:32`:

```python
def _rle_encode_core(data):
    out = bytearray()
    lit_start = 0
    def flush_literals(lit_end):        # emits data[lit_start:lit_end] ...
        j = lit_start
        while j < lit_end:
            chunk = lit_end - j
            if chunk > 128:
                chunk = 128
            out.append(chunk - 1)
            out.extend(data[j:j + chunk])
            j += chunk
    for m in _RUN_RE.finditer(data):
        start = m.start()
        flush_literals(start)           # re-emits [0:start) EVERY match!
        ... emit run ...
        # BUG: lit_start is never advanced past the run
    flush_literals(n)                   # ... and once more at the end
```

With K runs separated by literals, the literal prefix `[0, start_i)` is re-emitted
before every run i and once more at the end → output size Θ(K·n). Verified:

- `b'xaaabbbc'` (8 B) → 20 B blob that **fails round-trip**
  (`decode_node` raises “rle produced wrong output size”).
- App.java root: 100,425 B → **172,268,242 B** (1714× expansion), 27 M `extend`
  calls — this single bug is 67 % of total compression time.

It is currently *masked*: any input with ≥ 2 non-adjacent runs produces output
> n+1, so RLE always loses the size comparison and is never selected — hence the
test suite still passes. But it (a) burns ~17.5 s per compression here, and (b)
is a latent correctness landmine: any future change that makes RLE win (e.g.
inputs whose bloat stays under RAW+LZ sizes) selects a blob the decoder rejects.

Fix (one line, mandatory regardless of all other work): after emitting a run,
set `lit_start = m.end()`. Expected effect on App.java alone: RLE drops from
17.5 s to well under 0.5 s and produces a valid ~90 KB blob that loses honestly.

### 4.2 Structural causes (after the bug)

1. **Uniform strategy application.** Every node runs RLE, HUFF, LZ regardless of
   content; there are no content-derived rejection tests cheaper than the encoders'
   own gates.
2. **Fixed fan-out, no ranking.** DELTA always recurses; DICT always mines at
   n ≥ 1024; DIVIDE always transforms its top-3 estimates ×2 streams. Nothing
   compares a child's *predicted* value against the parent's already-known best.
3. **No information reuse.** Byte frequencies are recomputed inside
   `huffman.encode`, twice more inside `build_dictionary`/`_choose_escape`;
   features derived for `estimate_candidates` are thrown away; identical streams
   reached via different paths are recompressed from scratch.
4. **Depth-0 waste.** Leaves still execute three encoders even though they can
   never recurse — often the majority of invocations (168 of 217 here).
5. **Estimator blindness.** `estimate_candidates` uses a static size model that
   ignores downstream coding gain; it also admits slow-path divisors
   (everything except d ∈ {256, 65536} takes per-word Python loops; note d=4096,
   k=12, fails the `k % 8 == 0` fast-path test despite being a power of two).

---

## 5. Proposed search architecture

Replace *exhaustive recursion with post-hoc selection* by
*feature-ranked beam search with branch-and-bound*:

```
encode_beam(data, depth):
    feats   = extract_features(data)              # ONE cached O(n) pass (§6)
    scores  = {s: predict_size(s, feats) for s in strategies}   # §7
    order   = rank(scores)
    best    = RAW blob (n+1)                       # safe upper bound, always present
    frontier = []                                  # (predicted_total, node) pairs

    for strat in order[:K_leaf]:                   # only fully encode top-K leaf strategies
        blob = run(strat, data)
        best  = min(best, blob)

    if depth == 0: return best
    for strat in top-K_transform(feats):           # only expand top-K promising transforms
        for stream in transform(strat, data):
            child_best = encode_beam(stream, depth-1)     # memoized (§14)
            best = min(best, header(strat)+child_best)    # with B&B cuts (§9)
    return best
```

Properties:

- Every strategy remains *available*; pruning is rank-based, never a fixed
  blacklist (satisfies “do not randomly remove strategies”).
- Beam width K controls worst case exactly: ≤ K leaf encodes + K transform
  expansions per node instead of 3 leaves + 8 subtrees.
- RAW is always computed → a valid upper bound always exists for B&B.
- Depth semantics preserved; DEFAULT_DEPTH unchanged (the beam, not the depth
  constant, bounds the blow-up).

---

## 6. Cheap feature extraction design

One function, one pass, cached per node (§14). Target budget: ≤ 2–3 % of n in
C-speed primitives (no Python per-byte loops):

```python
features(n, data):
    freq   = Counter(data)                     # C-speed; REUSED by huffman + dict escape
    k      = len(freq)
    H0     = -Σ p·log2 p                       # order-0 entropy
    top_c  = max(freq.values())
    zero_f = freq.get(0, 0) / n                # zero-byte density (DIVIDE/LZ hint)
    ascii_f= printable fraction via strided slice check
    runs   = sampled run stats from _RUN_RE on strided 32 KiB window(s)
             → run_bytes_frac, big_run_seen, run_count_per_kb
    rep4   = 4-gram repeat rate: build set of strided data[i:i+4], count hits of
             a second strided sample against it   # O(n/64) hash ops, LZ hint
    delta_eq = fraction of sampled adjacent-equal bytes (DELTA hint)
    for w in (2, 4):
        hi_zero[w] = fraction of sampled words whose high bytes are 0 (DIVIDE hint)
```

Sampling discipline: windows at `{0, n/2}` capped at 32 KiB each (mirrors existing
gate sampling, so behaviour is stable for inputs where old gates fired).

Cost estimate for n = 100 KB: Counter ~1 ms, regex windows ~1 ms, 4-gram sets
~2 ms, word scans via `array`/slicing ~1 ms → **≈ 5 ms vs current ~26 s**.

---

## 7. Candidate scoring formulas

All predictors return an *estimated final size* so they are directly comparable
with actual blob lengths (calibration constants to be fitted on the corpus, §17):

| strategy | predictor | rationale |
|---|---|---|
| RAW | `n + 1` | exact |
| HUFFMAN | `n·H₀/8 + 2k + 6` | exact payload bound + serialized table; already used as gate — promote to score |
| RLE | `n·(1−run_bytes_frac) · 1.008 + 2·run_count_est + 2` | literals stay literal; each run costs ~2 B per ≤130 symbols |
| LZ | probe ratio extrapolation: `n · len(core(probe))/len(probe) + 8` on ONE 16 KiB probe (replaces today's dual 32 KiB probes) | match-model too complex to model analytically in pure Python; probe is honest |
| DELTA | `predict_huff(delta_sample)`: delta a 16 KiB sample, Counter, apply Huffman formula | delta pays off only if it turns data into low-entropy streams |
| DIVIDE(w,d) | sample-based: compute qmax/wq/rbits on 32 KiB sample → `n_s·wq + n_s·rbits/8` scaled to n; bonus factor `(1 − hi_zero[w])` penalty since high-zero words mean the stream will squash under LZ anyway | extends today's estimator with child-compressibility signal |
| DICT | sampled mine on 32 KiB: `Σ cᵢ·(lenᵢ−2) − Σ(lenᵢ+1) − 4` for phrases with cᵢ ≥ 3 | savings minus entry table cost |

Ranking rule: sort ascending; any strategy whose score exceeds `min(best_known,
RAW)` by more than a margin (e.g. ×1.05) is not fully encoded unless it is among
the top-K. Predictors are **heuristic** (see §15) — margins must absorb their error.

---

## 8. Beam-search design

- State: `(node_data, depth, predicted_total_size)`.
- Expansion of one node:
  1. extract features (cached);
  2. fully encode top `K_leaf = 2` leaf strategies by score, plus RAW;
  3. take top `K_branch = K−1` transforms by score; recurse into each child stream.
- Widths to evaluate: **K = 2, 3, 4** (dd.txt request). K=4 reproduces nearly all
  of today's beneficial choices on structured data while cutting worst case from
  585 nodes to ≤ 4·4·4 = 64 expansions.
- Sibling merging: DIVIDE emits 2 streams; count them as ONE expansion slot so a
  single DIVIDE choice costs one beam unit, matching the mental model “one strategy”.
- Termination: unchanged (depth countdown); small-input gates (MIN_LZ_LEN etc.)
  unchanged.
- Tie-breaking: prefer cheaper-to-evaluate strategy on equal scores
  (RAW > HUFF > RLE > LZ > others) to reduce variance.

Measured headroom for K: on App.java the correct decision needs only the LZ
score to outrank 7 alternatives — even K=2 suffices there; sensor-style inputs
need DELTA or DIVIDE ranked top-2, which their feature vectors produce easily
(high `delta_eq`, high `hi_zero`).

---

## 9. Branch-and-bound opportunities

Mathematically safe cuts (provable, keep unconditionally):

1. **Upper bound exists**: RAW gives `best ≤ n+1` before anything else runs.
2. **DIVIDE child cut**: when evaluating DIVIDE candidates sequentially, after
   building `head + qnode` we know `total ≥ len(head)+len(qnode)+1` because any
   encoded remainder node is ≥ 1 byte (mode byte). If that already ≥ best →
   skip the remainder subtree entirely. Safe: rnode ≥ 1 B holds for every mode.
3. **Completed-blob cut**: any finished blob ≥ best is discarded (already implicit).
4. **Huffman payload floor**: huffman output ≥ `serialize_table + ceil(Σ fᵢ·lᵢ /8)`
   where code lengths satisfy Kraft equality; computing exact minimum needs the
   tree, but `ceil(n·H₀/8)` is NOT a strict floor for the emitted bits when
   lengths are constrained… therefore treat as heuristic (below), except the
   trivial floor `len(blob) ≥ 2k + ceil(payload_bits/8)` once lengths are known —
   usable mid-encode to abort early, which is an implementation-level bound.
5. **Ordering cut**: evaluate cheapest-predicted first; once a candidate's
   partial bytes exceed best, stop (valid for any strategy whose output is built
   monotonically, true for all six).

Heuristic bounds (documented as such, tuned with margin):

- entropy-floor prune: skip strategy if `predicted_size(strategy) > best·1.02`.
- transform-chain prune (§E of dd.txt): measurable conditions only, e.g.
  skip DELTA→DELTA when `delta_eq(delta_stream) < threshold` (second differences
  of non-smooth data are noise); skip DIVIDE→DIVIDE when child has no zero-byte
  mass (`zero_f < 0.01`); these are *gating heuristics*, not proofs.

---

## 10. DIVIDE candidate pruning

Current: static `nw·wq + nw·rbits/8`, top 3, both streams always recursed.

Proposed:

1. Score by §7 predictor on a 32 KiB sample; admit at most `top_k = 2` candidates
   into the beam, and only those with score < best·0.95.
2. Restrict divisor sweep analytically:
   - power-of-two d with `log2(d) % 8 == 0` (256, 65536) hit the C-speed
     `_plane_split` path → cheap, keep;
   - d=4096 currently takes the slow path purely due to `k % 8 != 0`; either add
     a fast path (low byte-plane + nibble-plane via slicing) or drop it from
     defaults — measured candidates on text never choose it;
   - divisors 3 and 5 only when `w ≥ 2` AND sampled quotient distribution is
     concentrated (IQR of sampled quotients < xmax/4): these are the classic
     16-bit-sensor cases; for byte text they never win.
3. Apply safe cut §9.2 between q and r children.
4. Skip DIVIDE entirely when `hi_zero[w] < 0.02` for all w and `rep4` is low —
   quotient/remainder splitting of incompressible data cannot create structure
   worth more than the 4-byte header (heuristic, margin-covered).

---

## 11. LZ candidate pruning

Current gate: two full 32 KiB probe encodes at n ≥ 48 KiB — often ~50 % of the
cost of just encoding.

Proposed:

1. Feature pre-filter: if `rep4 < 0.04` and `run_bytes_frac < 0.02` → LZ cannot
   amortize its flag-bit overhead (~12.5 % on literal-dense streams); skip.
   (Heuristic; calibrated threshold from corpus.)
2. Replace dual 32 KiB probes with one 16 KiB probe at offset 0; extrapolate (§7).
   Probe result doubles as the score for beam ranking — no extra work.
3. Keep MIN_LZ_LEN=64; below that LZ flags overhead makes it structurally unable
   to beat RAW (safe: each 8 tokens carry 1 flag byte).
4. Inside `_lz_encode_core`, the hash chain cap (MAX_CHAIN=32, WINDOW=64 KiB)
   stays; no format change required.

---

## 12. Huffman candidate pruning

1. Promote today's entropy gate into the scoring phase: `score = n·H₀/8 + 2k + 6`
   using the SHARED frequency table (§14) — zero extra scanning.
2. Skip full encode when score ≥ best·1.02 (heuristic margin covers Huffman's
   ≤ ~2 % inefficiency vs ideal entropy on typical inputs).
3. Never run `huffman.encode` at depth-0 leaves unless it ranked top-K_leaf —
   removes most of the 217 invocations seen today.
4. Table-size guard stays: k ≤ 16 makes tables tiny; k > 200 on small n makes
   Huffman structurally unable to beat RAW (safe arithmetic: table ≥ 400 B +
   payload ≥ n·(8−ε)/8).

---

## 13. Dictionary candidate pruning

Current cost drivers: mining Counter over ≤ 65 K sampled slices ×2 lengths,
O(entries²) overlap filter, char-wise `substitute`, validation inconsistency
(validates on first 32 KiB with sample-chosen escape, then re-mines escape on
full data — the validated gain does not bound the delivered blob).

Proposed:

1. Feature pre-filter: mine only when sampled long-phrase evidence exists —
   e.g. `rep4 > 0.10` AND ascii/text-like (`ascii_f > 0.85` or SQL/JSON markers),
   else skip. (Heuristic; DICT on binary rarely wins — verified: App.java DICT
   8,598 B vs LZ 5,515 B despite 255 mined entries.)
2. Mine on a 32 KiB sample only; estimate savings by §7 formula; require
   estimated savings > 5 % of n before full mining + substitution.
3. Fix the escape/validation inconsistency (§15): choose escape once from the
   same window used for validation, and validate on the FULL substituted length
   prediction, or simply accept sample-based estimate as heuristic score.
4. Replace `substitute()` inner loop with Aho-Corasick-lite: single pass over
   data consulting a dict keyed by 2-byte prefix of entries (entries are ≥ 8 B,
   so prefix indexing is lossless) → ~5–10× faster in pure Python.
   Format-compatible: same token stream.

---

## 14. Caching strategy

Safe, correctness-preserving caches (keyed by immutable content, never mutated):

| cache | key | contents | invalidation |
|---|---|---|---|
| feature vector | `id(bytes)` within one compress call (+ `hash` fallback) | §6 struct | end of `compress()`; bytes objects are immutable → safe |
| freq table | same | share ONE Counter between huffman score, huffman encode, dict escape | same |
| delta sample stats | same | `delta_eq`, sampled post-delta entropy | same |
| encode_node result | `(content_sha1_8B, depth)` | final blob for that subtree | per-compress lifetime |
| estimate_candidates | `(id(data))` | sorted candidate list | per-node lifetime |

Memoization justification: `bytes` objects in the pipeline are immutable and the
search never mutates them; two paths reaching equal content (e.g. DELTA of a
constant region vs RAW slice) get identical results, so returning the cached blob
is observationally equivalent. Memory bound: LRU cap (e.g. 256 entries) with
byte-length-weighted eviction; peak memory stays ≪ current (which already holds
every sibling blob alive until `consider()` finishes).

---

## 15. Correctness risks

| risk | class | mitigation |
|---|---|---|
| RLE `lit_start` bug ships latent corrupt blobs | **correctness (today)** | fix immediately; add regression test `roundtrip(b'xaaabbbc')`, multi-run fuzz |
| beam/pruning drops the true optimum | quality, not losslessness | output is always SOME valid encoding (RAW fallback) → decompression safety unaffected; measure ratio regression on corpus |
| predictors mis-rank unusual data | quality | margins (×1.02–1.05), K configurable, keep exhaustive path behind `--exhaustive` flag for A/B |
| caching stale across calls | correctness | per-`compress()` lifetime only; keys include depth |
| DICT escape/validation mismatch | correctness-adjacent | unify escape selection window (§13.3) — decoder already handles any escape byte, so this is size-prediction risk, not decode risk |
| transformation-restriction heuristics wrong | quality | each restriction gated on measurable condition + can be disabled individually |
| `recursive_compress` re-compresses containers | none (works today) | out of scope |

Losslessness statement: every proposed mechanism only chooses WHICH valid
encoding to emit; the decoder contract (`decode_node`) is untouched. The single
true correctness hazard in the codebase is §4.1.

---

## 16. Expected complexity before/after

Let n = input size, D = depth (=3), F = fan-out (≤8 subtrees + 3 local leaves).

**Before:** nodes N ≈ O(F^D) bounded by gates (measured 217); work per node =
c_rle·n + c_lz·n + c_huff·n with pure-Python constants; total
Θ(N·n) = Θ(54n) encoder-bytes scanned here, dominated by buggy-RLE constant.
Wall: ~26 s @100 KB; scales linearly in n per node and exponentially in D.

**After:** nodes ≤ 1 + K·D expansions (K=3 → ≤ 10 subtrees + ~3 leaf encodes);
per node: features O(n) with C-speed primitives + ≤ 2 full encoder runs on
ranked winners. Total ≈ Θ(K·D·n) encoder-bytes ≈ 9n vs 54n, and the eliminated
RLE bug alone removes 67 % of current wall time.

Projected App.java: features ~5 ms + LZ probe 16 KB ~10 ms + full LZ ~30 ms +
rejected-candidate scores ~5 ms → **≈ 0.1 s (≈ 250× speedup)**, ratio expected
within ~1 % of current 5,526 B (identical tree likely chosen).
Structured inputs (sensor/log/sql) retain deep trees at K=3–4 with estimated
3–8× speedup; worst case (all-pruned) degrades gracefully toward “LZ-or-RAW”
behaviour, still lossless.

These are design-time estimates; §17 validates them.

---

## 17. Benchmark plan

Corpus (existing `samples/` + additions): App.java, main.py, program.c,
lib.rs, engine.cpp, app.js/ts, page.html, feed.xml, data.json, big.json,
dump.sql, notes.md, server.log, table.csv, text_en.txt, doc.pdf, photo.{jpg,png,webp},
archive.zip, random_os.bin, random_prng.bin, sensor_i16.bin, counters_u32.bin
(+ suggest: enwik-like text ≥ 1 MB, UTF-8 mixed-language, base64 blob).

Protocol:

1. Baseline capture on current HEAD: size, c-time, d-time, sha-verified round-trip
   (`benchmark.py` already does this; store CSV).
2. Stepwise patches, each independently benchmarked + fuzz-tested:
   P0 RLE fix → P1 shared features/freq cache → P2 scorers replacing gates →
   P3 beam K∈{2,3,4} → P4 DIVIDE restrictions → P5 LZ/DICT/HUFF prunes → P6 B&B cuts.
3. Metrics: compressed size delta vs baseline per file (accept ≤ +1 % median,
   ≤ +3 % worst), c-time speedup, d-time unchanged, peak mem.
4. Statistical hygiene: 3 repetitions, min-of times, fixed Python build;
   report per-file, not just aggregate (guards “optimize only for App.java”).
5. Correctness gate per patch: full unittest suite + new RLE multi-run regression +
   1000-case round-trip fuzz (random, structured, adversarial: many-runs,
   no-runs, all-zero, 129-byte boundaries, escape-heavy for DICT).
6. Acceptance: sub-second for text/source @100 KB at K=3; no file regresses
   > 3 % size; all tests green; `--exhaustive` path retained for parity studies.

---

## Appendix A — Proposed encoder pseudocode

```python
def compress(data, depth=DEFAULT_DEPTH, K=3):
    header(MAGIC, VERSION, len(data), crc32(data))
    return emit(encode_beam(data, depth, K))

def encode_beam(data, depth, K, cache):
    if id(data) in cache:  return cache[id(data)]          # §14 memo

    f = features(data)                                     # §6, one cached pass
    best = MODE_RAW_BLOB(data)                             # safe upper bound
    upper = len(best)

    # ---- rank every strategy by predicted FINAL size (§7)
    preds = {
        'huff':  f.n*f.H0/8 + 2*f.k + 6,
        'rle':   rle_predict(f),
        'lz':    lz_probe16k(data),                        # probe doubles as score
        'delta': huff_formula(delta_stats(f)),
        'dict':  dict_sample_predict(f),
        'divide': divide_sample_predicts(f),               # list per admitted (w,d)
    }

    # ---- fully encode only top-K_leaf leaves that beat the bound with margin
    for s in top_k(['huff','rle','lz'], K_leaf=2):
        if preds[s] <= upper * 1.02:
            best = shorter(best, wrap(s, run_encoder(s, data)))
            upper = len(best)

    if depth == 0:
        cache[id(data)] = best; return best

    # ---- expand top-K transforms, cheapest-first (enables B&B inside loops)
    transforms = rank_transforms(preds)[:K]
    for t in transforms:                                   # 'delta' | 'dict' | ('divide',w,d)
        parts = apply_transform(t, data)                   # 1 stream, or q+r pair
        head  = transform_header(t, parts)
        acc   = len(head)
        for stream in parts:                               # q then r; dict/delta have 1 part
            lb   = 1                                       # ANY encoded node ≥ 1 byte
            if acc + lower_bound_stream(stream, f) >= upper:   # heuristic LB + margin
                break                                      # §9 ordering cut (heuristic form)
            child = encode_beam(stream, depth-1, K, cache)
            acc += len(child)
            if acc >= upper and stream is last(parts):
                break                                      # cannot improve; discard
        else:
            cand = head + concat(children)
            if len(cand) < upper:
                best, upper = cand, len(cand)
            continue
        if acc < upper:                                    # completed early via break
            cand = head + concat(done_children) + MIN_REMAINDER
            if len(cand) < upper: best, upper = cand, len(cand)

    cache[id(data)] = best
    return best
```

Safety annotations in the pseudocode mirror §9: `upper` from RAW is provable;
stream lower bounds marked `heuristic` carry margins; every returned blob is a
complete, decodable encoding regardless of which cuts fire.

## Appendix B — Immediate action list (for later implementation)

1. **P0 (mandatory, correctness):** fix `lit_start` in `_rle_encode_core`;
   add regression tests. Alone: ~67 % wall-time reduction on App.java-class inputs.
2. P1 shared feature/frequency pass + memoization (§6, §14).
3. P2 scorer module replacing in-encoder gates (§7).
4. P3 beam driver with K=2/3/4 switch (§8), default K=3.
5. P4–P6 strategy-specific prunes + B&B cuts (§10–§13, §9).
