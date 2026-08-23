from .encoder import compress, recursive_compress, MAGIC as CONTAINER_MAGIC, VERSION
from .decoder import decompress, decompress_with_trace, parse_header
from .errors import DivideEncodeError, CorruptedError, NotDivideEncodedError
from .strategies import render_trace

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
    "__version__",
]
