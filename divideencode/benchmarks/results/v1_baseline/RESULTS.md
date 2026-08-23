# DivideEncode Benchmark Results

- Run: 2026-08-22 12:34:36
- Environment: Windows / Python 3.12.10 / AMD64
- Settings: DivideEncode depth=3; ZIP=raw deflate lvl9; GZIP lvl9; Brotli q11; Zstd lvl19; LZMA2 xz preset 9e
- NOTE: 'LZMA2(xz)' uses liblzma (the same algorithm family as 7-Zip); the 7z.exe binary itself was NOT TESTED.
- All tools verified lossless round-trip on every file before timing.
- Memory column is Python-side peak allocation measured with tracemalloc while compressing the first 128 KiB of each file (full-size tracing distorts timing and was therefore kept out of the timed path).

| File | Original | DE | ZIP(deflate) | GZIP | Brotli | Zstd | LZMA2(xz) |
|----|----|----|----|----|----|----|----|
| App.java | 100425 | 5526 (0.055) | 4042 (0.040) | 4060 (0.040) | 3277 (0.033) | 3476 (0.035) | 3188 (0.032) |
| app.js | 83043 | 5320 (0.064) | 3825 (0.046) | 3843 (0.046) | 3208 (0.039) | 3302 (0.040) | 3080 (0.037) |
| app.ts | 88275 | 5425 (0.061) | 3912 (0.044) | 3930 (0.045) | 3214 (0.036) | 3396 (0.038) | 3156 (0.036) |
| archive.zip | 74670 | 74682 (1.000) | 73935 (0.990) | 73953 (0.990) | 73750 (0.988) | 73778 (0.988) | 73960 (0.990) |
| big.json | 421249 | 93418 (0.222) | 75965 (0.180) | 75983 (0.180) | 53638 (0.127) | 58313 (0.138) | 50528 (0.120) |
| counters_u32.bin | 160000 | 9437 (0.059) | 44327 (0.277) | 44345 (0.277) | 31829 (0.199) | 34052 (0.213) | 21084 (0.132) |
| data.json | 393678 | 81528 (0.207) | 60382 (0.153) | 60400 (0.153) | 49045 (0.125) | 51601 (0.131) | 48628 (0.124) |
| doc.pdf | 44889 | 3471 (0.077) | 2536 (0.056) | 2554 (0.057) | 1338 (0.030) | 1565 (0.035) | 1108 (0.025) |
| dump.sql | 432464 | 70310 (0.163) | 53076 (0.123) | 53094 (0.123) | 40059 (0.093) | 45591 (0.105) | 42104 (0.097) |
| engine.cpp | 87326 | 5517 (0.063) | 3932 (0.045) | 3950 (0.045) | 3274 (0.037) | 3405 (0.039) | 3160 (0.036) |
| feed.xml | 150450 | 25021 (0.166) | 16581 (0.110) | 16599 (0.110) | 8833 (0.059) | 13215 (0.088) | 10096 (0.067) |
| lib.rs | 93269 | 5334 (0.057) | 3834 (0.041) | 3852 (0.041) | 3235 (0.035) | 3386 (0.036) | 3140 (0.034) |
| main.py | 88515 | 7481 (0.085) | 5526 (0.062) | 5544 (0.063) | 4198 (0.047) | 4702 (0.053) | 4416 (0.050) |
| notes.md | 20177 | 3159 (0.157) | 2226 (0.110) | 2244 (0.111) | 1613 (0.080) | 2000 (0.099) | 1936 (0.096) |
| page.html | 90574 | 15254 (0.168) | 10016 (0.111) | 10034 (0.111) | 5115 (0.056) | 5957 (0.066) | 4952 (0.055) |
| photo.jpg | 65397 | 65409 (1.000) | 64745 (0.990) | 64763 (0.990) | 64081 (0.980) | 64540 (0.987) | 65032 (0.994) |
| photo.png | 471403 | 471415 (1.000) | 471548 (1.000) | 471566 (1.000) | 471408 (1.000) | 471424 (1.000) | 471484 (1.000) |
| photo.webp | 60216 | 60228 (1.000) | 60236 (1.000) | 60254 (1.001) | 60220 (1.000) | 60226 (1.000) | 60276 (1.001) |
| program.c | 99918 | 5623 (0.056) | 4068 (0.041) | 4086 (0.041) | 3325 (0.033) | 3443 (0.034) | 3196 (0.032) |
| random_os.bin | 262144 | 262156 (1.000) | 262224 (1.000) | 262242 (1.000) | 262149 (1.000) | 262159 (1.000) | 262216 (1.000) |
| random_prng.bin | 262144 | 808 (0.003) | 5321 (0.020) | 5339 (0.020) | 1459 (0.006) | 1935 (0.007) | 1200 (0.005) |
| sensor_i16.bin | 131072 | 70900 (0.541) | 120084 (0.916) | 120102 (0.916) | 89271 (0.681) | 112948 (0.862) | 94192 (0.719) |
| server.log | 207757 | 49545 (0.238) | 34800 (0.168) | 34818 (0.168) | 28803 (0.139) | 30391 (0.146) | 27788 (0.134) |
| table.csv | 246142 | 81888 (0.333) | 61914 (0.252) | 61932 (0.252) | 46899 (0.191) | 50256 (0.204) | 42136 (0.171) |
| text_en.txt | 34497 | 18142 (0.526) | 12416 (0.360) | 12434 (0.360) | 11293 (0.327) | 11399 (0.330) | 11500 (0.333) |

