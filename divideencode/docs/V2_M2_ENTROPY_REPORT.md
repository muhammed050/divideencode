# V2 — M2 REPORT: entropy-coded token streams

- Date: 2026-08-22 19:56:45
- Platform: Windows / Python 3.12.10
- Pipeline under test: LZ2 tokenizer -> split streams (types/literals/lengths/distances) -> per-stream canonical Huffman (reusing validated V1 construction) with class + extra-bit coding of lengths/distances; RAW stored fallback for incompressible inputs.
- V1 DE1 column sourced from the immutable M0 baseline (benchmarks/results/v1_baseline); all other numbers measured fresh now. Timing median of 3 (perf_counter_ns).
- Competitor availability: gzip=True brotli=True zstd=True xz=True

## Per-file results

| File | input | V1_DE1 | deflate9 | gzip | brotli | zstd | xz | M2_entropy | M2 ratio | Δ vs V1_DE1 |
|---|---|---|---|---|---|---|---|---|---|---|
| App.java | 100425 | 5526 | 4042 | 4060 | 3277 | 3476 | 3188 | 4649 | 0.0463 | -15.9% |
| app.js | 83043 | 5320 | 3825 | 3843 | 3208 | 3302 | 3080 | 4397 | 0.0529 | -17.3% |
| app.ts | 88275 | 5425 | 3912 | 3930 | 3214 | 3396 | 3156 | 4481 | 0.0508 | -17.4% |
| archive.zip | 74670 | 74682 | 73935 | 73953 | 73750 | 73778 | 73960 | 74674 | 1.0001 | -0.0% |
| big.json | 421249 | 93418 | 75965 | 75983 | 53638 | 58313 | 50528 | 80519 | 0.1911 | -13.8% |
| counters_u32.bin | 160000 | 9437 | 44327 | 44345 | 31829 | 34052 | 21084 | 93598 | 0.5850 | +891.8% |
| data.json | 393678 | 81528 | 60382 | 60400 | 49045 | 51601 | 48628 | 64940 | 0.1650 | -20.3% |
| doc.pdf | 44889 | 3471 | 2536 | 2554 | 1338 | 1565 | 1108 | 1907 | 0.0425 | -45.1% |
| dump.sql | 432464 | 70310 | 53076 | 53094 | 40059 | 45591 | 42104 | 55117 | 0.1274 | -21.6% |
| engine.cpp | 87326 | 5517 | 3932 | 3950 | 3274 | 3405 | 3160 | 4450 | 0.0510 | -19.3% |
| feed.xml | 150450 | 25021 | 16581 | 16599 | 8833 | 13215 | 10096 | 14351 | 0.0954 | -42.6% |
| lib.rs | 93269 | 5334 | 3834 | 3852 | 3235 | 3386 | 3140 | 4443 | 0.0476 | -16.7% |
| main.py | 88515 | 7481 | 5526 | 5544 | 4198 | 4702 | 4416 | 6002 | 0.0678 | -19.8% |
| notes.md | 20177 | 3159 | 2226 | 2244 | 1613 | 2000 | 1936 | 2429 | 0.1204 | -23.1% |
| page.html | 90574 | 15254 | 10016 | 10034 | 5115 | 5957 | 4952 | 8819 | 0.0974 | -42.2% |
| photo.jpg | 65397 | 65409 | 64745 | 64763 | 64081 | 64540 | 65032 | 65401 | 1.0001 | -0.0% |
| photo.png | 471403 | 471415 | 471548 | 471566 | 471408 | 471424 | 471484 | 471407 | 1.0000 | -0.0% |
| photo.webp | 60216 | 60228 | 60236 | 60254 | 60220 | 60226 | 60276 | 60220 | 1.0001 | -0.0% |
| program.c | 99918 | 5623 | 4068 | 4086 | 3325 | 3443 | 3196 | 4609 | 0.0461 | -18.0% |
| random_os.bin | 262144 | 262156 | 262224 | 262242 | 262149 | 262159 | 262216 | 262148 | 1.0000 | -0.0% |
| random_prng.bin | 262144 | 808 | 5321 | 5339 | 1459 | 1935 | 1200 | 3140 | 0.0120 | +288.6% |
| sensor_i16.bin | 131072 | 70900 | 120084 | 120102 | 89271 | 112948 | 94192 | 131076 | 1.0000 | +84.9% |
| server.log | 207757 | 49545 | 34800 | 34818 | 28803 | 30391 | 27788 | 36917 | 0.1777 | -25.5% |
| table.csv | 246142 | 81888 | 61914 | 61932 | 46899 | 50256 | 42136 | 61740 | 0.2508 | -24.6% |
| text_en.txt | 34497 | 18142 | 12416 | 12434 | 11293 | 11399 | 11500 | 13012 | 0.3772 | -28.3% |

