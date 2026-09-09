"""Host side of the single-line envelope: transports, `Link`, and the loopback.

`firmware.link` defines the wire format and is imported here so there is one
definition of the prefixes and the base64 payload codec. This module adds the
host half: send one statement, read stdout, and turn the last line into a value
or a `RemoteError`.

Two transports implement the same tiny surface (`exec(code) -> stdout`,
`reset()`, optional `close()`):

* `SerialTransport` - real demoboard over `mpremote` on a USB CDC port.
* `LoopbackTransport` - no hardware at all. Runs the statement in an
  in-process namespace against the real `firmware.session` module, with
  `hil.fake_hw.FakeHardware` bound as the session's hardware factory, and
  captures stdout. That makes the full host -> envelope -> session -> board
  path testable under pytest, which is how the link and session are verified
  in this repo.

A transport failure outside the envelope (a traceback, a dropped port, no
output at all) is a `TransportError`, never a `RemoteError`: only an actual
``ERR <Type> <message>`` line means the remote call itself failed.
"""

import contextlib
import importlib
import io
import json
import subprocess
import time

from firmware.link import ERR_PREFIX, OK_PREFIX, b64decode, b64encode

# Statement sent once per connection (and again after a reset) so later calls
# can be written as `session.<entry point>(...)`.
PREAMBLE = "import firmware.session as session"

DEFAULT_TIMEOUT_S = 30
# Seconds to let the MCU reboot and re-read config.ini after `mpremote reset`.
DEFAULT_RESET_SETTLE_S = 1.0


class LinkError(Exception):
    """Base for host-side link failures."""


class TransportError(LinkError):
    """The remote runtime failed outside the envelope, or produced no line."""


class RemoteError(LinkError):
    """The remote call returned ``ERR <exc_type> <message>``."""

    def __init__(self, exc_type, message=""):
        text = exc_type if not message else "%s: %s" % (exc_type, message)
        LinkError.__init__(self, text)
        self.exc_type = exc_type
        self.message = message


def envelope_line(output):
    """Return the last non-blank line of *output*.

    Entry points print one line, but a debug helper called in the same
    statement may print before it, so the envelope is the last line.

    Raises:
        TransportError: *output* has no non-blank line.
    """
    for line in reversed(str(output).splitlines()):
        line = line.strip()
        if line:
            return line
    raise TransportError("no envelope line in remote output %r" % (output,))


def parse_payload(output):
    """Return the payload text of an ``OK`` line.

    Raises:
        RemoteError: the line is ``ERR <exc_type> [message]``.
        TransportError: the line is neither ``OK`` nor ``ERR``.
    """
    line = envelope_line(output)
    if line == OK_PREFIX:
        return ""
    if line.startswith(OK_PREFIX + " "):
        return line[len(OK_PREFIX) + 1 :]
    if line == ERR_PREFIX or line.startswith(ERR_PREFIX + " "):
        parts = line.split(" ", 2)
        exc_type = parts[1] if len(parts) > 1 else "RemoteError"
        message = parts[2] if len(parts) > 2 else ""
        raise RemoteError(exc_type, message)
    raise TransportError("unparsable envelope line %r" % (line,))


class SerialTransport:
    """`mpremote` against a real demoboard on a USB CDC port.

    Not used by the unit tests in this repo (there is no board in CI); it is
    the drop-in replacement for `LoopbackTransport` once hardware is attached.
    """

    def __init__(
        self,
        port=None,
        mpremote="mpremote",
        timeout_s=DEFAULT_TIMEOUT_S,
        settle_s=DEFAULT_RESET_SETTLE_S,
    ):
        self.port = port
        self.mpremote = mpremote
        self.timeout_s = timeout_s
        self.settle_s = settle_s

    def _run(self, args):
        command = [self.mpremote]
        if self.port:
            command += ["connect", self.port]
        command += args
        try:
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except OSError as error:
            raise TransportError("cannot run %s: %s" % (self.mpremote, error))
        except subprocess.TimeoutExpired:
            raise TransportError(
                "%s timed out after %s s" % (" ".join(command), self.timeout_s)
            )
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip()
            raise TransportError(
                "%s exited %d: %s" % (" ".join(command), done.returncode, detail)
            )
        return done.stdout

    def exec(self, code):
        """Run one statement on the MCU and return its stdout."""
        return self._run(["exec", code])

    def reset(self):
        """Soft-reboot the MCU, dropping all module state."""
        self._run(["reset"])
        if self.settle_s:
            time.sleep(self.settle_s)


