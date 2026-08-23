import zlib

from .bitstream import encode_varint
from .features import EncodeContext
from .strategies import encode_node, DEFAULT_DEPTH

MAGIC = b"DE1"
VERSION = 1


def compress(data, depth=DEFAULT_DEPTH):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("compress expects bytes-like data")
    data = bytes(data)
    out = bytearray()
    out += MAGIC
    out.append(VERSION)
    out += encode_varint(len(data))
    out += zlib.crc32(data).to_bytes(4, "little")
    out += encode_node(data, depth, EncodeContext())
    return bytes(out)


class CompressResult:
    __slots__ = ("data", "original_size", "compressed_size")

    def __init__(self, data):
        self.data = data
        self.original_size = None
        self.compressed_size = len(data)


def recursive_compress(data, max_passes=16):
    sizes = [len(data)]
    current = bytes(data)
    for _ in range(max_passes):
        nxt = compress(current)
        if len(nxt) < len(current):
            current = nxt
            sizes.append(len(nxt))
        else:
            break
    return current, sizes
