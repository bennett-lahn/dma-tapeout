"""Envelope format, base64 payloads, error reporting, and the loopback transport."""

import pytest

import hil.link
from firmware.board.pins import BUS_REQ_BIT, OE_QPI, UI_IN_UNUSED_MASK
from firmware.link import (
    ERR_PREFIX,
    OK_PREFIX,
    LinkError,
    b64decode,
    b64encode,
    envelope,
    format_err,
    format_ok,
    one_line,
)
from hil.fake_hw import FakeDemoBoard, FakeHardware, FakeHardwareError
from hil.link import (
    PREAMBLE,
    Link,
    LoopbackTransport,
    RemoteError,
    SerialTransport,
    TransportError,
    envelope_line,
    loopback_link,
    parse_payload,
    wait_for_mpremote_port,
)

ALL_BYTES = bytes(range(256))


class StubTransport:
    """Records statements and replays canned stdout; no session, no hardware."""

    def __init__(self, replies=None):
        self.codes = []
        self.replies = dict(replies or {})
        self.resets = 0
        self.closes = 0

    def exec(self, code):
        self.codes.append(code)
        return self.replies.get(code, "OK\n")

    def reset(self):
        self.resets += 1

    def close(self):
        self.closes += 1


# --- base64 payload codec ---


def test_selftest_b64_round_trip_covers_every_byte_value():
    payload = b64encode(ALL_BYTES)
    assert "\n" not in payload and "\r" not in payload
    assert b64decode(payload) == ALL_BYTES


def test_selftest_b64_round_trip_of_empty_and_unpadded_lengths():
    for data in (b"", b"a", b"ab", b"abc", b"abcd"):
        assert b64decode(b64encode(data)) == data


def test_selftest_b64decode_accepts_bytes_as_well_as_str():
    assert b64decode(b64encode(b"span").encode("ascii")) == b"span"


# --- OK / ERR line formatting ---


def test_selftest_format_ok_encodes_by_return_type():
    assert format_ok(None) == "OK"
    assert format_ok(b"\x00\xff") == "OK " + b64encode(b"\x00\xff")
    assert format_ok("already-qpi") == "OK already-qpi"
    assert format_ok(7) == "OK 7"
    assert format_ok(True) == "OK true"
    assert format_ok([1, 2]) == "OK [1, 2]"
    assert format_ok({"done": True}) == 'OK {"done": true}'


def test_selftest_format_ok_refuses_a_multiline_str_payload():
    with pytest.raises(LinkError, match="one line"):
        format_ok("first\nsecond")


def test_selftest_format_ok_refuses_a_type_with_no_payload_encoding():
    with pytest.raises(LinkError, match="no payload encoding for object"):
        format_ok(object())


def test_selftest_format_err_names_the_exception_type_and_message():
    assert format_err(ValueError("bad span")) == "ERR ValueError bad span"


def test_selftest_format_err_omits_an_empty_message():
    assert format_err(RuntimeError()) == "ERR RuntimeError"


def test_selftest_format_err_collapses_a_multiline_message_to_one_line():
    line = format_err(ValueError("first\nsecond\tthird"))
    assert line == "ERR ValueError first second third"
    assert len(line.splitlines()) == 1


def test_selftest_one_line_collapses_every_whitespace_run():
    assert one_line("a\n\n b\t c ") == "a b c"


# --- envelope wrapper ---


def test_selftest_envelope_prints_exactly_one_line_and_returns_it(capsys):
    wrapped = envelope(lambda: {"oe": 0})
    line = wrapped()
    printed = capsys.readouterr().out
    assert line == 'OK {"oe": 0}'
    assert printed == line + "\n"


def test_selftest_envelope_passes_arguments_through():
    wrapped = envelope(lambda device, addr=0: bytes([device, addr]))
    assert wrapped(1, addr=2) == "OK " + b64encode(b"\x01\x02")


def test_selftest_envelope_reports_an_exception_instead_of_raising(capsys):
    def boom():
        raise KeyError("no such device")

    line = envelope(boom)()
    assert line == "ERR KeyError 'no such device'"
    assert capsys.readouterr().out == line + "\n"


def test_selftest_envelope_reports_an_unencodable_payload_as_a_link_error():
    line = envelope(lambda: object())()
    assert line.startswith(ERR_PREFIX + " LinkError")


# --- host-side parsing ---


def test_selftest_parse_payload_reads_ok_with_and_without_a_payload():
    assert parse_payload("OK\n") == ""
    assert parse_payload("OK already-qpi\n") == "already-qpi"
    assert parse_payload('OK {"done": true}\n') == '{"done": true}'


def test_selftest_parse_payload_raises_remote_error_with_type_and_message():
    with pytest.raises(RemoteError) as caught:
        parse_payload("ERR DmaError timeout waiting for DONE high\n")
    assert caught.value.exc_type == "DmaError"
    assert caught.value.message == "timeout waiting for DONE high"


def test_selftest_parse_payload_handles_err_with_no_message():
    with pytest.raises(RemoteError) as caught:
        parse_payload("ERR SessionError")
    assert caught.value.exc_type == "SessionError"
    assert caught.value.message == ""


