# V2 DESIGN ANALYSIS — diagnosis, design space, architectures, ranking, roadmap

## 1. Fundamental architectural diagnosis (Q1–Q8)

**Q1 — implementation quality or algorithmic design?**
Both are weak, but the *binding constraint is algorithmic design*. The Python code is competent for pure Python (gates, canonical Huffman, plane-split fast path), yet sits 10³–10⁴× behind C compressors — an unbridgeable gap for any Python-kernel design. More importantly, the *format* caps ratio: LZ tokens are stored raw (12.5 % flag tax + raw literals + fixed-width distances) and the entropy stage is order-0-only. No amount of implementation polish removes either ceiling.

**Q2 — will more beam-search optimization improve ratio?**
No. The search already finds near-optimal trees within its vocabulary: winners are single leaves (app.js → one LZ node; text_en.txt → one HUFFMAN node) or shallow DIVIDE trees. The candidate vocabulary itself is what's poor. Expected gain from smarter search alone: <1–2 %.

**Q3 — will beam-search optimization improve speed?**
Yes, meaningfully but not orders of magnitude: memoization/caching of subtree results, feature-gating DICT/DELTA before full evaluation, and serialize-winner-only could plausibly deliver 2–5× with zero ratio change. Beyond that requires a different engine.

**Q4 — missing compression concepts?**
Entropy-coded LZ streams (separate literal/length/distance alphabets); range coding / ANS; context modeling (>order-0); lazy & optimal parsing; repeat offsets (rep-matches); block splitting + independent streams; sliding-window chain maintenance; structured-record/columnar modeling; numeric token modeling; static dictionaries; BWT/MTF.

**Q5 — three biggest reasons DE trails Zstd/Brotli/LZMA2?**
1. **No entropy coding of LZ output** — every match costs 3 raw bytes + flag bit; literals cost 1 byte + flag bit.
2. **Order-0-only statistics applied only to whole blocks** — no contexts, no adaptivity, never applied to token streams.
3. **Greedy single-pass parsing without rep-offsets** vs their lazy/optimal parsers — structurally worse parses even at equal format efficiency.
(Honorable mention: pure-Python kernel ⇒ the speed dimension is lost regardless.)

**Q6 — first component to redesign?**
`patterns.py` LZ → a modern LZ77 tokenizer emitting separate literal/length/distance streams into a real entropy coder. This single change addresses Q5-1/2/3 simultaneously.

**Q7 — preserve?**
The DE1 container discipline (magic/version/varint/CRC/trailing check); `errors.py`; test architecture; benchmark harness + corpus; DIVIDE transform as a *pre-filter* for numeric binary (its proven niche: counters/sensor files where it beats everyone); DELTA as a cheap pre-transform candidate; the trace/inspect tooling.

**Q8 — deprecate eventually?**
Standalone HUFF leaf (subsumed by entropy-coded streams); DICT in current form (superseded by rep-offsets + static dictionary); exhaustive `encode_node` search (replaced by classifier + fixed pipeline); BitWriter/parse_table/CompressResult dead code.

## 2. Design-space evaluation (A–H)

| Option | Ratio | Speed | Mem | Complexity | Python-suitability | Decoder risk | JSON | Source | Binary |
|---|---|---|---|---|---|---|---|---|---|
| A. Modern LZ77 + entropy-coded lit/len/dist | high (+15–25 % vs V1 on text) | med (C ext helps most) | low-med | med-high | med (needs C ext or numpy for speed) | med | ++++ | ++++ | ++ |
| B. Context modeling + arithmetic/range coding | very high per-byte | slow in pure Python | med | high | poor w/o C ext | med | ++ | ++ | ++ |
| C. Structured-data modeling + multi-stream | very high on tables/JSON | med | med | high | good (column split is array-friendly) | low-med | +++++ | ++ | + |
| D. BWT+MTF+RLE+entropy | high on text | decode OK, encode memory-heavy | high | med | med-poor | low | + | ++ | + |
| E. LZ + context modeling hybrid | highest | slowest | high | very high | poor w/o C ext | high | +++++ | ++++ | ++ |
| F. Optimal/near-optimal parsing | +3–8 % over lazy | expensive (SSP ~O(n·window)) | high | high | poor | n/a (encoder-only) | ++ | ++ | + |
| G. Repeat offsets | +5–12 % on structured text | nearly free | none | **low** | **excellent** | low | +++++ | ++++ | + |
| H. Multi-stream block architecture | neutral–small win, enables parallelism + locality | scales with cores | med | med | good (multiprocessing) | low | ++ | ++ | ++ |

