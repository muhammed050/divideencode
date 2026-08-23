# PHASE 0 — Repository Audit Report

Date: 2026-08-22 · Working copy: `F:\ff\divideencode-2` · Python 3.12.10, Windows
Scope: read-only audit. No production code modified.

## Files inspected

`divideencode/{__init__,encoder,decoder,strategies,patterns,huffman,
divide_transform,dictionary,bitstream,errors}.py`, `benchmarks/{benchmark,gen_samples}.py`,
all four `tests/test_*.py`. (No README present; docs/ is empty.)

## Claim verification vs ARCHITECTURE_OPTIMIZATION.md

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `_rle_encode_core` never advances `lit_start`; literal prefix re-emitted per run | **CONFIRMED** | `patterns.py:32-58`; direct test: `b'xaaabbbc'` (8 B) → 20 B blob, `decode_node` rejects it (“rle produced wrong output size”); App.java root node → **172,268,242 B** blob |
| 2 | Bug masked because bloated RLE always loses size comparison | **CONFIRMED** | Full unittest suite passes (38/38); no test exercises RLE-wins path; bloated output ≥ n+1 whenever ≥2 non-adjacent runs |
| 3 | `encode_node` ≈ 217 calls on App.java | **CONFIRMED** | Instrumented run: 217 calls, depth histogram {3: 1, 2: 8, 1: 40, 0: 168} |
| 4 | RLE dominates wall time | **CONFIRMED** | Wrapper-timed: rle_encode 217 calls / 5.45 MB / **17.53 s** of 26.18 s total (67 %) |
| 5 | LZ / Huffman called at every node | **CONFIRMED** | 217 calls each, 5.45 MB scanned each; lz 2.10 s, huffman 1.94 s |
| 6 | DICT mined frequently | **CONFIRMED** | build_dictionary 49 calls (~3.8 s cum under cProfile); substitute 98 calls |
| 7 | DIVIDE transform cost moderate; estimate cheap | **CONFIRMED** | divide_transform 59 calls / 2.7 MB / 0.11 s; estimate_candidates 49 calls / 0.15 s |
| 8 | App.java ≈ 26 s → 5,526 B | **CONFIRMED** | Measured 26.7 s wall → 5,526 B container (inner node 5,516 = mode byte + 5,515 LZ payload) |
| 9 | Winning tree for App.java is a single LZ node | **CONFIRMED** | `decompress_with_trace` → trace = `LZ -> 100425 B` only |
| 10 | Fast-path DIVIDE only for d ∈ {256, 65536}; d=4096 slow path | **CONFIRMED** | `divide_transform.py:62`: requires `(1<<k)==d and k%8==0 and k>=8`; k(4096)=12 fails `k%8==0` |
| 11 | DICT escape/validation inconsistency | **CONFIRMED** | `dictionary.py:57-63`: validates substitution gain using escape chosen from 32 KiB sample, then re-chooses escape from FULL data before returning — validated gain does not bound final size (decode correctness unaffected) |
| 12 | No caching anywhere; Counter recomputed repeatedly | **CONFIRMED** | huffman.encode builds `Counter(data)` per call; `_choose_escape` builds two more Counters per DICT attempt |
| 13 | Existing gates: RLE ≥16 KiB sample-scan, LZ ≥48 KiB dual 32 KiB probes, HUFF entropy ≥8 KiB | **CONFIRMED** | `patterns.py:12-13,16-29,92-106`; `huffman.py:9,88-95` |

## Corrections / nuances found during audit

- dd.txt's older profile (RLE 2.1 s cumulative) is stale relative to this copy;
  current RLE cost is dominated by the lit_start bug (27 M `bytearray.extend`
  calls under cProfile). The gates were compensating for symptoms.
- `estimate_candidates`'s static model ignores downstream coding gain (its top
  root pick w=2/d=256 yields 7,822 B vs LZ's 5,515 B) — as stated in the doc.
- Decoder contract (`decompress`) enforces: magic/version, varint original length,
  CRC-32, zero trailing garbage. Any encoder-side change must emit exactly one
  complete `decode_node` stream — beam/pruning changes affect only *which*
  valid encoding is chosen.
- Test suite has NO RLE-specific unit test; `test_roundtrip.test_long_runs`
  passes only because RLE's bloated blob loses comparisons. P0 regression tests
  must close this gap.
- All 22 dd.txt-listed sample files exist (plus extras app.ts, lib.rs, notes.md
  which will be included in benchmarks).

## Audit conclusion

Every load-bearing claim of ARCHITECTURE_OPTIMIZATION.md is reproduced against
the real code. Proceeding to BASELINE benchmark, then P0.