def test_selftest_parse_payload_rejects_a_line_that_is_not_an_envelope():
    with pytest.raises(TransportError, match="unparsable"):
        parse_payload("OKAY fine\n")


def test_selftest_parse_payload_rejects_output_with_no_line():
    with pytest.raises(TransportError, match="no envelope line"):
        parse_payload("\n  \n")


def test_selftest_envelope_line_takes_the_last_printed_line():
    assert envelope_line("dev0 0x000000  01 02\nOK\n") == OK_PREFIX


# --- Link over a stub transport ---


def test_selftest_link_sends_the_preamble_once_then_the_statements():
    transport = StubTransport()
    link = Link(transport)
    link.call("session.pulse_start()")
    link.call("session.pulse_start()")
    assert transport.codes == [
        PREAMBLE,
        "session.pulse_start()",
        "session.pulse_start()",
    ]


def test_selftest_link_reset_reboots_the_remote_and_resends_the_preamble():
    transport = StubTransport()
    link = Link(transport)
    link.call("session.status()")
    link.reset()
    link.call("session.status()")
    assert transport.resets == 1
    assert transport.codes.count(PREAMBLE) == 2


def test_selftest_link_typed_calls_decode_json_bytes_and_bare_ok():
    transport = StubTransport(
        {
            "json": 'OK {"bus_gnt": false, "oe": 0}\n',
            "bytes": "OK " + b64encode(ALL_BYTES) + "\n",
            "none": "OK\n",
            "text": "OK qpi\n",
        }
    )
    link = Link(transport)
    assert link.call_json("json") == {"bus_gnt": False, "oe": 0}
    assert link.call_bytes("bytes") == ALL_BYTES
    assert link.call_none("none") is None
    assert link.call_text("text") == "qpi"


def test_selftest_link_call_none_rejects_an_unexpected_payload():
    link = Link(StubTransport({"noisy": "OK 1\n"}))
    with pytest.raises(TransportError, match="bare OK"):
        link.call_none("noisy")


def test_selftest_link_call_json_rejects_a_non_json_payload():
    link = Link(StubTransport({"text": "OK qpi\n"}))
    with pytest.raises(TransportError, match="not JSON"):
        link.call_json("text")


def test_selftest_serial_transport_builds_the_mpremote_command(monkeypatch):
    """The real-hardware path, checked without a board: only the argv is built."""
    calls = []

    class Done:
        returncode = 0
        stdout = "OK qpi\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Done()

    monkeypatch.setattr(hil.link.subprocess, "run", fake_run)
    transport = SerialTransport(port="/dev/ttyACM0", settle_s=0, port_wait_s=0)
    assert transport.exec("session.status()") == "OK qpi\n"
    transport.reset()
    assert calls == [
        # `resume` disables mpremote's default auto-soft-reset, which would
        # otherwise wipe the board's Python heap (and any prior import) at
        # the start of this connection, before `exec` even runs.
        ["mpremote", "connect", "/dev/ttyACM0", "resume", "exec", "session.status()"],
        ["mpremote", "connect", "/dev/ttyACM0", "reset"],
    ]


def test_selftest_serial_transport_reports_a_missing_mpremote_binary():
    transport = SerialTransport(mpremote="mpremote-not-installed-here")
    with pytest.raises(TransportError, match="cannot run"):
        transport.exec("session.status()")


def test_selftest_serial_transport_reports_a_nonzero_exit(monkeypatch):
    class Done:
        returncode = 1
        stdout = ""
        stderr = "could not enter raw repl"

    monkeypatch.setattr(hil.link.subprocess, "run", lambda command, **kw: Done())
    with pytest.raises(TransportError, match="could not enter raw repl"):
        SerialTransport(port_wait_s=0).exec("session.status()")


def test_selftest_serial_transport_retries_until_the_port_reappears(monkeypatch):
    calls = []

    class Done:
        def __init__(self, code, err="", out=""):
            self.returncode = code
            self.stdout = out
            self.stderr = err

    def fake_run(command, **kwargs):
        calls.append(command)
        if len(calls) < 3:
            return Done(
                1,
                err="mpremote: failed to access COM10 (it may be in use by another program)",
            )
        return Done(0, out="OK\n")

    monkeypatch.setattr(hil.link.subprocess, "run", fake_run)
    monkeypatch.setattr(hil.link.time, "sleep", lambda s: None)
    transport = SerialTransport(port="COM10", settle_s=0, port_wait_s=5)
    assert transport.exec("import firmware.session as session") == "OK\n"
    assert len(calls) == 3


def test_selftest_wait_for_mpremote_port_times_out(monkeypatch):
    monkeypatch.setattr(hil.link.time, "sleep", lambda s: None)

    def fake_run(command, **kwargs):
        return type(
            "Done",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": "mpremote: failed to access COM10",
            },
        )()

    with pytest.raises(TransportError, match="not ready"):
        wait_for_mpremote_port(
            "COM10", timeout_s=0.01, interval_s=0, run=fake_run
        )


def test_selftest_link_close_closes_the_transport():
    transport = StubTransport()
    link = Link(transport)
    link.exec("session.status()")
    link.close()
    assert transport.closes == 1