## Compression / Decompression Speed

| File | DE comp | DE decomp | ZIP comp | Brotli comp | Zstd comp | LZMA2 comp |
|------|---------|-----------|----------|-------------|-----------|------------|
| App.java | 8.11s | 9.5ms | 1.3ms | 235.3ms | 119.2ms | 44.3ms |
| app.js | 7.33s | 7.4ms | 0.9ms | 197.8ms | 80.2ms | 37.6ms |
| app.ts | 7.15s | 8.1ms | 0.9ms | 202.2ms | 92.9ms | 40.0ms |
| archive.zip | 2.98s | 0.0ms | 2.4ms | 325.8ms | 6.6ms | 49.5ms |
| big.json | 37.77s | 265.2ms | 38.9ms | 636.3ms | 183.7ms | 199.7ms |
| counters_u32.bin | 2.66s | 44.9ms | 13.4ms | 199.1ms | 34.4ms | 39.8ms |
| data.json | 59.64s | 221.0ms | 26.2ms | 568.7ms | 181.4ms | 190.1ms |
| doc.pdf | 4.02s | 4.3ms | 0.6ms | 56.7ms | 22.5ms | 23.4ms |
| dump.sql | 19.85s | 199.4ms | 15.7ms | 898.4ms | 377.8ms | 700.4ms |
| engine.cpp | 9.28s | 8.8ms | 0.9ms | 200.9ms | 90.3ms | 36.8ms |
| feed.xml | 14.66s | 16.4ms | 2.9ms | 222.0ms | 124.7ms | 76.4ms |
| lib.rs | 10.50s | 8.4ms | 1.1ms | 221.3ms | 99.6ms | 40.6ms |
| main.py | 8.53s | 8.3ms | 1.4ms | 119.4ms | 63.0ms | 37.7ms |
| notes.md | 2.60s | 2.3ms | 0.2ms | 28.5ms | 13.1ms | 17.8ms |
| page.html | 11.46s | 9.9ms | 2.3ms | 152.9ms | 60.7ms | 50.8ms |
| photo.jpg | 3.91s | 0.1ms | 2.1ms | 296.5ms | 5.1ms | 24.4ms |
| photo.png | 10.14s | 0.2ms | 13.4ms | 277.2ms | 25.1ms | 100.8ms |
| photo.webp | 260.6ms | 0.0ms | 1.3ms | 33.4ms | 4.2ms | 24.6ms |
| program.c | 9.82s | 8.5ms | 1.0ms | 245.6ms | 114.7ms | 40.0ms |
| random_os.bin | 456.1ms | 0.1ms | 7.4ms | 72.8ms | 15.2ms | 61.2ms |
| random_prng.bin | 5.02s | 55.5ms | 2.2ms | 247.3ms | 50.5ms | 59.7ms |
| sensor_i16.bin | 2.66s | 81.7ms | 4.9ms | 750.0ms | 20.8ms | 45.2ms |
| server.log | 34.47s | 25.7ms | 6.8ms | 278.5ms | 111.5ms | 85.5ms |
| table.csv | 41.41s | 239.0ms | 17.9ms | 385.6ms | 140.8ms | 156.4ms |
| text_en.txt | 2.94s | 46.8ms | 2.4ms | 46.8ms | 10.9ms | 23.2ms |

## DivideEncode memory usage (tracemalloc peak)

| File | Peak memory |
|------|-------------|
| App.java | 6.5 MB |
| app.js | 6.5 MB |
| app.ts | 6.6 MB |
| archive.zip | 11.8 MB |
| big.json | 11.9 MB |
| counters_u32.bin | 23.1 MB |
| data.json | 11.7 MB |
| doc.pdf | 5.6 MB |
| dump.sql | 6.7 MB |
| engine.cpp | 6.5 MB |
| feed.xml | 6.6 MB |
| lib.rs | 6.4 MB |
| main.py | 6.8 MB |
| notes.md | 1.6 MB |
| page.html | 6.6 MB |
| photo.jpg | 11.8 MB |
| photo.png | 23.0 MB |
| photo.webp | 11.4 MB |
| program.c | 6.7 MB |
| random_os.bin | 23.0 MB |
| random_prng.bin | 6.7 MB |
| sensor_i16.bin | 23.0 MB |
| server.log | 11.9 MB |
| table.csv | 22.9 MB |
| text_en.txt | 6.3 MB |
