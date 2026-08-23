"""V2 research track package (M1: LZ2, M2: split-stream entropy). Isolated
from DivideEncode V1."""

from .lz2 import (
    MIN_MATCH,
    DEFAULT_WINDOW,
    DEFAULT_MAX_CHAIN,
    DEFAULT_LAZY,
    DEFAULT_NICE_LENGTH,
    Config,
    LZ2Error,
    TokenStats,
    encode_varint,
    decode_varint,
    tokenize,
    detokenize,
    tokenize_tokens,
    serialize_raw,
    analyze,
)
from .entropy import (
    compress as lz2h_compress,
    decompress as lz2h_decompress,
    entropy_encode,
    entropy_decode,
)

__version__ = "0.2.0-m2"

__all__ = [
    "MIN_MATCH", "DEFAULT_WINDOW", "DEFAULT_MAX_CHAIN", "DEFAULT_LAZY",
    "DEFAULT_NICE_LENGTH", "Config", "LZ2Error", "TokenStats",
    "encode_varint", "decode_varint", "tokenize", "detokenize",
    "tokenize_tokens", "serialize_raw", "analyze",
    "lz2h_compress", "lz2h_decompress", "entropy_encode", "entropy_decode",
    "__version__",
]