# --- loopback transport against firmware.session ---


def test_selftest_loopback_runs_session_entry_points_and_captures_stdout():
    link = loopback_link()
    result = link.call_json("session.init('tt_um_lahnb_sgdma')")
    assert result["design"] == "tt_um_lahnb_sgdma"
    assert result["clock_hz"] == 66000000
    assert link.call_json("session.status()")["done"] is True


def test_selftest_loopback_reports_a_session_exception_as_a_remote_error():
    link = loopback_link()
    with pytest.raises(RemoteError) as caught:
        link.call("session.status()")
    assert caught.value.exc_type == "SessionError"
    assert "init" in caught.value.message


def test_selftest_loopback_raises_transport_error_when_the_statement_itself_fails():
    link = loopback_link()
    with pytest.raises(TransportError, match="AttributeError"):
        link.call("session.not_an_entry_point()")


def test_selftest_loopback_reset_drops_session_state_like_a_soft_reboot():
    transport = LoopbackTransport(FakeHardware())
    link = Link(transport)
    link.call_json("session.init('tt_um_lahnb_sgdma')")
    link.reset()
    with pytest.raises(RemoteError, match="SessionError"):
        link.call("session.status()")
    assert transport.history.count(PREAMBLE) == 2


def test_selftest_loopback_close_detaches_the_fake_hardware_factory():
    transport = LoopbackTransport(FakeHardware())
    assert transport.session.HARDWARE_FACTORY is transport.hardware
    Link(transport).close()
    assert transport.session.HARDWARE_FACTORY is None


def test_selftest_loopback_rejects_an_unknown_design_name():
    link = loopback_link()
    with pytest.raises(RemoteError) as caught:
        link.call("session.init('tt_um_not_this_project')")
    assert caught.value.exc_type == "BoardError"


# --- base64 data transfer end to end through the loopback ---


def test_selftest_loopback_carries_span_bytes_as_base64_both_ways():
    hardware = FakeHardware()
    link = loopback_link(hardware)
    link.call_json("session.init('tt_um_lahnb_sgdma')")
    link.call_text("session.bring_up_psram()")

    written = link.call_json(
        "session.write_span(1, 4096, '%s')" % b64encode(ALL_BYTES)
    )
    assert written == {"device": 1, "addr": 4096, "length": 256}
    assert hardware.fetch(1, 4096, 256) == ALL_BYTES
    assert link.call_bytes("session.read_span(1, 4096, 256)") == ALL_BYTES


def test_selftest_loopback_reads_back_an_empty_span_as_a_bare_ok():
    link = loopback_link()
    link.call_json("session.init('tt_um_lahnb_sgdma')")
    link.call_text("session.bring_up_psram()")
    assert link.exec("session.read_span(0, 0, 0)").strip() == OK_PREFIX
    assert link.call_bytes("session.read_span(0, 0, 0)") == b""


def test_selftest_loopback_rejects_an_unknown_device_index():
    link = loopback_link()
    link.call_json("session.init('tt_um_lahnb_sgdma')")
    with pytest.raises(RemoteError) as caught:
        link.call("session.read_span(2, 0, 1)")
    assert caught.value.exc_type == "SessionError"


# --- the fake board polices the frozen pin rules ---


def test_selftest_fake_board_refuses_mcu_drive_without_grant_or_reset():
    """D26 bus keeper: Hi-Z is the only legal OE while rst_n=1 and BUS_GNT=0."""
    with pytest.raises(FakeHardwareError, match="D26"):
        FakeDemoBoard().uio_oe_pico.value = OE_QPI

    while_reset = FakeDemoBoard()
    while_reset.reset_project(True)
    while_reset.uio_oe_pico.value = OE_QPI
    assert int(while_reset.uio_oe_pico) == OE_QPI

    while_granted = FakeDemoBoard()
    while_granted.ui_in[BUS_REQ_BIT] = 1
    assert while_granted.bus_gnt is True
    while_granted.uio_oe_pico.value = OE_QPI
    assert int(while_granted.uio_oe_pico) == OE_QPI


def test_selftest_fake_board_refuses_a_driven_unused_ui_in_bit():
    """D34: the ASIC does not use ui_in[1] or ui_in[7:3]; firmware keeps them 0."""
    board = FakeDemoBoard()
    with pytest.raises(FakeHardwareError, match="D34"):
        board.ui_in.value = UI_IN_UNUSED_MASK


def test_selftest_session_calls_leave_the_mcu_hi_z_and_ui_in_zero():
    hardware = FakeHardware()
    link = loopback_link(hardware)
    link.call_json("session.init('tt_um_lahnb_sgdma')")
    link.call_text("session.bring_up_psram()")
    link.call_json("session.write_span(0, 0, '%s')" % b64encode(b"\xa5"))
    link.call_bytes("session.read_span(0, 0, 1)")
    assert int(hardware.tt.uio_oe_pico) == 0
    assert int(hardware.tt.ui_in) == 0
    status = link.call_json("session.status()")
    assert status["oe"] == 0
    assert status["bus_req"] is False
    assert status["bus_gnt"] is False
