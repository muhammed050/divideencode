# ARCHITECTURE AUDIT — DivideEncode V1 (read-only audit, 2026-08-22)

## 1. Repository inventory

| Path | Purpose | LOC | Perf-critical | V1 | V2 impact |
|---|---|---|---|---|---|
| `divideencode/__init__.py` | Public API re-exports (`compress`, `decompress`, traces) | 19 | no | yes | low |
| `divideencode/encoder.py` | Container assembly: magic/version/varint/CRC + `encode_node`; `recursive_compress` | 42 | no (thin) | yes | medium (DE2 header) |
| `divideencode/decoder.py` | Header parse, node dispatch, CRC verify, trace decode | 48 | warm path | yes | medium |
| `divideencode/strategies.py` | **Core search**: `encode_node` exhaustive strategy-tree search; mode constants; `decode_node` dispatch; `render_trace` | 238 | **hot** | yes | **high** |
| `divideencode/patterns.py` | RLE codec + LZ codec (match finder, flag-byte token format) | 247 | **hottest** | yes | **high** |
| `divideencode/huffman.py` | Order-0 static canonical Huffman (build/serialize/inline bit-pack/decode) | 204 | hot | yes | medium (replaced by range coder in V2) |
| `divideencode/divide_transform.py` | DIVIDE transform (quotient/remainder split), bit pack/unpack, candidate estimator | 198 | hot | yes | medium |
| `divideencode/dictionary.py` | DICT transform: phrase mining, escape choice, substitution/expansion | 116 | hot (encoder only) | yes | medium |
| `divideencode/bitstream.py` | varint, BitWriter (unused), BitReader (only `read_bit` used) | 92 | low | partial | low |
| `divideencode/errors.py` | Exception hierarchy | 10 | no | yes | keep |
| `divideencode/cli.py` | compress/decompress/inspect/recursive/benchmark subcommands | 178 | no | yes | low |
| `tests/test_roundtrip.py` | Text/structured/binary round-trips + SHA checks | 92 | – | – | extend |
| `tests/test_edge_cases.py` | Empty/1-byte/all-zero/patterns/large/depth/bytearray | 81 | – | – | extend |
| `tests/test_random.py` | Crypto-random, PRNG, biased alphabets, inflation bound ≤64 B | 65 | – | – | keep |
| `tests/test_corruption.py` | Header flips, payload flips, truncation, extension, garbage magic/version | 86 | – | – | extend |
| `benchmarks/benchmark.py` | DE vs ZIP/GZIP/Brotli/Zstd/XZ runner, CSV+MD output, tracemalloc probe | 285 | – | – | medium |
| `benchmarks/gen_samples.py` | Deterministic corpus generator (28 files) | 484 | – | – | low |
| `benchmarks/results/*` | RESULTS.md, benchmark_results.csv, run_optimized.log | – | – | – | regenerate per release |
| `samples/` | 28 generated corpus files (~3.3 MB total) | – | – | – | keep |
| `docs/` | empty (this audit) | – | – | – | – |

Dead code found: `bitstream.BitWriter` (entire class), `BitReader.read_bits`, `BitReader.align_tail_bits`, `huffman.parse_table` (decoder re-implements table parsing inline), `encoder.CompressResult`.

## 2. Dependency / call graph

```
cli.py ──► encoder.compress ─┬─► bitstream.encode_varint
                             ├─► zlib.crc32
                             └─► strategies.encode_node ──┬─► patterns.rle_encode/_core
                                                          ├─► huffman.encode
                                                          ├─► patterns.lz_encode/_core
                                                          ├─► strategies._delta_encode   (recursive)
                                                          ├─► divide_transform.estimate_candidates
                                                          │        └► _word_max
                                                          ├─► divide_transform.divide_transform
                                                          │        └► pack_bits / _plane_split
                                                          └─► dictionary.build_dictionary/substitute
                                                                   └► Counter

decompress ─► decoder.parse_header ─► strategies.decode_node ─┬─► patterns.rle_decode/lz_decode
                                                              ├─► huffman.decode ─► bitstream.BitReader
                                                              ├─► strategies._delta_decode       (recursive)
                                                              ├─► divide_transform.divide_reconstruct
                                                              │        └► unpack_bits / _plane_join
                                                              └─► dictionary.expand
```

