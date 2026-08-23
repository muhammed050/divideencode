"""DivideEncode V2 -- versioned container scaffolding (M0) + codec (M2).

V1 remains fully intact and independently importable:
    from divideencode import compress, decompress          # V1 (DE1)
    from divideencode.v2 import compress, decompress        # V2 (DE2)
"""
from .container import (MAGIC, VERSION, ContainerError,
                        build_header, parse_header)
from .codec import compress, decompress

__all__ = [
    "MAGIC", "VERSION", "ContainerError",
    "build_header", "parse_header",
    "compress", "decompress",
]
