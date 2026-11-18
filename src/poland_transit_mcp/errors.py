"""The one exception whose message reaches the caller as it is."""


class Refused(ValueError):
    """A request this server will not answer, with a sentence the caller can act on (a bad date, an unknown stop, a
    box too large). Any other exception from inside a tool is answered with a neutral line, because Python's own
    messages can quote the input or the server's internals."""
