# ARCHITECTURE AUDIT — DivideEncode (read-only)

Date: 2026-08-22 · HEAD of `F:\ff\divideencode-2` · Python 3.12, Windows
Scope: full inventory + encoder/decoder pipeline trace. **No code modified.**

---

## 1. Repository inventory

### Source package `divideencode/` (11 modules, ~64 KB)

| File | Lines | Purpose | Performance-critical | Part of V1 | V2 impact |
|---|---|---|---|---|---|
| `__init__.py` | 19 | Public API re-exports (`compress`, `decompress`, `decompress_with_trace`, `parse_header`, `recursive_compress`, `render_trace`, errors) | no | yes | low |
| `encoder.py` | 43 | Container writer: magic `DE1`, version byte, varint orig-len, CRC-32; calls `strategies.encode_node`; `recursive_compress()` (DE-in-DE, ≤16 passes) | low | yes | medium (header evolves) |
| `decoder.py` | 48 | Header parser + `decompress`/`decompress_with_trace`; trailing-garbage check; CRC verify (optional flag) | no (I/O thin) | yes | medium |
| `strategies.py` | 382 | **Core**: mode registry RAW/DELTA/RLE/DICT/DIVIDE/HUFF/LZ; recursive best-of search `encode_node` with beam/exhaustive switch (`SEARCH_MODE`, `BEAM_K=3`, `LEAF_K=2`, `PRUNE_MARGIN=1.05`); symmetric `decode_node`; gates `MIN_LZ_LEN=64`, `MIN_DICT_LEN=1024`, `MIN_DIVIDE_LEN=32`, `DEFAULT_DEPTH=3` | **yes** | yes | **high** |
| `features.py` | 211 | Per-node statistics (P1): freq Counter, H0, printable/run/4-gram/delta-eq/high-byte-zero features on ≤2×32 KiB windows; `EncodeContext` per-compress caches keyed by `id(data)` (memo ≤1024 entries, cleared wholesale when full) | **yes** | yes | high |
| `scoring.py` | 235 | Heuristic size predictors (P2): raw/huff/rle formulas, LZ probe ratio, sampled delta, dict gate+probe, divide candidate re-rank; `_child_factor()` subtree estimator | **yes** | yes | high |
| `patterns.py` | 263 | RLE codec (regex `(.)\1{2,}`, control bytes <128 lit / ≥128 run) and LZ77 codec: greedy hash-chain matcher (`WINDOW=65536`, `MIN_MATCH=4`, `MAX_MATCH=259`, `MAX_CHAIN=32`, 4-byte-slice keys), flag-bit groups of 8, 3-byte match tokens; decoders with bounds checks | **yes** | yes | **high** (LZ replaced in V2) |
| `huffman.py` | 205 | Order-0 static canonical Huffman; heap build w/ tie counter; MAX_CODE_LEN=32 via freq-halving recursion; table = varint count + 2 B/symbol; pair-table encode fast path ≥32 KiB; bit-by-bit decode | **yes** | yes | high |
| `dictionary.py` | 117 | DICT transform: mine phrases len 12 then 8 (≤65536 positions each, count≥3), greedy non-overlap pick ≤255, rarest-byte escape, sample validation | warm | yes | medium |
| `divide_transform.py` | 198 | DIVIDE quotient/remainder split by word size w∈{1,2,4} × divisor d∈{2..65536}; plane-split fast path only for d ∈ {256, 65536} (k%8==0); general path per-word Python loop; `pack_bits`/`unpack_bits`; `estimate_candidates` static ranking | warm–hot | yes | medium |
| `bitstream.py` | 92 | LEB128 varint (≤56-bit), MSB-first BitWriter/BitReader | warm | yes | low–medium |
| `errors.py` | 10 | `DivideEncodeError` ← `CorruptedError` / `NotDivideEncodedError` | no | yes | keep |

### Tooling

| Path | Purpose | Notes |
|---|---|---|
| `cli.py` | subcommands compress/decompress/inspect/recursive/benchmark; `--depth --exhaustive --k` | catches only `DivideEncodeError` |
| `benchmarks/baseline.py` | instrumented DE-only run over 25 samples (monkey-patches strategy fns); incremental JSON | produced `results/baseline*.json|log` |
| `benchmarks/benchmark.py` | multi-algo comparison vs deflate/gzip/brotli/zstd/xz + tracemalloc peak (128 KiB prefix) + recursive pass count | competitor table exists only in `run_optimized.log` for 6 files |
| `benchmarks/gen_samples.py` | regenerates the 25 synthetic samples | deterministic seeds |
| `tests/` (6 files) | roundtrip, edge cases, random, corruption, features, RLE regression | **59 tests, all pass (~11 s)** |
| `samples/` | 25 files: source code ×8, structured text ×7, binary ×5, media/compressed ×5 | |
| `AUDIT_REPORT.md` | Phase-0 audit; documents the since-fixed RLE `lit_start` bug | historical |
| `docs/` | was empty; now contains this report set | |

### Dependency map

```
cli ──► encoder.compress ──► strategies.encode_node ──┬─► features.EncodeContext (caches)
     │                        │                       ├─► scoring.predict_all ─┐
     │                        │                       ├─► patterns.rle/lz      │
     │                        │                       ├─► huffman.encode       │
     │                        │                       ├─► divide_transform     │
     │                        │                       └─► dictionary.*         │
     │                        │              (recurses into itself for DELTA/DIVIDE/DICT children)
     └──► decoder.decompress ──► strategies.decode_node ─┬─► patterns.rle/lz decode
                                  (symmetric modes)      ├─► huffman.decode
                                                         ├─► divide_reconstruct/unpack_bits
                                                         └─► dictionary.expand   (+ bitstream.varint)
scoring ──► features(Features), patterns(lz_probe,_lz_encode_core),
            divide_transform(divide_transform), dictionary(build_dictionary/substitute)
            (function-local imports used to break import cycles)
```

