from .v1.encoder import compress, recursive_compress, MAGIC as CONTAINER_MAGIC, VERSION
from .v1.decoder import decompress, decompress_with_trace, parse_header
from .errors import DivideEncodeError, CorruptedError, NotDivideEncodedError
from .v1.strategies import render_trace

__version__ = "0.1.0"

__all__ = [
    "compress",
    "decompress",
    "decompress_with_trace",
    "parse_header",
    "recursive_compress",
    "render_trace",
    "DivideEncodeError",
    "CorruptedError",
    "NotDivideEncodedError",
    "CONTAINER_MAGIC",
    "VERSION",
    "__version__",
]
