"""DE2 "Fast HYB" compression engine (V2).

Pipeline per block:
    scan_features -> classify -> ONE transform -> LZ/TSE core ->
    separated token streams (Huffman literals) -> DE2 block container

Public API:
    compress(data, block_size=262144)  -> DE2 container bytes
    decompress(blob, verify=True)      -> original bytes

V1 is untouched and remains fully functional.
"""
from ..errors import CorruptedError
from ..patterns import rle_encode, rle_decode

from . import container, entropy, lz, transforms
from .classifier import (MODE_RAW, MODE_RLE, MODE_LZ, MODE_DELTA_LZ,
                         MODE_STRUCT_LZ, classify)
from .container import (MAGIC, VERSION, parse_header, iter_blocks,
                        write_header, write_block, check_block_crc)
from .features import scan_features

DEFAULT_BLOCK_SIZE = 262144   # 256 KiB


def _encode_block(data, max_chain=lz.MAX_CHAIN, lazy=lz.LAZY):
    fs = scan_features(data)
    mode, hint = classify(fs)

    payload = None
    tmeta = b""

    if mode == MODE_STRUCT_LZ:
        # structured transform lands in M3; plain LZ stays correct
        mode = MODE_LZ

    if mode == MODE_RLE:
        blob = rle_encode(data)
        if blob is not None:
            payload = blob
        else:
            mode = MODE_LZ

    if mode == MODE_DELTA_LZ:
        frame, tmeta = transforms.numeric_encode(
            data, mono=fs.mono32, hi_gain=fs.delta_ratio)
        payload = frame
        # fall through to size check with the ORIGINAL data length

    if mode == MODE_LZ and payload is None:
        payload = lz.encode(data, max_chain=max_chain, lazy=lazy)

    # RAW fallback rule: never expand a block
    if payload is None or len(payload) >= len(data):
        return write_block(MODE_RAW, b"", data, bytes(data))

    return write_block(mode, tmeta, data, payload)


def compress(data, block_size=DEFAULT_BLOCK_SIZE, max_chain=lz.MAX_CHAIN,
             lazy=lz.LAZY):
    """Compress bytes into a DE2 container.

    block_size: independent block granularity (64 KiB..1 MiB sensible;
        larger blocks trade ~1-2% better ratio for decode locality).
    max_chain/lazy: matcher knobs; the defaults are the benchmark-tuned
        DE2-balanced profile (chain 64 measured -1.4% size for +21% time;
        disabling lazy cost +12.7% size).
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("compress expects bytes-like data")
    data = bytes(data)
    n = len(data)
    blocks = []
    for off in range(0, n, block_size):
        chunk = data[off:off + block_size]
        blob = _encode_block(chunk, max_chain=max_chain, lazy=lazy)
        blocks.append(blob)
    out = bytearray()
    out += write_header(n, len(blocks))
    for b in blocks:
        out += b
    return bytes(out)


def block_modes(blob):
    """Introspection: list of mode names, one per block."""
    _orig_total, block_count, pos = parse_header(blob)
    return [container.MODE_NAMES.get(h.mode, str(h.mode))
            for h, _c in iter_blocks(blob, pos, len(blob), block_count,
                                     _orig_total)]


def decompress(blob, verify=True):
    orig_total, block_count, pos = parse_header(blob)
    end = len(blob)
    tables = entropy.DecodeTables()
    parts = []
    produced = 0
    for header, crc in iter_blocks(blob, pos, end, block_count, orig_total):
        raw_len = header.raw_len
        payload = header.payload
        mode = header.mode
        if mode == MODE_RAW:
            if len(payload) != raw_len:
                raise CorruptedError("raw block size mismatch")
            data = payload
        elif mode == MODE_RLE:
            data, _p = rle_decode(payload, 0, len(payload), raw_len)
        elif mode == MODE_LZ:
            data, _p = lz.decode(payload, 0, len(payload), raw_len, tables)
        elif mode == MODE_DELTA_LZ:
            inner, _p = lz.decode(payload, 0, len(payload), raw_len, tables)
            data = transforms.transform_decode(inner, header.tmeta, raw_len)
            if len(data) != raw_len:
                raise CorruptedError("delta produced wrong block size")
        else:
            raise CorruptedError("unhandled block mode %d" % mode)
        if verify:
            check_block_crc(data, crc)
        parts.append(data)
        produced += len(data)
    if produced != orig_total:
        raise CorruptedError("assembled size mismatch")
    return b"".join(parts)
