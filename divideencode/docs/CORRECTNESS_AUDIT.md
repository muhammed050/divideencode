# CORRECTNESS AUDIT — tests, robustness, security

## 1. Existing test coverage

| Area | Covered by | Verdict |
|---|---|---|
| Empty file (container = 10 B asserted) | test_edge_cases | good |
| 1-byte / 2-byte files | test_edge_cases, test_roundtrip(SHA) | good |
| Crypto-random 1 B–16 KB, PRNG, biased alphabets | test_random | good |
| Inflation bound ≤ header+64 B on random data | test_random.test_random_ratio_is_bounded | excellent (rare) |
| all-zero / all-0xFF / patterns / periodic+noise | test_edge_cases | good |
| UTF-8 text, mixed lines | test_roundtrip | good |
| JSON-like / CSV-like synthetic | test_roundtrip | basic only |
| Binary structured (i16 arrays), incrementing bytes | test_roundtrip/edge_cases | good |
| Large input 512 KiB random | test_edge_cases | present |
| recursive_compress termination + lossless | test_edge_cases | good |
| depth parameter 0..4 | test_edge_cases | good |
| bytearray/memoryview acceptance | test_edge_cases | good |
| Corruption: every header byte flip, sampled payload flips (must ALL be detected), truncations, appended garbage, bad magic/version, CRC silent-damage check | test_corruption | strong |
| JS/TS/Java/Rust/C/C++ real files, HTML/XML, SQL, PDF, ZIP, images | **not in tests** (only benchmark samples) | gap |
| Fuzzing / property-based testing (hypothesis etc.) | none | gap |
| Mode-specific malformed streams (bad wcode/divisor/wq, dict id overflow, huffman incomplete table, lz invalid distance) | none (only whole-container flips) | gap |
| Recursion-depth attack | none | gap |

## 2. Robustness findings (code inspection + empirical probes)

### F1 — Unbounded decoder recursion (confirmed empirically)
`decode_node` recurses for MODE_DELTA/DIVIDE/DICT children with no depth limit. A crafted container of ~5000 nested `MODE_DELTA` bytes raises `RecursionError`, which is **not** a `DivideEncodeError` and escapes the CLI/`decompress` error contract (probe: N=900 OK; N=5000 → RecursionError). Encoder-side recursion is bounded (depth≤3), so this is a decode-surface issue only. Severity: medium (DoS-style crash on malicious input; no memory unsafety).

### F2 — Integer/size validation
Good: varint shift>56 rejected; RAW bounds-checked against `end`; RLE/LZ/HUFF/DIVIDE/DICT each validate truncation, ranges (`off∈[1,len(out)]`, overrun vs expected_len, Kraft completeness for Huffman tables, remainder<d, word overflow, dict count/id limits). Weak spots: `orig_len` unbounded relative to blob (safe due to per-mode checks); RLE run token can overshoot expected_len transiently before the final equality check (memory-bounded by stream length ×130).

### F3 — CRC scope
CRC32 over original data, little-endian, verified post-decode. Detects accidental corruption; not adversarial-grade (32-bit, non-keyed). Test suite asserts *every* single-byte payload flip is caught — currently true structurally or via CRC.

### F4 — Dead code
`BitWriter`, `BitReader.read_bits`, `BitReader.align_tail_bits`, `huffman.parse_table` (logic duplicated inline in `decode`), `encoder.CompressResult`. No behavioral risk; hygiene item.

### F5 — Minor asymmetries
Decoder accepts RLE run codes 126–127 encoder never emits; DICT entry-count limit decoder=1020 vs encoder=255. Both harmless but worth pinning with format docs/tests.

## 3. Missing tests to add (V2 prep)
- Malformed-stream unit tests per mode (F2 list) incl. deep-nesting container asserting `DivideEncodeError` (or explicit depth cap) rather than `RecursionError`.
- Real-world corpus round-trips: app.js/App.java/big.json/archive.zip/photo.* as regression fixtures.
- Property tests: arbitrary bytes ⇒ decompress(compress(x)) == x, size bound ≤ n + c.
- Determinism test: same input → byte-identical blob across runs.
- Decompression-time bound test on adversarial small-input/large-output containers (e.g., RLE/LZ amplification) to pin DoS resistance.

## 4. Overall verdict
Round-trip correctness is solid across the exercised space (28 corpus files + broad synthetic cases all verify). The one genuine defect class is malicious-input surface (F1); everything else is hygiene or coverage expansion.
