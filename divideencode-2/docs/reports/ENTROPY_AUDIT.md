# ENTROPY AUDIT — DivideEncode

Scope: `divideencode/huffman.py` plus every place entropy is (not) exploited. Read-only.

---

## 1. Inventory of entropy coders

| Coder | Where | Model | Static/Adaptive |
|---|---|---|---|
| Order-0 canonical Huffman | `huffman.py`, MODE_HUFF leaf only | single byte distribution over whole node | static (table serialized per node) |
| Implicit order-0 in LZ/RLE/DICT/DIVIDE streams | none | fixed-width bytes | — |

That is the complete list. There is no arithmetic/range coding, no FSE/rANS, no context modeling anywhere.

## 2. Implementation details

**Code construction** (`build_code_lengths`): heap of `(freq, tie_counter, symbol_list)`; depths incremented by walking merged symbol lists → O(k log k) merges but list concatenation makes worst case O(k²) for skewed alphabets (k ≤ 256, so bounded ~65K ops). Deterministic via tie counter. Length limit 32 enforced by recursive frequency halving (`(f+1)//2`) — terminates because repeated halving flattens the distribution.

**Canonicalization**: sort by (length, symbol), standard `code <<= Δlen; code++`. Encoder and decoder recompute identical codes from lengths alone.

**Table serialization**: varint(count) + 2 B/symbol (sym,len) ⇒ ≤514 B overhead; count>256 rejected on decode; completeness check Σ2^(maxlen−len) == 2^maxlen rejects incomplete/oversubscribed tables (invalid codes caught by `cur_len > max_len`). This validation is correct and strict.

**Encode cost**: bit-packing with Python int accumulator; ≥32 KiB path precomputes a **65536-entry pair table** (built per call — O(65536) even if only used once) then codes 2 bytes/step. Entropy gate ≥8 KiB: bail if `n·H0/8 + 2k + 8 ≥ n − n/64`.

**Decode cost**: `BitReader.read_bit()` one *method call per output bit*, linear canonical search per symbol (`idx = val − first_code[len]`) — correct but the slowest possible decode design (~10–20× slower than table-driven).

## 3. Theoretical limits of the current entropy model

Order-0 static Huffman on a byte alphabet has expected length H0 + p_max per symbol (Huffman redundancy < 1 bit/symbol worst case, typically ≈0.02–0.05 bits). It cannot beat:

```
n · H0 / 8 + 2k + 8 + O(1)   bytes
```

Anything whose information lives **between** bytes is invisible to it.

## 4. What information about the input is the current compressor failing to model?

Explicitly, by class:

| Missing concept | Where it costs today (measured corpus) |
|---|---|
| **Byte context (order-1/2)** | JSON: `"` after `{`, `:` after key — near-deterministic transitions coded at full H0 cost. text_en.txt lands at 18142 B for 34497 B English (0.53 ratio) where order-N coders reach ~0.35 |
| **LZ literal statistics** | literals inside MODE_LZ are raw; huff leaf competes instead of composing |
| **Match length distribution** | len stored flat in 1 B; short matches dominate real data and would code at ~2–4 bits under any entropy coder |
| **Distance distribution** | offsets stored flat in 2 B; real distances cluster (same-record fields) |
| **Flag entropy** | literal/match bitmap is ~50/50 random-looking but costs 1 bit each regardless |
| **Repeated tokens / keys** | DICT partially covers this but is all-or-nothing per node with a 255-entry cap and byte-aligned escape coding |
| **Numeric columns** | DIVIDE covers power-of-two alignment cases only; decimal numbers in CSV/SQL/JSON get no model |
| **Structured records** | no field-aware split, no column detection (see COMPRESSION_AUDIT) |
| **Local statistics** | one static table per node; no blocks, no adaptation |
| **Length/distance interdependence** | not modeled at all |

Bottom line: the entropy stage models exactly one distribution (whole-node byte histogram), applied as an alternative to other leaves rather than as a layer beneath them.