class LoopbackTransport:
    """In-process stand-in for `mpremote exec` against `firmware.session`.

    *hardware* is the factory installed on `firmware.session`, so
    `session.init(...)` binds a fake board and fake QSPI transport instead of a
    real DemoBoard; it defaults to a fresh `FakeHardware`. `reset()` drops
    session module state the way an MCU soft reboot does, so each test starts
    uninitialized.

    Statements run in `namespace`, which starts empty: the `Link` preamble has
    to import the session module for itself, exactly as it does over a serial
    port.
    """

    def __init__(self, hardware=None):
        if hardware is None:
            from .fake_hw import FakeHardware

            hardware = FakeHardware()
        self.hardware = hardware
        self.session = importlib.import_module("firmware.session")
        self.history = []
        self.outputs = []
        self.namespace = {}
        self.reset()

    def reset(self):
        """Rebind the fake hardware, clear session state, drop the namespace."""
        self.session.set_hardware_factory(self.hardware)
        self.session._clear_state()
        self.namespace = {}

    def close(self):
        """Detach the fake hardware so a later real session is not hijacked."""
        self.session.set_hardware_factory(None)

    def exec(self, code):
        """Run *code* in the loopback namespace and return what it printed.

        Raises:
            TransportError: *code* raised. An enveloped entry point never does,
                so this is the loopback's version of an MCU traceback: a typo
                in the statement, or a call that is not an entry point.
        """
        self.history.append(code)
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                exec(code, self.namespace)
        except Exception as error:
            raise TransportError(
                "remote exec raised %s: %s" % (type(error).__name__, error)
            )
        output = buffer.getvalue()
        self.outputs.append(output)
        return output


class Link:
    """Envelope client over a pluggable transport.

    `exec` is the raw statement channel; `call` and its typed variants parse
    the envelope. The transport is opened lazily so constructing a `Link` does
    not touch hardware.
    """

    def __init__(self, transport, preamble=PREAMBLE):
        self.transport = transport
        self.preamble = preamble
        self.last_output = ""
        self._opened = False

    def open(self):
        """Send the preamble once, so `session.<name>(...)` resolves remotely."""
        if self._opened:
            return
        self._opened = True
        if self.preamble:
            self.transport.exec(self.preamble)

    def close(self):
        """Close the transport, if it has anything to close."""
        self._opened = False
        close = getattr(self.transport, "close", None)
        if close is not None:
            close()

    def reset(self):
        """Reset the MCU (or the loopback), then re-send the preamble."""
        self._opened = False
        self.transport.reset()
        self.open()

    def exec(self, code: str) -> str:
        """Run one remote statement and return its stdout."""
        self.open()
        self.last_output = self.transport.exec(code)
        return self.last_output

    def call(self, expr: str) -> str:
        """Run *expr* and return its ``OK`` payload text ("" when there is none).

        Raises:
            RemoteError: the call returned ``ERR <exc_type> <message>``.
        """
        return parse_payload(self.exec(expr))

    def call_none(self, expr: str) -> None:
        """Run *expr* and require a bare ``OK``."""
        payload = self.call(expr)
        if payload:
            raise TransportError("expected a bare OK from %r, got %r" % (expr, payload))

    def call_json(self, expr: str):
        """Run *expr* and decode its JSON payload."""
        payload = self.call(expr)
        try:
            return json.loads(payload)
        except ValueError as error:
            raise TransportError("payload %r is not JSON: %s" % (payload, error))

    def call_bytes(self, expr: str) -> bytes:
        """Run *expr* and decode its base64 payload."""
        return b64decode(self.call(expr))

    def call_text(self, expr: str) -> str:
        """Run *expr* and return its payload verbatim."""
        return self.call(expr)


def loopback_link(hardware=None):
    """Build a `Link` over a fresh `LoopbackTransport` (no hardware needed)."""
    return Link(LoopbackTransport(hardware=hardware))


__all__ = [
    "DEFAULT_RESET_SETTLE_S",
    "DEFAULT_TIMEOUT_S",
    "PREAMBLE",
    "Link",
    "LinkError",
    "LoopbackTransport",
    "RemoteError",
    "SerialTransport",
    "TransportError",
    "b64decode",
    "b64encode",
    "envelope_line",
    "loopback_link",
    "parse_payload",
]
