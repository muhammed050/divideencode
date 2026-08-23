import zlib

from .bitstream import encode_varint
from .features import EncodeContext
from .strategies import (encode_node, DEFAULT_DEPTH, ENABLE_WORK_BUDGET,
                         WORK_BUDGET_FACTOR, WORK_BUDGET_MIN)

MAGIC = b"DE1"
VERSION = 1


def compress(data, depth=DEFAULT_DEPTH):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("compress expects bytes-like data")
    data = bytes(data)
    if ENABLE_WORK_BUDGET:
        limit = max(WORK_BUDGET_FACTOR * len(data), WORK_BUDGET_MIN)
    else:
        limit = None
    out = bytearray()
    out += MAGIC
    out.append(VERSION)
    out += encode_varint(len(data))
    out += zlib.crc32(data).to_bytes(4, "little")
    out += encode_node(data, depth, EncodeContext(work_limit=limit))
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