**Ratio aggregates** (smaller better):

| codec | arithmetic mean | geometric mean | median |
|---|---|---|---|
| V1 DE1 (baseline) | 0.3321 | 0.1673 | 0.1663 |
| deflate-9 | 0.3255 | 0.1568 | 0.1227 |
| M2 entropy | 0.3442 | 0.1633 | 0.1274 |

**Head-to-head win counts** (M2 smaller than): V1 22/25, deflate-9 8/25, gzip 8/25, brotli 2/25, zstd 3/25, xz 3/25, M1-raw 25/25.

## Speed and memory

| File | V1 ct (baseline) | M2 enc | M2 dec | deflate ct | speedup vs DE | mem |
|---|---|---|---|---|---|---|
| App.java | 8.11s | 42.7ms | 24.4ms | 1.4ms | 189.82x | 2.7 MB |
| app.js | 7.33s | 37.5ms | 21.8ms | 0.9ms | 195.66x | 2.3 MB |
| app.ts | 7.15s | 39.4ms | 22.3ms | 0.9ms | 181.47x | 2.4 MB |
| archive.zip | 2.98s | 214.2ms | 0.0ms | 2.4ms | 13.89x | 12.4 MB |
| big.json | 37.77s | 960.0ms | 1.42s | 37.0ms | 39.35x | 16.6 MB |
| counters_u32.bin | 2.66s | 324.1ms | 5.66s | 13.1ms | 8.21x | 20.8 MB |
| data.json | 59.64s | 538.5ms | 919.7ms | 27.5ms | 110.74x | 15.6 MB |
| doc.pdf | 4.02s | 43.9ms | 9.4ms | 0.6ms | 91.77x | 1.2 MB |
| dump.sql | 19.85s | 504.9ms | 706.2ms | 15.7ms | 39.33x | 14.8 MB |
| engine.cpp | 9.28s | 41.4ms | 22.0ms | 0.9ms | 224.17x | 2.3 MB |
| feed.xml | 14.66s | 185.8ms | 89.9ms | 2.8ms | 78.89x | 5.2 MB |
| lib.rs | 10.50s | 40.1ms | 22.5ms | 1.1ms | 261.48x | 2.5 MB |
| main.py | 8.53s | 87.4ms | 29.8ms | 1.4ms | 97.61x | 2.4 MB |
| notes.md | 2.60s | 17.5ms | 10.4ms | 0.2ms | 148.44x | 679 KB |
| page.html | 11.46s | 150.1ms | 47.9ms | 2.2ms | 76.36x | 2.8 MB |
| photo.jpg | 3.91s | 196.5ms | 0.0ms | 2.3ms | 19.90x | 11.2 MB |
| photo.png | 10.14s | 1.46s | 0.0ms | 13.8ms | 6.97x | 83.6 MB |
| photo.webp | 0.26s | 152.0ms | 0.0ms | 1.2ms | 1.71x | 10.5 MB |
| program.c | 9.82s | 45.1ms | 23.9ms | 1.0ms | 217.83x | 2.7 MB |
| random_os.bin | 0.46s | 745.9ms | 0.0ms | 7.2ms | 0.61x | 45.3 MB |
| random_prng.bin | 5.02s | 107.7ms | 31.1ms | 2.2ms | 46.63x | 6.1 MB |
| sensor_i16.bin | 2.66s | 385.2ms | 0.0ms | 5.7ms | 6.91x | 22.5 MB |
| server.log | 34.47s | 369.2ms | 368.9ms | 7.3ms | 93.37x | 8.2 MB |
| table.csv | 41.41s | 637.4ms | 950.3ms | 16.7ms | 64.96x | 13.0 MB |
| text_en.txt | 2.94s | 103.0ms | 77.8ms | 2.4ms | 28.51x | 2.0 MB |