No circular imports. Package has zero third-party runtime dependencies (stdlib only: zlib, heapq, math, collections, array, re).

## 3. Encoder pipeline (CLI → bytes)

1. **Input read + type coercion** (`cli.cmd_compress`, `encoder.compress`): file → `bytes(data)` copy #1.
2. **Header build**: `b"DE1"` + version byte + varint(orig_len) + crc32 little-endian (4 B).
3. **Strategy-tree search** (`strategies.encode_node(data, depth=3)`) — see §4.
4. **Output assembly**: header + winning tree serialization. No padding, no alignment.

Repeated-work sites: every `encode_node` invocation re-runs all leaf codecs on its byte range; DELTA/DIVIDE/DICT children are fresh encodes with zero result sharing. Measured on app.js (83 KB): 325 `encode_node` calls; `_lz_encode_core` executed 338× over a cumulative 5.80 MB of input (≈70 full passes); `huffman.encode` 325× over 5.37 MB (≈65 passes); RLE core 240× over 2.88 MB.

## 4. encode_node decision procedure (per node)

For byte range `data`, depth d:

1. Candidate `RAW`: mode byte + raw copy — always present (guarantees termination and bounds worst case at n+1 bytes/node).
2. If non-empty:
   - `RLE` leaf (gated ≥16 KB by 64 KB-sample run-density heuristic);
   - `HUFFMAN` leaf (entropy-gated ≥8 KB);
   - `LZ` leaf if n≥64 (gated ≥48 KB by two 32 KB sample probes at <96 % size);
   - if d>0:
     - `DELTA` → recurse(d-1) — unconditional recursion for any size;
     - `DIVIDE` if n≥32: `estimate_candidates` returns top-3 (w,d) pairs by size estimate; for each: transform → recurse on quotient stream and remainder stream (two subtrees, each d-1);
     - `DICT` if n≥1024: mine phrases, viability-probe on first 32 KiB, then substitute → recurse(d-1).
3. Keep smallest blob (ties → first evaluated wins: RAW beats equal-size others).

Branching factor ≈ up to 12 subtrees/node; empirical total nodes: app.js 325, counters_u32.bin 77, text_en.txt 169. Depth bound 3 keeps the tree finite; size gates keep it tractable but do not eliminate duplicate work between sibling branches.

## 5. Decoder pipeline

`parse_header` → magic/version check → varint orig_len → CRC32 → `decode_node(buf,pos,end,orig_len)` recursive dispatch mirroring encoder modes exactly (all 7 modes have matching decoders). After root: trailing-garbage check (`pos != len(blob)`), then CRC32 verification over reconstructed data.

Symmetry verified conceptually: every encoder-emitted construct is parsed; expected_len threading makes every node self-delimiting in output size. Malformed-input handling exists at every stage except unbounded recursion depth (see CORRECTNESS_AUDIT).

## 6. Format / container (DE1)

```
offset  size  field
0       3     magic "DE1"
3       1     version (=1)
4       v     varint original length (≤2^56, shift>56 rejected)
4+v     4     CRC32 of original data, little-endian
9..     ...   strategy-tree nodes
```

Node encodings:
- RAW(0x00): [mode][expected_len raw bytes]
- DELTA(0x01): [mode][child node]
- RLE(0x02): control-byte stream (128..255 = run of c−125 copies of next byte; else literal count c+1)
- DICT(0x03): [mode][escape byte][varint count][count × (varint len + entry)][varint sub_len][child]
- DIVIDE(0x04): [mode][wcode][divisor id][wq][tail bytes][q child][r child]
- HUFF(0x05): [mode][varint pairs][(sym,len)×pairs][MSB-first canonical bitstream]
- LZ(0x06): [mode][flag byte + 8 tokens]× (literal=1 B; match=off LE16 + len−4 byte)

Properties: overhead 9–13 B header + 1–4 B/node; max theoretical orig_len 2^56−1; corruption detected structurally + CRC32 (accidental damage only, not adversarial); forward compatibility none beyond version rejection (unknown mode ⇒ error, no skippable-frame design); deterministic encoder (stable sorts, insertion-counter tie-breaks); decoder safety good except recursion depth (unbounded via nested DELTA nodes — confirmed RecursionError escapes the exception contract at ~5000 nested nodes).
