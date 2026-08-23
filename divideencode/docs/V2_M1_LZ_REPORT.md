# V2 — M1 REPORT: LZ2 tokenizer vs V1 LZ

- Date: 2026-08-22 18:20:51
- Platform: Windows / Python 3.12.10
- Scope: isolated experiment. V1 untouched (frozen copy in `divideencode_v1/`). No entropy coding yet — this compares *raw token representations* only.
- V1 measured via `_lz_encode_core` (its 48 KB quality gate bypassed on purpose so weak-LZ files still produce a blob).
- V2 default config: window=65536, max_chain=32, lazy=1, nice_length=96.
- Timing: perf_counter_ns, 1 warmup + 3 timed runs, median. Times cover pure tokenization/serialization; file I/O excluded.

## Per-file results

| File | input | V1_LZ | V2_raw | Δsize | ratio V1 | ratio V2 | tokens | lits | match | rep | avg_len | avg_dist | V1 enc | V2 enc | V1 dec | V2 dec | mem |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| App.java | 100425 | 5514 | 6429 | +16.6% | 0.0549 | 0.0640 | 3003 | 1914 | 1012 | 77 | 93.1 | 16351 | 22.5ms | 40.3ms | 8.3ms | 8.6ms | 2.6 MB |
| app.js | 83043 | 5308 | 5921 | +11.5% | 0.0639 | 0.0713 | 2888 | 1837 | 984 | 67 | 79.1 | 14175 | 18.4ms | 34.4ms | 7.2ms | 7.5ms | 2.3 MB |
| app.ts | 88275 | 5413 | 6040 | +11.6% | 0.0613 | 0.0684 | 2932 | 1858 | 1006 | 68 | 81.2 | 14971 | 19.4ms | 36.4ms | 7.7ms | 8.0ms | 2.3 MB |
| archive.zip | 74670 | 83500 | 83403 | -0.1% | 1.1183 | 1.1170 | 73886 | 73698 | 172 | 16 | 5.2 | 3345 | 41.1ms | 147.8ms | 15.0ms | 15.0ms | 8.1 MB |
| big.json | 421249 | 115741 | 119669 | +3.4% | 0.2748 | 0.2841 | 50765 | 25573 | 24527 | 665 | 15.7 | 18850 | 311.9ms | 896.2ms | 54.1ms | 64.6ms | 13.0 MB |
| counters_u32.bin | 160000 | 126574 | 126576 | +0.0% | 0.7911 | 0.7911 | 110140 | 108807 | 1 | 1332 | 38.4 | 36 | 75.2ms | 227.3ms | 26.8ms | 26.9ms | 15.1 MB |
| data.json | 393678 | 86075 | 91103 | +5.8% | 0.2186 | 0.2314 | 43845 | 27177 | 16087 | 581 | 22.0 | 21354 | 169.1ms | 494.7ms | 44.7ms | 53.1ms | 12.7 MB |
| doc.pdf | 44889 | 3697 | 3606 | -2.5% | 0.0824 | 0.0803 | 1711 | 873 | 155 | 683 | 53.3 | 9010 | 11.3ms | 42.8ms | 4.1ms | 4.2ms | 1.2 MB |
| dump.sql | 432464 | 75003 | 80077 | +6.8% | 0.1734 | 0.1852 | 33553 | 17279 | 16115 | 159 | 25.5 | 23819 | 175.2ms | 479.0ms | 46.7ms | 54.5ms | 12.5 MB |
| engine.cpp | 87326 | 5505 | 6300 | +14.4% | 0.0630 | 0.0721 | 2962 | 1888 | 995 | 79 | 80.5 | 14212 | 21.2ms | 38.4ms | 7.7ms | 8.1ms | 2.2 MB |
| feed.xml | 150450 | 25009 | 25376 | +1.5% | 0.1662 | 0.1687 | 12494 | 7800 | 2645 | 2049 | 27.3 | 21456 | 55.6ms | 172.0ms | 16.0ms | 17.0ms | 4.5 MB |
| lib.rs | 93269 | 5322 | 6258 | +17.6% | 0.0571 | 0.0671 | 2964 | 1905 | 997 | 62 | 86.7 | 14167 | 20.0ms | 36.3ms | 7.9ms | 8.2ms | 2.4 MB |
| main.py | 88515 | 7469 | 7774 | +4.1% | 0.0844 | 0.0878 | 3662 | 2085 | 1505 | 72 | 55.2 | 14615 | 23.9ms | 82.4ms | 7.9ms | 8.4ms | 2.4 MB |
| notes.md | 20177 | 3147 | 3045 | -3.2% | 0.1560 | 0.1509 | 1816 | 1310 | 409 | 97 | 38.1 | 3698 | 5.5ms | 14.8ms | 2.0ms | 2.2ms | 658 KB |
| page.html | 90574 | 15242 | 14869 | -2.4% | 0.1683 | 0.1642 | 6973 | 3899 | 1767 | 1307 | 27.3 | 17558 | 34.2ms | 142.2ms | 9.4ms | 10.5ms | 2.6 MB |
| photo.jpg | 65397 | 73094 | 73176 | +0.1% | 1.1177 | 1.1190 | 64612 | 64411 | 193 | 8 | 5.2 | 18752 | 34.2ms | 128.7ms | 13.2ms | 13.1ms | 7.4 MB |
| photo.png | 471403 | 530273 | 530303 | +0.0% | 1.1249 | 1.1249 | 471282 | 471242 | 40 | 0 | 4.0 | 30939 | 381.9ms | 937.2ms | 95.0ms | 93.3ms | 57.0 MB |
| photo.webp | 60216 | 67741 | 67744 | +0.0% | 1.1250 | 1.1250 | 60210 | 60208 | 2 | 0 | 4.0 | 19166 | 30.3ms | 97.5ms | 12.0ms | 11.8ms | 7.1 MB |
| program.c | 99918 | 5611 | 6391 | +13.9% | 0.0562 | 0.0640 | 2977 | 1887 | 1009 | 81 | 91.1 | 15698 | 22.2ms | 41.3ms | 8.4ms | 8.7ms | 2.6 MB |
| random_os.bin | 262144 | 294904 | 294912 | +0.0% | 1.1250 | 1.1250 | 262128 | 262123 | 5 | 0 | 4.2 | 44411 | 177.6ms | 507.5ms | 52.5ms | 51.9ms | 29.8 MB |
| random_prng.bin | 262144 | 9144 | 5234 | -42.8% | 0.0349 | 0.0200 | 2135 | 1065 | 6 | 1064 | 244.4 | 18497 | 61.4ms | 101.7ms | 21.7ms | 20.7ms | 6.1 MB |
| sensor_i16.bin | 131072 | 146861 | 147018 | +0.1% | 1.1205 | 1.1217 | 130053 | 129778 | 275 | 0 | 4.7 | 25052 | 76.9ms | 262.3ms | 26.3ms | 26.4ms | 14.9 MB |
| server.log | 207757 | 49533 | 50161 | +1.3% | 0.2384 | 0.2414 | 22712 | 11982 | 10512 | 218 | 18.3 | 17925 | 103.7ms | 345.6ms | 24.8ms | 29.2ms | 6.8 MB |
| table.csv | 246142 | 88723 | 93438 | +5.3% | 0.3605 | 0.3796 | 34290 | 11347 | 21214 | 1729 | 10.2 | 24811 | 188.4ms | 571.0ms | 35.1ms | 46.0ms | 10.5 MB |
| text_en.txt | 34497 | 18492 | 17960 | -2.9% | 0.5360 | 0.5206 | 7155 | 2287 | 4862 | 6 | 6.6 | 5753 | 34.0ms | 97.4ms | 6.0ms | 8.1ms | 1.6 MB |

