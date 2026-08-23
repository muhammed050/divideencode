# BENCHMARK AUDIT — DivideEncode vs zlib/gzip/Brotli/Zstd/LZMA2(xz)

Date: 2026-08-22 · All fresh measurements taken on this machine (Python 3.12, Windows) in read-only mode.

---

## 0. CRITICAL FINDING — all retained benchmark artifacts are stale

Every stored result predates the beam-search implementation now at HEAD:

| Artifact | Mode it captured | Evidence |
|---|---|---|
| `results/baseline.json/log` | exhaustive | App.java: rle/huff/lz each called **217 times** (= every node) — impossible under beam's `LEAF_K=2`; rle time 17.1 s reflects the since-fixed RLE bug era |
| `results/phase_p0.json`, `phase_p1.json/log` | exhaustive + P1 caches | same node counts; sizes identical to baseline |
| `run_optimized.log` (competitor table) | exhaustive-era DE | DE times 2.7–43.7 s match stale logs |

Therefore the premise numbers quoted in the audit brief (App.java ≈24.83 s, big.json ≈43.66 s, …) describe code that no longer exists at HEAD.

## 1. Fresh HEAD measurements — beam vs exhaustive (same machine, today)

| File | n | beam size | beam time | exh size | exh time | Δsize |
|---|---:|---:|---:|---:|---:|---:|
| App.java | 100,425 | 5,526 | 0.16 s | 5,526 | ~25.8 s* | 0% |
| app.js | 83,043 | 5,320 | 0.15 s | 5,320 | ~12.8 s* | 0% |
| app.ts | 88,275 | 5,425 | 0.15 s† | 5,425 | ~13.2 s* | 0% |
| big.json | 421,249 | 93,418 | 1.67 s | 93,418 | 49.5 s | 0% |
| data.json | 393,678 | 86,087 | 0.38 s | 81,528 | 93.9 s | **+5.6%** |
| table.csv | 246,142 | 88,735 | 0.43 s | 81,888 | 64.6 s | **+8.4%** |
| server.log | 207,757 | 49,545 | 0.30 s | 49,545 | 49.9 s | 0% |
| random_prng.bin | 262,144 | 3,470 | 0.57 s | **808** | 7.0 s | **+329%** |
| notes.md | 20,177 | 3,159 | 0.09 s | 3,159 | ~5.3 s* | 0% |
| text_en.txt | 34,497 | 18,142 | 1.14 s | 18,142 | ~5.6 s* | 0% |

\* from stored phase_p1.log · † extrapolated from app.js timing profile.
All round-trips verified lossless (`decompress == input`).

Interpretation:
- Beam is **30–160× faster** than the stale published numbers on identical outputs for source code and JSON.
- Beam costs ratio where predictors mis-rank: structured tabular (+5–8%) and one severe outlier (random_prng.bin, whose winning DELTA→RLE chain gets pruned).

## 2. Competitor comparison (only 6 files have competitor rows)

From `run_optimized.log` (DE sizes unchanged under beam except where noted):

| File | orig | DE | ZIP-9 | GZIP-9 | Brotli-11 | Zstd-19 | xz-9e | DE vs best | DE c-time (stale → beam) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| App.java | 100,425 | 5,526 | 4,042 | 4,060 | 3,277 | 3,476 | **3,188** | +73.3% | 24.8 s → 0.16 s |
| app.js | 83,043 | 5,320 | 3,825 | 3,843 | 3,208 | 3,302 | **3,080** | +72.7% | 9.8 s → 0.15 s |
| app.ts | 88,275 | 5,425 | 3,912 | 3,930 | 3,214 | 3,396 | **3,156** | +71.9% | 9.6 s → 0.15 s |
| archive.zip | 74,670 | 74,682 | 73,935 | 73,953 | **73,750** | 73,778 | 73,960 | +1.3% | 2.7 s → ~0.2 s |
| big.json | 421,249 | 93,418 | 75,965 | 75,983 | 53,638 | 58,313 | **50,528** | +84.9% | 43.7 s → 1.67 s |

Decompression (all files): DE 7–260 ms pure-Python vs <0.1–5 ms for competitors ⇒ **10–250× slower decode**.

Full-corpus competitor coverage is missing for 19/25 samples (log truncated after big.json); no `benchmark_results.csv` was retained. A complete re-run is required before/with any V2 milestone.

## 3. Corpus-wide DE behavior (exhaustive-era sizes = beam sizes except noted)

Best DE cases:
- `counters_u32.bin` 160,000 → 9,437 (ratio 0.059) — DIVIDE plane split
- `random_prng.bin` 262,144 → **808** (0.003) — DELTA→RLE chain; *beats zlib/gzip outright*; regresses to 3,470 under beam
- `doc.pdf` 44,889 → 3,471 · `notes.md` 20,177 → 3,159

Worst DE cases (ratio): `text_en.txt` 0.526 (plain English! competitors reach ~0.35–0.40), `server.log` 0.238, `page.html` 0.168, `feed.xml` 0.166, `table.csv` 0.333, `data.json` 0.207–0.219.

Structured-data failures: CSV/JSON/SQL/XML land 45–85% above xz because DE has no numeric/column modeling, no repeat offsets, a 64 KiB window that misses cross-record repeats, and fixed-width tokens.

Already-compressed / random behavior: excellent and safe — constant **+12 B** overhead (magic 3 + version 1 + varint ≤3 + CRC 4 + RAW mode 1) on photo.jpg/png/webp, archive.zip, random_os.bin; fast RAW fallback (photo.webp: 4 nodes, 0.3–0.6 s).

Speed ranking (beam HEAD): worst `big.json` 1.67 s (~0.25 MB/s), `text_en.txt` 1.14 s, `data.json` 0.38 s; best `notes.md` 0.09 s. Decode worst: `big.json` ~0.26 s (~1.6 MB/s).
