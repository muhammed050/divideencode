class DivideEncodeError(Exception):
    pass


class CorruptedError(DivideEncodeError):
    pass


class NotDivideEncodedError(DivideEncodeError):
    pass
