# CORRECTNESS AUDIT — Tests, Coverage, Malformed-Input Robustness

Date: 2026-08-22 · Read-only; suite executed unmodified: **59 tests, 6 files, all pass in ~10.9 s.**

---

## 1. Test inventory

| File | Tests | What it covers |
|---|---:|---|
| `test_roundtrip.py` | 9 | ASCII/UTF-8 text, synthetic JSON/CSV, repeating patterns, long runs, incrementing bytes, binary all-values, structured u16 arrays, SHA-256 verification over sizes 0–7777 |
| `test_edge_cases.py` | 13 | empty (asserts exactly 10-byte container), 1-byte ×3 values, 2 bytes, all-zero/all-one 100 KB, periodic+noise, Unicode+emoji, structured binary, 512 KiB os.urandom, recursive_compress termination & losslessness, bytearray input, depth parameter 0/1/2/4 |
| `test_random.py` | 5 | crypto-random never corrupts (1→16384), LCG multi-seed/multi-size, biased low-entropy alphabets, random-data overhead ≤ +64 B, hex-text |
| `test_corruption.py` | 9 | header byte flips (0..11), sampled payload flips (**every** flip must be detected), truncation set, garbage suffix, bad magic/version, empty/garbage containers, CRC catches silently-wrong output |
| `test_features.py` | 9 | feature values on uniform/text/random distributions, hi-zero LE stats, memo roundtrip, cache isolation between EncodeContext instances, no stale state across compress calls, depth-in-memo-key semantics |
| `test_rle.py` | 14 | RLE regression suite for the fixed P0 `lit_start` bug: boundaries 127/128/129/130, separated/adjacent/no runs, 200-trial fuzz, container-level roundtrip on run-heavy data |

## 2. Coverage matrix vs required categories

| Category | Unit-tested? | Notes |
|---|---|---|
| empty file | ✔ | exact size asserted |
| 1-byte / 2-byte | ✔ | |
| random data | ✔✔ | crypto + PRNG + biased + bounded-overhead |
| all-zero / repeated bytes | ✔ | |
| UTF-8 / ASCII | ✔ | |
| JSON / CSV | ◐ | synthetic generators only; real `big.json`/`table.csv` exercised solely by benchmarks (no assertions) |
| JavaScript / TypeScript / Java | ✘ | samples exist; no unit tests |
| SQL / HTML / XML | ✘ | same gap |
| compressed files (zip/jpg/png/webp/pdf) | ✘ | samples only — no "must not inflate >12 B" unit assertions |
| large files (>512 KiB) | ✘ | largest unit input is 512 KiB |
| fuzz / property testing | ◐ | only RLE has real fuzzing; no property tests over `decode_node`, varint, bitstream, huffman tables |

## 3. Malformed-input robustness (code inspection + empirical probes)

**Strong (verified in code):** magic+version checks; LEB128 capped at 56 bits with overflow error; every leaf read bounds-checked against `end`; RAW truncated check; LZ offset ∈ [1,len(out)] and overrun rejection; DIVIDE wcode/did/wq range checks + remainder-range + word-overflow validation and child-size equality checks; DICT count ≤1020, entry-length vs remaining bytes, sub_len ≤2³¹, id-range and exact-output-size checks; Huffman table count 1..256, code lengths 1..32, canonical completeness Σ2^(max−len)==2^max, invalid-code detection; RLE strict output-size equality; trailing-garbage rejection; CRC-32 verified by default.

**Gaps found:**

1. **Recursion-depth attack (empirically confirmed):** a crafted stream of N consecutive DELTA mode-bytes makes `decode_node` recurse N levels deep; at N=5000 this raises bare `RecursionError` — *not* a `DivideEncodeError` — so `cli.main()`'s handler misses it and the CLI dies with a traceback instead of a clean error. Severity low (bounded by input length) but violates the documented error contract. Same shape applies to adversarial DICT/DIVIDE nesting.
2. **Encoder/decoder asymmetry:** decoder accepts up to 1020 dict entries while encoder caps at 255 — safe, but the wider surface is untested.
3. Untested decoder paths reachable only via crafted input: varint overflow, invalid LZ back-reference/overrun, incomplete/oversubscribed Huffman tables, mid-stream invalid codes (payload-flip sampling covers some indirectly), divide remainder-out-of-range.
4. `verify_crc=False` and `decompress_with_trace` structure are untested.
5. Big-endian host correctness (`sys.byteorder` branches in `features.py`/`divide_transform.py`) has zero coverage.
6. No determinism test pinning byte-identical output across runs (holds today by construction: sorted keys, tie counters, no RNG).
7. No CLI-level tests (error paths, exit codes).
8. No resource-bound tests (time/memory ceilings on hostile inputs).
9. `recursive_compress` multi-pass correctness tested only lightly (single case).

## 4. Missing-test priority list

1. Property/fuzz harness feeding random bytes and mutated valid containers into `decompress`, asserting only `DivideEncodeError` (or success) escapes — would have caught finding #1.
2. Recursion-depth guard test once a depth limit exists.
3. Round-trip unit tests over the actual `samples/` corpus with ratio bounds per class (esp. "incompressible ⇒ ≤ +16 B").
4. Direct negative tests for each malformed branch listed above.
5. Determinism golden-blob test.
