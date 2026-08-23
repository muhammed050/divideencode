# DivideEncode

Experimental lossless compression research project.

The repository contains the original V1 implementation and the newer DE2/V2 pipeline. V1 is kept intact for regression and benchmark comparison.

## DE2 architecture

```text
Input
  -> feature scan
  -> deterministic classifier
  -> RAW / RLE / LZ / DELTA+LZ
  -> entropy coding
  -> DE2 block container
  -> CRC validation
```

### Core DE2 components

- `features.py` — block-level feature extraction
- `classifier.py` — deterministic codec selection
- `lz.py` — hash-chain LZ with lazy parsing and REP offsets
- `entropy.py` — canonical Huffman with raw fallback and fast decoding
- `transforms.py` — numeric delta/zigzag/plane-split transforms
- `container.py` — versioned block container with CRCs and strict validation

## Current status

DE2 M0–M5 are implemented and benchmarked. The current balanced profile uses 256 KiB blocks, chain length 32, and lazy parsing. The benchmark corpus shows DE2 improving the V1 ratio on 20/25 files while compressing substantially faster than V1.

See `divideencode-2/docs/DE2_STATUS.md` and `divideencode-2/benchmarks/results/v2_RESULTS.md` for the current engineering and benchmark status.

## Repository layout

The `chore/restructure-cleanup` branch is being used to clean the historical workspace into a stable project layout. During this transition, legacy directories are intentionally preserved until their contents are verified and migrated; V1 remains untouched.

```text
divideencode-2/
  divideencode/   # DE2 implementation
  tests/          # DE2 tests
  benchmarks/     # benchmark runners/results
  docs/           # design and status reports
  samples/        # benchmark/sample data

divideencode/     # original V1 implementation
```

## Design principles

1. Lossless correctness comes first.
2. V1 remains regression-frozen.
3. Codec selection is deterministic and benchmark-driven.
4. Incompressible data must not suffer large expansion.
5. New algorithms are added only when measurements justify them.

## Development

Run the test suite from the DE2 project directory and keep benchmark results reproducible. Generated Python caches and local scratch files should never be committed.
