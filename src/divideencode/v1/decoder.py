import zlib

from .bitstream import decode_varint
from .errors import CorruptedError, NotDivideEncodedError
from .strategies import decode_node

MAGIC = b"DE1"


def parse_header(blob):
    if len(blob) < 5 or blob[:3] != MAGIC:
        raise NotDivideEncodedError("not a DivideEncode container")
    version = blob[3]
    if version != 1:
        raise NotDivideEncodedError("unsupported container version %d" % version)
    orig_len, pos = decode_varint(blob, 4, len(blob))
    if pos + 4 > len(blob):
        raise CorruptedError("container header truncated")
    crc = int.from_bytes(blob[pos:pos + 4], "little")
    return orig_len, crc, pos + 4


def decompress(blob, verify_crc=True):
    orig_len, crc, pos = parse_header(blob)
    data, pos = decode_node(blob, pos, len(blob), orig_len)
    if pos != len(blob):
        raise CorruptedError("trailing garbage after compressed stream")
    if verify_crc and zlib.crc32(data) != crc:
        raise CorruptedError("crc32 mismatch: data is corrupted")
    return data


def decompress_with_trace(blob, verify_crc=True):
    orig_len, crc, pos = parse_header(blob)
    trace = []
    data, pos = decode_node(blob, pos, len(blob), orig_len, trace)
    if pos != len(blob):
        raise CorruptedError("trailing garbage after compressed stream")
    if verify_crc and zlib.crc32(data) != crc:
        raise CorruptedError("crc32 mismatch: data is corrupted")
    return {
        "original_size": orig_len,
        "compressed_size": len(blob),
        "stored_crc32": crc,
        "actual_crc32": zlib.crc32(data),
        "trace": trace,
        "data": data,
    }