Best value-per-effort ordering for this project: **G → A → H → C**, with B/E deferred until a C extension exists.

## 3. Three complete V2 architectures

### V2-A "DE2-classic" — modern LZ pipeline (recommended)
```
input ─► block splitter(≤256 KB blocks)
      ─► optional pre-transforms (DIVIDE|DELTA|none, chosen by cheap classifier)
      ─► greedy/lazy LZ77: hash-chain matcher + rep-offsets {R0,R1,R2}
      ─► streams: literals[] | lit-lengths meta-block | match lengths | distances(+rep flags)
      ─► per-stream Huffman (canonical, like deflate) ── later swap-in rANS
      ─► frame: [DE2 magic][ver][block table][per-block stream headers][CRC32C]
decoder: symmetric, table-driven Huffman, bulk copy() match execution
```
- Format: self-describing blocks; each block: mode byte, optional pre-transform id, stream lengths, Huffman table headers.
- Parser: lazy-1 (deflate-style) initially; optimal parsing hook left behind cost API.
- Memory: O(window 64 KB × chains) per block; bounded, streaming-friendly.
- Complexity: moderate; ~2–3 kLOC Python + optional C accelerator module.
- Expected ratio: parity-to-better vs zlib-9 on text/code (V1 loses by 23–52 % there); keeps DIVIDE edge on numeric binaries via pre-transform slot.
- Expected speed (pure Python): still 50–200× slower than zlib; with C inner loop: 5–20× slower than zlib, competitive with zstd -1..3.
- Risks: bitstream/table bugs (mitigate by reusing validated canonical machinery); parser regressions caught by existing corpus.

### V2-B "DE2-cm" — context-modeling range coder
```
input ─► optional DIVIDE/DELTA
      ─► order-1/order-2 context mixer (primary ctx = previous byte class; secondary = position mod stride)
      ─► binary-decomposed symbols through adaptive arithmetic/range coder
      ─► frame: [DE2][ctx config][model params][payload][CRC]
decoder mirrors model exactly (bit-exact adaptive state)
```
- Ratio: strongest per-byte modeling in pure Python terms; can approach xz on text if mixed well.
- Speed: poor in pure Python (~100 KB/s–1 MB/s class); decode equally slow (adaptive).
- Risk: highest — decoder must replicate encoder state perfectly; any drift = corruption.
- Verdict: research track; only viable after C extension or as offline "maximum" mode.

### V2-C "DE2-struct" — structure-aware column compressor
```
input ─► record detector (delimiter/newline/JSON brace scanning, sampled)
      ─► field/column splitter + per-column type inference (int/float/enum/timestamp/text)
      ─► per-column codec: numeric→delta+divide+rANS; enum→dictionary; text→LZ(V2-A engine)
      ─► multiplexed streams + schema header
      ─► frame: [DE2][schema][column table][streams][CRC]
```
- Ratio: potentially beats XZ on table.csv/big.json/dump.sql class data (their worst losses today); falls back to V2-A engine on unstructured input.
- Complexity: high (schema inference must be conservative + fully reversible); decoder needs schema interpreter.
- Speed: good (column arrays are batch-friendly, numpy-accelerable).
- Risk: medium (fallback path bounds damage); big win exactly where DE currently fails hardest.

## 4. Weighted ranking

Criteria: ratio 30 %, comp-speed 25 %, decomp-speed 15 %, complexity 10 %, correctness risk 10 %, Python suitability 10 %. Scores 1–10 per criterion.

| Architecture | Ratio(30) | CompSpd(25) | DecSpd(15) | Simplicity(10) | LowRisk(10) | PySuit(10) | **Total** |
|---|---|---|---|---|---|---|---|
| V2-A classic LZ | 7 (210) | 6 (150) | 8 (120) | 6 (60) | 7 (70) | 7 (70) | **68.0** |
| V2-B context/range | 9 (270) | 2 (50) | 3 (45) | 2 (20) | 3 (30) | 3 (30) | 44.5 |
| V2-C structural | 9 (270) | 6 (150) | 7 (105) | 3 (30) | 5 (50) | 7 (70) | 67.5 |

