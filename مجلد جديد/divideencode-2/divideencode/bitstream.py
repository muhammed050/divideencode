from .errors import CorruptedError


def encode_varint(n):
    if n < 0:
        raise ValueError("varint requires non-negative integer")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def decode_varint(buf, pos, end):
    result = 0
    shift = 0
    while True:
        if pos >= end:
            raise CorruptedError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 56:
            raise CorruptedError("varint overflow")


class BitWriter:
    __slots__ = ("buf", "cur", "n")

    def __init__(self):
        self.buf = bytearray()
        self.cur = 0
        self.n = 0

    def write_bits(self, value, nbits):
        self.cur = (self.cur << nbits) | value
        self.n += nbits
        while self.n >= 8:
            self.n -= 8
            self.buf.append((self.cur >> self.n) & 0xFF)
        self.cur &= (1 << self.n) - 1

    def getvalue(self):
        out = bytes(self.buf)
        tail = self.n
        if tail:
            out += bytes(((self.cur << (8 - tail)) & 0xFF,))
        return out

    def bit_length(self):
        return len(self.buf) * 8 + self.n


class BitReader:
    __slots__ = ("buf", "pos", "end", "cur", "n")

    def __init__(self, buf, pos, end):
        self.buf = buf
        self.pos = pos
        self.end = end
        self.cur = 0
        self.n = 0

    def read_bit(self):
        if self.n == 0:
            if self.pos >= self.end:
                raise CorruptedError("bitstream exhausted")
            self.cur = self.buf[self.pos]
            self.pos += 1
            self.n = 8
        self.n -= 1
        return (self.cur >> self.n) & 1

    def read_bits(self, nbits):
        while self.n < nbits:
            if self.pos >= self.end:
                raise CorruptedError("bitstream exhausted")
            self.cur = (self.cur << 8) | self.buf[self.pos]
            self.pos += 1
            self.n += 8
        self.n -= nbits
        return (self.cur >> self.n) & ((1 << nbits) - 1)

    def align_tail_bits(self):
        return self.n
