# BENCHMARK AUDIT — DivideEncode V1 vs competitors

Source: benchmarks/results/RESULTS.md + benchmark_results.csv (run 2026-08-22, Windows / Python 3.12.10 / AMD64; DE depth=3; ZIP=raw deflate lvl9; GZIP lvl9; Brotli q11; Zstd 19; LZMA2 xz 9e). Note: dd.txt quotes older timings (App.java ≈24.8 s, big.json ≈43.7 s); the current results file supersedes them (8.1 s / 37.8 s) — the code has already been partially optimized since those figures were written.

## 1. Average ratios (25 files ≥1 KB)

| Algo | avg ratio |
|---|---|
| DE | **0.3321** |
| ZIP(deflate-9) | 0.3255 |
| GZIP-9 | 0.3257 |
| Zstd-19 | 0.3071 |
| Brotli-q11 | **0.2936** |
| LZMA2(xz 9e) | **0.2928** |

DE is on average *behind even deflate*, and ~12–14 % behind Brotli/XZ on mean ratio.

## 2. Per-file size gap (positive = DE larger/worse)

| File | orig | DE | vs ZIP | vs Brotli | vs XZ | DE time |
|---|---|---|---|---|---|---|
| App.java | 100425 | 5526 | +36.7 % | +68.6 % | +73.3 % | 8.11 s |
| app.js | 83043 | 5320 | +39.1 % | +65.8 % | +72.7 % | 7.33 s |
| app.ts | 88275 | 5425 | +38.7 % | +68.8 % | +71.9 % | 7.15 s |
| archive.zip | 74670 | 74682 | +1.0 % | +1.3 % | +1.0 % | 2.98 s |
| big.json | 421249 | 93418 | +23.0 % | +74.2 % | +84.9 % | 37.77 s |
| counters_u32.bin | 160000 | 9437 | **−78.7 %** | −70.4 % | −55.2 % | 2.66 s |
| data.json | 393678 | 81528 | +35.0 % | +66.2 % | +67.7 % | 59.64 s |
| doc.pdf | 44889 | 3471 | +36.9 % | +159.4 % | +213.3 % | 4.02 s |
| dump.sql | 432464 | 70310 | +32.5 % | +75.5 % | +67.0 % | 19.85 s |
| engine.cpp | 87326 | 5517 | +40.3 % | +68.5 % | +74.6 % | 9.28 s |
| feed.xml | 150450 | 25021 | +50.9 % | +183.3 % | +147.8 % | 14.66 s |
| lib.rs | 93269 | 5334 | +39.1 % | +64.9 % | +69.9 % | 10.50 s |
| main.py | 88515 | 7481 | +35.4 % | +78.2 % | +69.4 % | 8.53 s |
| notes.md | 20177 | 3159 | +41.9 % | +95.8 % | +63.2 % | 2.60 s |
| page.html | 90574 | 15254 | +52.3 % | +198.2 % | +208.0 % | 11.46 s |
| photo.jpg/png/webp | – | ~1.000 | ±0–1 % | ±0–1 % | ±0–1 % | 0.26–10.1 s |
| program.c | 99918 | 5623 | +38.2 % | +69.1 % | +75.9 % | 9.82 s |
| random_os.bin | 262144 | 262156 | −0.0 % | +0.0 % | −0.0 % | 0.46 s |
| random_prng.bin | 262144 | 808 | **−84.8 %** | −44.6 % | −32.7 % | 5.02 s |
| sensor_i16.bin | 131072 | 70900 | **−41.0 %** | −20.6 % | −24.7 % | 2.66 s |
| server.log | 207757 | 49545 | +42.4 % | +72.0 % | +78.3 % | 34.47 s |
| table.csv | 246142 | 81888 | +32.3 % | +74.6 % | +94.3 % | 41.41 s |
| text_en.txt | 34497 | 18142 | +46.1 % | +60.6 % | +57.8 % | 2.94 s |

## 3. Speed gaps

Median compression slowdown: **2298× vs deflate-9**, **175× vs xz-9e** (per-file: 62×–13000×). Decompression: median 8.7 ms — small files fine; big files lag (big.json 265 ms vs zlib 1 ms).

### Best DE cases
counters_u32.bin, random_prng.bin, sensor_i16.bin — numeric/binary with exploitable word structure (DIVIDE/DELTA chains) or weak PRNG (DICT+LZ). DE is strictly best-in-corpus on these.

### Worst DE cases
page.html (+208 % vs XZ), doc.pdf (+213 % vs XZ), feed.xml (+183 % vs Brotli) — highly repetitive markup where entropy-coded LZ + static dictionaries shine and raw-token LZ cannot compete.

### Best speed cases
random_os.bin (0.46 s — gates short-circuit everything), photo.webp (0.26 s), archive.zip-class incompressibles (~3 s dominated by gate probes).

### Worst speed cases
data.json 59.6 s, big.json 37.8 s, table.csv 41.4 s, server.log 34.5 s — large + structured ⇒ many tree nodes × all-codec evaluation.

### Structured-data failures
All JSON/XML/HTML/CSV/SQL/source files: DE loses to every competitor by 23–208 %. Root causes per LZ_AUDIT/ENTROPY_AUDIT: no entropy coding of LZ output, no rep-offsets, no context modeling.

### Already-compressed-data behavior
Correct: RAW fallback keeps overhead ≤12 bytes (photo.png 471415 vs 471403 = header only); never inflates beyond header+mode byte. Compression time still wasted on gates/probes (up to 10.1 s for photo.png) but bounded.

## 4. Benchmark methodology notes
- Timing excludes I/O ✓; includes full search (inherent) ✓.
- tracemalloc probe outside timed path ✓ (documented in RESULTS.md).
- Single-shot timing, no warm-up repetitions → noise on sub-second entries.
- CSV omits competitor time columns (only RESULTS.md prose table has them).
- recursive_compress extra pass reported separately (DE_recursive_best) — good practice, keep.