Ranking: **1st V2-A (68.0)**, 2nd V2-C (67.5, statistical tie — preferred once V2-A engine exists), 3rd V2-B (44.5). Recommended sequence: build V2-A now; evolve it into V2-C's fallback engine; treat V2-B as post-C-extension research.

## 5. Roadmap M0–M9

| Milestone | Objective | Files affected | Tests required | Benchmark gate | Success criteria | Rollback |
|---|---|---|---|---|---|---|
| M0 | Freeze V1: tag release, fix F1 recursion cap (raise CorruptedError beyond depth N), remove dead code, add malformed-stream tests | decoder.py, strategies.py, tests/ | new corruption tests incl. deep-nesting | rerun suite once, archive results | all green; baseline CSV archived | git tag v1-freeze |
| M1 | New LZ/tokenizer: hash-chain + rep-offsets, emits 4 streams, still size-scored against V1 codecs | patterns.py or new lz2.py, strategies.py | round-trip property tests + corpus fixtures | corpus A/B vs V1 sizes | ≤ V1 sizes on ≥90 % corpus files | keep V1 LZ selectable |
| M2 | Entropy-coded streams: per-stream canonical Huffman (later rANS swap) | huffman.py, new streams.py | table-completeness fuzzing, truncation tests | corpus A/B | −10 % avg size vs M1 on text/code | disable entropy stage via flag |
| M3 | DE2 container: block framing, block table, CRC32C, versioned mode registry | encoder.py, decoder.py, new container.py | container fuzzing, forward-compat rejection tests | full rerun | byte-exact determinism; all corpus round-trips | DE1 reader kept forever |
| M4 | Classifier: cheap features (alphabet, run-rate, match-rate probe) select pipeline instead of exhaustive search | new classify.py, strategies.py | misclassification safety tests (fallback always valid) | time-per-file comparison | ≥2× mean speedup at ≤1 % ratio regression | force-search escape hatch (--exhaustive) |
| M5 | Improved parsing: lazy-1, then bounded-optimal SSP option | lz engine | golden-parse regression tests | ratio deltas per file | −3 % vs greedy on code/json | parser=greedy default toggle |
| M6 | Structured modeling: record/column splitter feeding V2-A engine (V2-C path) | new struct.py | schema-reversibility property tests | table.csv/big.json/data.json focus | beat XZ on ≥2 of 3 structured files | auto-fallback to plain blocks |
| M7 | Benchmarking: repetitions (median-of-5), competitor time columns in CSV, CI job | benchmarks/benchmark.py | – | – | stable, reproducible reports | – |
| M8 | Fuzzing/security: random-mutation + structure-aware fuzz targets, amplification limits, depth caps everywhere | tests/fuzz/, decoder guards | OSS-Fuzz-style local campaign | – | zero uncaught exceptions under 10⁶ mutations | – |
| M9 | Optimization: profile-guided; optional C extension for match finder + range coder; multiprocessing block encoding | extensions/, build scripts | ABI + fallback-pure-Python tests | final report vs §6 targets | hit realistic targets below | pure-Python fallback retained |

## 6. Performance targets for V2 (grounded in current numbers)

Baseline (current): avg ratio 0.332; median compression 2298× slower than deflate-9; worst structured losses +66…+208 % vs Brotli/XZ.

### Compression ratio
| Target | Definition | Plausibility |
|---|---|---|
| Conservative | avg ratio ≤ 0.31 (match deflate-9 mean); eliminate all >+40 % outliers vs ZIP | entropy-coded streams alone |
| Realistic | avg ≤ 0.28; structured files within +10 % of XZ; keep numeric-binary wins | + rep-offsets + lazy parsing + classifier |
| Aggressive | avg ≤ 0.26; ≥parity with XZ on table.csv/big.json class; best-in-corpus on 6+ files | + structural modeling (M6) |

### Compression speed
| Target | Definition |
|---|---|
| Conservative | ≤300 ms for 83 KB source files (≈25× faster than today); ≤5 s for big.json |
| Realistic | ≤80 ms / 83 KB (≈90× faster); ≤1.5 s big.json (classifier + caching + leaner gates) |
| Aggressive | ≤10 ms / 83 KB with C extension inner loops (~zstd -3 territory); sub-second big.json |

Decompression: conservative ≤2× current; realistic ≈ zlib-parity for small files, ≤100 ms for big.json via fast-table Huffman + bulk copies.