**Corpus totals:** input 4169694 B → V1_LZ 1852895 B (0.4444) → V2_raw 1872783 B (0.4491), overall Δ +1.07%.

**Head-to-head:** V2 smaller on 6 files, larger on 19, equal on 0. Median encode speedup V2 vs V1: **0.35x**; median decode speedup: **0.96x**.

## Chain-depth × lazy sweep (encode size / time)


**App.java**

| lazy | chain | size | enc time |
|---|---|---|---|
| 0 | 4 | 6547 | 31.4ms |
| 0 | 8 | 6531 | 32.0ms |
| 0 | 16 | 6515 | 33.4ms |
| 0 | 32 | 6505 | 33.0ms |
| 0 | 64 | 6494 | 32.8ms |
| 1 | 4 | 6458 | 36.5ms |
| 1 | 8 | 6451 | 37.0ms |
| 1 | 16 | 6435 | 38.0ms |
| 1 | 32 | 6429 | 39.3ms |
| 1 | 64 | 6421 | 40.5ms |

**big.json**

| lazy | chain | size | enc time |
|---|---|---|---|
| 0 | 4 | 128770 | 336.6ms |
| 0 | 8 | 126224 | 368.5ms |
| 0 | 16 | 125581 | 420.6ms |
| 0 | 32 | 125438 | 466.3ms |
| 0 | 64 | 123520 | 564.6ms |
| 1 | 4 | 122761 | 534.2ms |
| 1 | 8 | 121443 | 573.7ms |
| 1 | 16 | 120984 | 672.7ms |
| 1 | 32 | 119669 | 875.8ms |
| 1 | 64 | 116493 | 1.31s |