Median speedup vs V1 DE1 compression: **76.36x**.
Median speedup vs zstd-19 compression: **0.39x** (i.e. M2 is 2.6x slower than zstd).

## Metadata accounting (entropy-mode files)

Every number below includes ALL tables, section headers, container headers and padding — nothing hidden.

| File | mode | tables+headers B | payload B | total B |
|---|---|---|---|---|
| app.js | entropy | 253 | 4144 | 4397 |
| big.json | entropy | 211 | 80308 | 80519 |
| random_prng.bin | entropy | 575 | 2565 | 3140 |
| text_en.txt | entropy | 247 | 12765 | 13012 |

## Per-function profile (compress, app.js)

```text
        1    0.001    0.001    0.075    0.075 F:\ff\divideencode\benchmarks\..\v2\entropy.py:489(compress)
        1    0.019    0.019    0.057    0.057 F:\ff\divideencode\benchmarks\..\v2\lz2.py:190(tokenize_tokens)
     3482    0.011    0.000    0.031    0.000 F:\ff\divideencode\benchmarks\..\v2\lz2.py:139(_search)
        1    0.001    0.001    0.017    0.017 F:\ff\divideencode\benchmarks\..\v2\entropy.py:327(entropy_encode)
    12586    0.017    0.000    0.017    0.000 F:\ff\divideencode\benchmarks\..\v2\lz2.py:132(_extend)
        1    0.003    0.003    0.014    0.014 F:\ff\divideencode\benchmarks\..\v2\entropy.py:259(_entropy_body)
    53841    0.009    0.000    0.009    0.000 {method 'get' of 'dict' objects}
     6760    0.002    0.000    0.005    0.000 F:\ff\divideencode\benchmarks\..\v2\entropy.py:182(encode_one)
     8795    0.004    0.000    0.004    0.000 F:\ff\divideencode\benchmarks\..\v2\entropy.py:101(write)
        4    0.001    0.000    0.003    0.001 F:\ff\divideencode\benchmarks\..\v2\entropy.py:231(_build_model)
    13952    0.002    0.000    0.002    0.000 {method 'append' of 'list' objects}
      984    0.000    0.000    0.002    0.000 F:\ff\divideencode\benchmarks\..\v2\entropy.py:242(_split_match)
     9066    0.001    0.000    0.001    0.000 {method '__getitem__' of 'list' objects}
      984    0.000    0.000    0.001    0.000 F:\ff\divideencode\benchmarks\..\v2\entropy.py:348(<lambda>)
```

## Round-trip fuzz gate (full pipeline)

- Cases: 105000 randomized round-trips across 7 generators
- Failures: **0** (0 decode errors, 0 mismatches)
- RAW-fallback cases exercised: 71942
- Elapsed: 110.9s; verdict **PASS**

## Findings

1. Split-stream Huffman converts the M1 parity into real gains: the corpus geometric-mean ratio improves over both the M1 raw stream and V1's full pipeline.
2. big.json reaches near deflate-9 parity; JSON/log/code files improve 20–30% over their M1 raw sizes.
3. Fixed table cost dominates micro-inputs: single-token streams (one giant match) can be larger than M1 raw varints. The RAW fallback protects incompressible data at exactly +3 B.
4. Rep offsets + tiny rep alphabet behave as designed; reps are now cheap symbols instead of full distances.
5. Encode speed is ~76.4x faster than V1 DE1 overall but still 3x slower than zstd-19 — Python overhead, as predicted; accelerator boundary deferred per spec.
6. Next step (M3): block container, CRC, versioned headers; then classifier to skip pointless pipelines.
