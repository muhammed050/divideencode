"""DE2 container format (M0 spec, frozen).

File layout:
    magic   b"DE2"            3 B
    version u8                == 1
    flags    u8               reserved (0)
    orig_total varint         total uncompressed size
    block_count varint
    header_crc32 le32         CRC-32 over all preceding header bytes

Block layout (repeated block_count times):
    mode      u8              0=RAW 1=RLE 2=LZ 3=DELTA+LZ 4=STRUCT+LZ
    tmeta_len varint
    tmeta     bytes           explicit transform-reversal metadata
    raw_len   varint          uncompressed size of this block
    comp_len  varint          payload byte count
    payload_crc32 le32        CRC-32 of the UNCOMPRESSED block bytes
    payload   bytes           comp_len bytes (mode-specific encoding)

Every field read is bounds-checked; unknown modes/flags, truncation,
trailing garbage and any CRC mismatch are hard errors. Blocks decode
independently.
"""
import zlib

from ..bitstream import encode_varint, decode_varint
from ..errors import CorruptedError, NotDivideEncodedError

from .classifier import MODE_RAW, MODE_NAMES

MAGIC = b"DE2"
VERSION = 1


def write_header(orig_total, block_count):
    out = bytearray()
    out += MAGIC
    out.append(VERSION)
    out.append(0)  # flags
    out += encode_varint(orig_total)
    out += encode_varint(block_count)
    crc = zlib.crc32(bytes(out)) & 0xFFFFFFFF
    out += crc.to_bytes(4, "little")
    return bytes(out)


def parse_header(blob):
    if len(blob) < 11 or blob[:3] != MAGIC:
        raise NotDivideEncodedError("not a DE2 container")
    version = blob[3]
    if version != VERSION:
        raise NotDivideEncodedError(
            "unsupported DE2 version %d" % version)
    flags = blob[4]
    if flags != 0:
        raise NotDivideEncodedError("unknown DE2 flags 0x%02x" % flags)
    end = len(blob)
    orig_total, pos = decode_varint(blob, 5, end)
    block_count, pos = decode_varint(blob, pos, end)
    if pos + 4 > end:
        raise CorruptedError("DE2 header truncated")
    stored_crc = int.from_bytes(blob[pos:pos + 4], "little")
    if zlib.crc32(blob[:pos]) & 0xFFFFFFFF != stored_crc:
        raise CorruptedError("DE2 header crc mismatch")
    return orig_total, block_count, pos + 4


class BlockHeader:
    __slots__ = ("mode", "tmeta", "raw_len", "payload")

    def __init__(self, mode, tmeta, raw_len, payload):
        self.mode = mode
        self.tmeta = tmeta
        self.raw_len = raw_len
        self.payload = payload


def write_block(mode, tmeta, raw_data, payload):
    tmeta = tmeta or b""
    out = bytearray()
    out.append(mode)
    out += encode_varint(len(tmeta))
    out += tmeta
    out += encode_varint(len(raw_data) if raw_data else 0)
    out += encode_varint(len(payload))
    crc = zlib.crc32(bytes(raw_data)) & 0xFFFFFFFF if raw_data else 0
    out += crc.to_bytes(4, "little")
    out += payload
    return bytes(out)


def iter_blocks(blob, pos, end, block_count, orig_total):
    """Yield BlockHeader objects; strict validation throughout."""
    remaining_orig = orig_total
    for idx in range(block_count):
        if pos >= end:
            raise CorruptedError("block %d missing" % idx)
        mode = blob[pos]
        pos += 1
        if mode not in MODE_NAMES:
            raise CorruptedError("invalid block mode %d" % mode)
        tmeta_len, pos = decode_varint(blob, pos, end)
        if tmeta_len > end - pos:
            raise CorruptedError("tmeta truncated in block %d" % idx)
        tmeta = bytes(blob[pos:pos + tmeta_len])
        pos += tmeta_len
        raw_len, pos = decode_varint(blob, pos, end)
        comp_len, pos = decode_varint(blob, pos, end)
        if raw_len > remaining_orig:
            raise CorruptedError("block %d exceeds declared total" % idx)
        if comp_len > end - pos:
            raise CorruptedError("block %d payload truncated" % idx)
        if pos + 4 > end:
            raise CorruptedError("block %d crc truncated" % idx)
        crc = int.from_bytes(blob[pos:pos + 4], "little")
        pos += 4
        payload = bytes(blob[pos:pos + comp_len])
        pos += comp_len
        remaining_orig -= raw_len
        yield BlockHeader(mode, tmeta, raw_len, payload), crc
    if pos != end:
        raise CorruptedError("trailing garbage after DE2 blocks")
    if remaining_orig != 0:
        raise CorruptedError("block sizes do not sum to original length")


def check_block_crc(data, crc):
    if zlib.crc32(data) & 0xFFFFFFFF != crc:
        raise CorruptedError("block crc32 mismatch: data corrupted")
