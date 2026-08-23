"""V2 M2 — split-stream entropy coding for LZ2 token streams.

Isolated research module (V2 track). V1 stays untouched; the canonical
Huffman construction is REUSED from the frozen, validated implementation in
divideencode.v1.huffman.py.
"""

from bisect import bisect_right

from divideencode.v1.huffman import build_code_lengths, canonical_codes

from .lz2 import (
    MIN_MATCH,
    Config,
    LZ2Error,
    tokenize_tokens,
    encode_varint,
    decode_varint,
)

TYPE_LIT = 0
TYPE_MATCH = 1
TYPE_REP0 = 2
TYPE_MAX = 4
MODE_RAW = 0
MODE_ENTROPY = 1
MAX_CHUNK = 1 << 16
LEN_MAX = 1 << 17
DIST_MAX = 1 << 20
MAX_CODE_LEN = 32

class EntropyError(Exception):
    pass

