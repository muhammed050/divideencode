"""DivideEncode V2 -- DE2 container scaffolding (M0).

Header-only plumbing; the payload codec lives in codec.py (M2).

V1 remains fully intact and independently importable:
    from divideencode import compress, decompress          # V1 (DE1)
    from divideencode.v2 import compress, decompress        # V2 (DE2)
"""
from ..bitstream import encode_varint, decode_varint

MAGIC = b"DE2"
VERSION = 2

HEADER_MIN_LEN = len(MAGIC) + 1


class ContainerError(ValueError):
    """Raised for malformed DE2 containers."""


def build_header(original_length, crc32):
    """Serialise the fixed DE2 pre-payload header."""
    if original_length < 0:
        raise ValueError("negative length")
    out = bytearray()
    out += MAGIC
    out.append(VERSION)
    out += encode_varint(original_length)
    out += int(crc32 & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(out)


def parse_header(buf):
    """Validate a DE2 header; return (original_length, crc32, payload_pos)."""
    if not isinstance(buf, (bytes, bytearray, memoryview)):
        raise ContainerError("container must be bytes-like")
    buf = bytes(buf[:HEADER_MIN_LEN + 13])
    if len(buf) < HEADER_MIN_LEN or buf[:len(MAGIC)] != MAGIC:
        raise ContainerError("not a DE2 container")
    pos = len(MAGIC)
    if buf[pos] != VERSION:
        raise ContainerError("unsupported DE2 version %d" % buf[pos])
    pos += 1
    try:
        original_length, pos = decode_varint(buf, pos, len(buf))
    except IndexError:
        raise ContainerError("truncated DE2 header") from None
    if pos + 4 > len(buf):
        raise ContainerError("truncated DE2 header")
    crc32 = int.from_bytes(buf[pos:pos + 4], "little")
    return original_length, crc32, pos + 4


__all__ = [
    "MAGIC", "VERSION", "ContainerError",
    "build_header", "parse_header",
]
