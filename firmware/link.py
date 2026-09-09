"""Single-line result envelope for MicroPython functions invoked from the host.

The host sends one statement over the REPL (`mpremote exec`) and reads stdout.
Every remotely callable function therefore prints exactly one line:

* success: ``OK`` (no payload), or ``OK <payload>``
* failure: ``ERR <ExceptionType>``, or ``ERR <ExceptionType> <message>``

Payload encoding is chosen from the return type, so the host decodes by the
call it made instead of sniffing the text:

| Return value | Payload |
|---|---|
| ``None`` | none (bare ``OK``) |
| ``bytes`` / ``bytearray`` | base64, padded, no newline |
| ``str`` | verbatim (must already be one line) |
| ``bool`` / ``int`` / ``float`` / ``dict`` / ``list`` / ``tuple`` | JSON |

The line is the whole contract: an exception message spanning several lines is
collapsed to single spaces, and a payload string containing a newline is
refused (`LinkError`) rather than silently truncated, because a second line
would be parsed by the host as the envelope.

MicroPython notes: `binascii`, `ubinascii`, and `base64` are all accepted for
base64, and `json` falls back to `ujson`. Function objects on MicroPython do
not accept attribute assignment, so `envelope` does not copy `__name__` or
stash the wrapped function on the wrapper. Modules that need both an enveloped
and a plain entry point (see `session.py`) keep the plain function under its
own name.
"""

try:
    from binascii import a2b_base64 as _a2b, b2a_base64 as _b2a
except ImportError:
    try:
        from ubinascii import a2b_base64 as _a2b, b2a_base64 as _b2a
    except ImportError:
        from base64 import b64decode as _a2b, b64encode as _b2a

try:
    import json
except ImportError:
    import ujson as json

OK_PREFIX = "OK"
ERR_PREFIX = "ERR"

# Types serialized as JSON. bytes and str are handled before this tuple.
JSON_TYPES = (bool, int, float, dict, list, tuple)


class LinkError(Exception):
    """A value cannot be represented as one envelope line."""


def b64encode(data):
    """Return *data* as a padded single-line base64 str.

    `b2a_base64` appends a newline and `b64encode` does not; both are stripped
    so the result cannot break the one-line envelope.
    """
    return _b2a(bytes(data)).decode("ascii").strip()


def b64decode(text):
    """Return the bytes carried by a base64 str (or bytes) payload."""
    if isinstance(text, str):
        text = text.encode("ascii")
    if not text:
        return b""
    return bytes(_a2b(text))


def one_line(text):
    """Collapse every whitespace run in *text* to one space.

    Used on exception messages, which may be multi-line, so a failure can never
    emit a second line that the host would parse as the envelope.
    """
    return " ".join(str(text).split())


def format_payload(value):
    """Encode *value* as the payload half of an ``OK`` line ("" for no payload).

    Raises:
        LinkError: a str payload contains a newline, or the type has no payload
            encoding.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return b64encode(value)
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            raise LinkError(
                "str payload must be one line, got %d" % len(value.splitlines())
            )
        return value
    if isinstance(value, JSON_TYPES):
        return json.dumps(value)
    raise LinkError("no payload encoding for %s" % type(value).__name__)


def format_ok(value=None):
    """Return the success line for *value* (``OK`` or ``OK <payload>``)."""
    payload = format_payload(value)
    if payload:
        return "%s %s" % (OK_PREFIX, payload)
    return OK_PREFIX


def format_err(error):
    """Return the failure line for *error* (``ERR <Type> [message]``)."""
    name = type(error).__name__
    message = one_line(error)
    if message:
        return "%s %s %s" % (ERR_PREFIX, name, message)
    return "%s %s" % (ERR_PREFIX, name)


def envelope(fn):
    """Wrap *fn* so it prints one result line and returns that same line.

    The wrapper never raises: any `Exception` from *fn*, including a payload
    that cannot be encoded, becomes ``ERR <Type> <message>``. The line is both
    printed, for the host reading stdout, and returned, so MCU-side callers and
    host-side unit tests can assert on it without capturing stdout.
    """

    def wrapper(*args, **kwargs):
        try:
            line = format_ok(fn(*args, **kwargs))
        except Exception as error:
            line = format_err(error)
        print(line)
        return line

    return wrapper