Callers: `encoder.py` imports `encode_node` directly from `strategies` at module load — monkey-patching `strategies.encode_node` does not affect the root call (matters for instrumentation; `baseline.py` patches both).

---

## 2. Encoder pipeline audit (CLI → final bytes)

```
INPUT bytes
→ header: "DE1" + ver(1) + varint(len) + crc32(data)            [O(n), C-speed]
→ encode_node(data, depth=3, ctx):
    ① features(data)          — cached per id(data): Counter O(n), entropy O(k),
                                windowed printable/run/rep4/delta-eq/hi-zero stats
    ② estimate_candidates     — static top-3 DIVIDE (w,d) pairs by size formula
    ③ scoring.predict_all     — heuristic FINAL-size estimates incl. cached
                                lz_probe (16 KiB window through full LZ core)
    ④ consider(RAW)           — exact upper bound (n+1)
    ⑤ leaf encoders           — beam: rank rle/huff/lz by prediction,
                                skip if pred > best×1.05, run ≤ LEAF_K=2
                                exhaustive: run all three always
    ⑥ transforms (if depth>0) — DELTA child recursion;
                                DIVIDE top-BEAM_K=3 pairs (q-node computed before
                                safe head_len cut; r-node after);
                                DICT (mine → substitute → inner recursion)
    ⑦ memoize blob by (id(data), depth); return argmin
→ container = header + winning node stream
```

Per-stage cost / repeated work:

| Stage | Complexity | Repeated work? | Bottleneck risk |
|---|---|---|---|
| header/CRC | O(n) C | none | none |
| features | O(n) Counter + O(win) regex/slicing; Python loop only in delta_eq & rep4 probes | eliminated by cache | moderate (full-file Counter per node) |
| predict_lz | full LZ core over 16 KiB per distinct node | cached per id | **high** |
| predict_delta | Python loop over 16 KiB per node (duplicated `_delta_encode` logic) | cached? **no** — recomputed per node unless same object | **high** |
| predict_divide | up to 6× `divide_transform` on 32 KiB sample per node + `_child_factor` | not cross-node cached | **high** on transform-rich trees |
| predict_dict | phrase mining on 32 KiB + substitute probe (cached per id) | mining redone later in `expand_dict` on full data | high when gated |
| leaves | rle regex O(n); huff heap+packing O(n); lz greedy O(n·chain) | gated/ranked (beam) or unconditional (exhaustive) | **highest** overall |
| transforms | delta pure-Python byte loop O(n); divide per-word loop O(n/w); substitute O(n·lens) | memo prevents identical-object recompute only | high |
| memo | O(1) lookup | wholesale clear at 1024 entries can thrash | low |

Places where the same input bytes are processed multiple times (current beam HEAD):

1. `predict_lz` LZ-encodes a 16 KiB window, then `lz` leaf may LZ-encode the whole buffer again.
2. `predict_dict` mines/substitutes a 32 KiB prefix; `expand_dict` re-mines and substitutes the *full* buffer (no result sharing).
3. `predict_delta` computes a sampled delta; `expand_delta` computes the full delta again.
4. `predict_divide_candidates` runs `divide_transform` on a 32 KiB sample ×6; `expand_divide` runs it again on the full buffer.
5. `ctx.freq_table` shared between huff leaf and dict escape (good); but `huffman.encode` recomputes entropy from that Counter every call.
6. `recursive_compress` reruns the entire search per pass.

Exhaustive mode multiplies all of this by ~every node ×3 leaves + all transforms; measured 173 nodes / 22.1 MB bytes-seen for big.json (52× input) vs 4 nodes / 891 KB (2.1×) in beam.

## 3. Decoder pipeline audit

```
container parse: magic/version/varint/crc  → decode_node(blob,pos,end,expected_len):
  RAW    : slice expected_len bytes (bounds-checked)
  DELTA  : recurse(same len) → _delta_decode (Python byte loop)
  RLE    : control-byte walk → strict size check
  DICT   : escape, varint count ≤1020, entry lens ≤ remaining, varint sub_len ≤2^31,
           recurse(sub_len) → expand (id range-checked, strict size check)
  DIVIDE : wcode/did/wq validated; tail sliced; q-child(n_words*wq),
           r-child((n_words*rbits+7)//8) recursed → divide_reconstruct
           (stream sizes, remainder range, word overflow all validated)
  HUFF   : table parse (count 1..256, len 1..32), canonical completeness check
           (Σ2^(maxlen−len)==2^maxlen), bit-by-bit decode to exactly expected_len
  LZ     : flag groups; offsets validated 1..len(out); overrun rejected
then: pos must equal len(blob); crc32(output) == stored crc (unless verify_crc=False)
```

Symmetry: every encoder mode has a matching decoder; verified by reading both paths plus the 59-test suite. Recursion depth is bounded **only** by input length for attacker-crafted streams (see CORRECTNESS_AUDIT: deep nesting raises bare `RecursionError`, not `CorruptedError`). Integer sizes: Python bignums (no overflow); varints capped at 56 bits; lengths validated against `end` at every leaf. CRC checked correctly (CRC-32 of reconstructed output vs header field).
