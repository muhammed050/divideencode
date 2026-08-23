# PERFORMANCE AUDIT — DivideEncode V1 (encoder hot paths, memory)

Measurement basis: cProfile of `compress()` on app.js (83 KB; 13.25 s profiled / 7.45 s wall), instrumented counters for counters_u32.bin and text_en.txt, plus tracemalloc peaks recorded in benchmarks/results/RESULTS.md. No code was modified.

## 1. What the benchmark actually times

`benchmarks/benchmark.py` times pure in-memory compress/decompress (file I/O excluded), including all search/model-selection work (inherent to DE). The tracemalloc probe runs *outside* the timed path (first 128 KiB only) — no contamination of timings; single-shot timing without repetitions is the main metrological weakness. Competitor settings are near-max (deflate-9 raw, gzip-9, brotli q11, zstd 19, xz 9e) — a "best effort vs best effort" comparison.

## 2. Profiled hot spots (app.js, one compression)

| share | function | note |
|---|---|---|
| ~34 % | `_lz_encode_core` (4.47 s cum, 338 calls) | hash-chain matcher |
| ~29 % | DICT machinery: `build_dictionary` 3.88 s + `substitute` 1.73 s + Counter.update 1.73 s | **never wins** on this file |
| ~21 % | `huffman.encode` 2.76 s (325 calls) | incl. 65 k-entry pairwise table rebuild per call |
| ~8 % | `_match_length` + dict `.get` (5.09 M calls) + list/bytearray appends (6.88 M) | inner-loop primitives |

Total bytes fed to LZ across the search: 5.80 MB ≈ 70× input size. Huffman: 65×. RLE core: 35×.

## 3. Top 20 bottlenecks (ranked)

| # | Location | Operation | Est. cost share | Why expensive | Future solution | Impact | Difficulty |
|---|---|---|---|---|---|---|---|
| 1 | strategies.encode_node | exhaustive re-evaluation of every codec at every tree node | dominant (multiplies everything) | no cache/memoization of subtree results | result cache keyed by (id(bytes), depth); skip codecs already beaten at parent | 2–5× speed, zero ratio loss | low |
| 2 | dictionary.build_dictionary/substitute | phrase mining + O(n·lens) substitution tried everywhere | ~29 % profiled on app.js | runs even when DICT cannot win | feature gate (alphabet diversity / match-rate test) before mining | big win on text/code | low |
| 3 | patterns._lz_encode_core | Python hash chains, 4-byte slice keys | ~34 % | per-position slicing/hashing/appends | C extension / restructure around bytes.find + positions array | 10–100× LZ speed | high |
| 4 | strategies DELTA recursion | unconditional recursive encode | moderate | always evaluated, usually loses | cheap entropy probe before recursing | moderate | low |
| 5 | huffman.encode | 65536-entry pairwise table rebuilt per call | part of 21 % | 256×256 loop × 325 calls | build lazily / reuse when alphabet unchanged | few % | low |
| 6 | huffman.decode | bit-at-a-time decode | decompression path | per-bit Python loop | canonical fast-table (8/16-bit lookup) | 5–10× decode | medium |
| 7 | lz_decode | byte-at-a-time copy via append(out[src+k]) | decompression path | no bulk copies | bytearray slice-copy runs | 2–5× decode | low |
| 8 | divide_transform slow path | per-word int.from_bytes/div/mod loops | hot on numeric files | Python int objects per word | array-based batch math or restrict to plane-split powers-of-two first | moderate | medium |
| 9 | estimate_candidates/_word_max | full scans per node ≥32 B | repeated scans | called at every internal node | compute once per unique range | small–moderate | low |
| 10 | encode_node serialization | candidates fully serialized before compare | wasted work | speculative evaluation | length-only scoring then serialize winner | up to ~2× on wide nodes | medium |
| 11 | patterns chain cap | del chain[:2048] churn | allocation pressure | list slicing copies | collections.deque or ring buffer | small | low |
| 12 | bytes()/bytearray() conversions at module boundaries | repeated whole-buffer copies | memory traffic | encoder.py, patterns, huffman each copy | pass memoryview internally | small–moderate | medium |
| 13 | rle gate regex finditer over sample | per-node regex | minor but repeated | runs at every node ≥16 KB | hoist to top-level once per buffer | small | low |
| 14 | _choose_escape twice | two Counter passes (sample + full data) | minor | duplicate work | single pass on decision | tiny | trivial |
| 15 | Counter(data) per huffman.encode | C-level but 325× | measurable | recount per call | reuse counts from parent | small | low |
| 16 | recursion overhead of encode_node itself | 20.5 M Python calls total (app.js) | interpreter tax | deep candidate trees | flatten search to iterative worklist | small alone, synergistic with #1 | medium |
| 17 | lz_encode gating probes | two extra full LZ encodes on 32 KB samples | up to 2× LZ cost for gated-out data | probes run full encoder | cheaper statistics-based gate | moderate on incompressible inputs | medium |
| 18 | pack_bits/unpack_bits | bit-by-bit Python loops | numeric-file decode/encode | pure-Python bit packing | int.from_bytes bulk tricks / lookup tables | moderate | medium |
| 19 | render_trace/trace building | dict-per-node during traced decode | dev tooling only | – | keep as is | none | – |
| 20 | single-threaded search | independent DIVIDE/DICT subtrees serialized | scaling ceiling | GIL-bound pure Python | multiprocessing per subtree (V2 concern) | n-core× potential | high |

## 4. Memory audit

tracemalloc peaks (128 KiB probe): 1.6 MB (notes.md) … 24 MB (photo.png/random_os/counters/table.csv) ⇒ **15–190× input**, driven by:
- LZ hash table: one Python list per distinct 4-gram + 4096-entry chains + 36-byte slice key objects — dominant term on binary files;
- Huffman pairwise table: 65536-slot pointer array (~0.5 MB) per encode call;
- duplicated buffers: `bytes(data)` coercion (#0 copy), per-codec output bytearrays converted again with `bytes()`, delta/divide intermediate streams;
- beam-state/tree storage: every candidate blob held simultaneously until best chosen (transient peak ≈ sum of all candidate sizes).

Decompression peaks are modest (output + one stream). Unnecessary copies identified: encoder entry coercion, per-mode `bytes(...)` wrappers in encode_node (`bytes((MODE_RAW,)) + bytes(data)` allocates a full copy just to measure it), double conversion inside codecs.

## 5. Computational model comparison

| | DE V1 | zlib | Brotli | Zstd | LZMA2 |
|---|---|---|---|---|---|
| passes over data | ~50–70 (LZ alone) | 1 | ~1 | 1 | ~1 (+match finder) |
| language kernel | Python | C | C++ | C | C |
| parsing | greedy, re-parsed dozens of times from scratch | lazy+hash chains | optimal-ish | lazy, rep-offsets | optimal-range, Markov |
| entropy stage on tokens | none | Huffman(lit+len+dist combined) | context-mixed literal+distance, static dict | FSE sequences + literals | range-coded adaptive contexts |

The 10³–10⁴× speed gap and the ratio gap are both structural consequences of this table.