**counters_u32.bin**

| lazy | chain | size | enc time |
|---|---|---|---|
| 0 | 4 | 126576 | 216.8ms |
| 0 | 8 | 126576 | 216.7ms |
| 0 | 16 | 126576 | 218.0ms |
| 0 | 32 | 126576 | 222.2ms |
| 0 | 64 | 126576 | 217.2ms |
| 1 | 4 | 126576 | 228.3ms |
| 1 | 8 | 126576 | 230.3ms |
| 1 | 16 | 126576 | 231.3ms |
| 1 | 32 | 126576 | 229.3ms |
| 1 | 64 | 126576 | 228.7ms |

**server.log**

| lazy | chain | size | enc time |
|---|---|---|---|
| 0 | 4 | 56894 | 151.9ms |
| 0 | 8 | 54544 | 157.7ms |
| 0 | 16 | 53105 | 167.4ms |
| 0 | 32 | 52441 | 182.3ms |
| 0 | 64 | 51911 | 197.1ms |
| 1 | 4 | 53585 | 239.2ms |
| 1 | 8 | 51778 | 251.1ms |
| 1 | 16 | 50646 | 286.1ms |
| 1 | 32 | 50161 | 341.2ms |
| 1 | 64 | 49775 | 410.9ms |

## Window sweep — big.json (chain=32, lazy=1)

| window | raw size | enc time |
|---|---|---|
| 32 KiB | 119605 | 815.7ms |
| 64 KiB | 119669 | 871.3ms |
| 128 KiB | 118076 | 831.3ms |
| 256 KiB | 116923 | 813.4ms |
| 512 KiB | 116788 | 824.9ms |
| 1024 KiB | 116788 | 826.9ms |

## Round-trip fuzz gate

- Cases: 105000 randomized round-trips across 6 generators (random / low-alphabet / biased-runs / repetitive / piecewise / periodic records)
- Failures: **0** (0 decode errors, 0 mismatches)
- Tokens exercised: 15765463 literals, 1280079 matches, 261920 reps
- Elapsed: 55.7s; verdict **PASS**

## Findings

1. Correctness: every corpus file and all fuzz cases round-trip byte-exactly.
2. Raw representation is roughly size-neutral versus V1's fixed 3-byte match format: varint distances shrink short-distance (text/code) streams but grow >16 KiB distances; rep tokens are strictly cheaper than any V1 equivalent.
3. Rep offsets fire measurably exactly where predicted by the audit (periodic structured text).
4. The real gains are deferred to M2: these streams are still uncompressed bytes. Split-stream entropy coding is expected to cut literal-heavy streams substantially.
5. Chain depth beyond ~16 buys little size on this corpus while costing linearly more encode time (see sweeps).
