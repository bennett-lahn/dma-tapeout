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
    HARD_RESET_CODE,
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


class FakeMpSerial:
    """Stand-in for `mpremote.transport_serial.SerialTransport` (no board)."""

    def __init__(self, factory, port):
        self.factory = factory
        self.port = port
        self.in_raw_repl = False
        self.calls = []

    def enter_raw_repl(self, soft_reset=True, timeout_overall=10):
        self.calls.append(("enter_raw_repl", soft_reset))
        error = self.factory.enter_error
        if error is not None:
            self.factory.enter_error = None
            raise error
        self.in_raw_repl = True

    def exec_raw(self, command, timeout=10, data_consumer=None):
        self.calls.append(("exec_raw", command, timeout))
        error = self.factory.exec_error
        if error is not None:
            self.factory.exec_error = None
            raise error
        return self.factory.replies.get(command, (b"OK\n", b""))

    def exec_raw_no_follow(self, command):
        self.calls.append(("exec_raw_no_follow", command))

    def exit_raw_repl(self):
        self.calls.append(("exit_raw_repl",))
        self.in_raw_repl = False

    def close(self):
        self.calls.append(("close",))


class FakeMpFactory:
    """Injectable `connect=` for `SerialTransport` unit tests."""

    def __init__(self):
        self.instances = []
        self.fail_connects = 0
        self.connect_error = OSError(
            "failed to access COM10 (it may be in use by another program)"
        )
        self.enter_error = None
        self.exec_error = None
        self.replies = {}

    def __call__(self, port):
        if self.fail_connects > 0:
            self.fail_connects -= 1
            raise self.connect_error
        instance = FakeMpSerial(self, port)
        self.instances.append(instance)
        return instance


def _serial(factory, **kwargs):
    kwargs.setdefault("settle_s", 0)
    kwargs.setdefault("port_wait_s", 0)
    kwargs.setdefault("port", "COM10")
    return SerialTransport(connect=factory, **kwargs)


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


def test_selftest_serial_transport_opens_one_raw_repl_for_every_exec():
    """The whole point of the persistent REPL: one connect, N statements."""
    factory = FakeMpFactory()
    factory.replies["session.status()"] = (b"OK qpi\n", b"")
    transport = _serial(factory)
    assert transport.exec("session.status()") == "OK qpi\n"
    assert transport.exec("session.status()") == "OK qpi\n"
    assert len(factory.instances) == 1
    repl = factory.instances[0]
    assert repl.calls == [
        ("enter_raw_repl", False),
        ("exec_raw", "session.status()", transport.timeout_s),
        ("exec_raw", "session.status()", transport.timeout_s),
    ]


def test_selftest_serial_transport_enters_raw_repl_without_soft_reset():
    """Library equivalent of CLI `resume`: MCU heap (and the preamble) survives."""
    factory = FakeMpFactory()
    transport = _serial(factory)
    transport.exec("session.status()")
    enters = [
        call for call in factory.instances[0].calls if call[0] == "enter_raw_repl"
    ]
    assert enters == [("enter_raw_repl", False)]


def test_selftest_serial_transport_reports_a_device_traceback_as_transport_error():
    factory = FakeMpFactory()
    factory.replies["session.status()"] = (
        b"",
        b"Traceback (most recent call last):\r\nNameError: name 'session' isn't defined\r\n",
    )
    transport = _serial(factory)
    with pytest.raises(TransportError, match="NameError"):
        transport.exec("session.status()")


def test_selftest_serial_transport_resyncs_after_a_failed_exec_without_retrying_it():
    factory = FakeMpFactory()
    factory.exec_error = RuntimeError("timeout waiting for first EOF reception")
    factory.replies["session.pulse_start()"] = (b"OK\n", b"")
    transport = _serial(factory)
    with pytest.raises(TransportError, match="timeout waiting for first EOF"):
        transport.exec("session.pulse_start()")
    assert transport.exec("session.pulse_start()") == "OK\n"
    assert len(factory.instances) == 1
    repl = factory.instances[0]
    assert repl.calls == [
        ("enter_raw_repl", False),
        ("exec_raw", "session.pulse_start()", transport.timeout_s),
        ("enter_raw_repl", False),
        ("exec_raw", "session.pulse_start()", transport.timeout_s),
    ]


def test_selftest_serial_transport_retries_until_the_port_reappears(monkeypatch):
    monkeypatch.setattr(hil.link.time, "sleep", lambda s: None)
    factory = FakeMpFactory()
    factory.fail_connects = 2
    transport = _serial(factory, port_wait_s=5)
    assert transport.exec("import firmware.session as session") == "OK\n"
    assert factory.fail_connects == 0
    assert len(factory.instances) == 1


def test_selftest_serial_transport_close_exits_raw_repl_then_closes():
    factory = FakeMpFactory()
    transport = _serial(factory)
    transport.exec("session.status()")
    transport.close()
    assert factory.instances[0].calls == [
        ("enter_raw_repl", False),
        ("exec_raw", "session.status()", transport.timeout_s),
        ("exit_raw_repl",),
        ("close",),
    ]


def test_selftest_serial_transport_reset_hard_resets_and_reconnects():
    factory = FakeMpFactory()
    transport = _serial(factory)
    transport.exec("session.status()")
    transport.reset()
    assert len(factory.instances) == 2
    assert factory.instances[0].calls == [
        ("enter_raw_repl", False),
        ("exec_raw", "session.status()", transport.timeout_s),
        ("exec_raw_no_follow", HARD_RESET_CODE),
        ("close",),
    ]
    assert factory.instances[1].calls == [("enter_raw_repl", False)]
    assert factory.instances[0].in_raw_repl is True
    assert factory.instances[1].in_raw_repl is True


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


def test_selftest_link_close_leaves_a_borrowed_transport_open():
    transport = StubTransport()
    link = Link(transport, owns_transport=False)
    link.exec("session.status()")
    link.close()
    assert transport.closes == 0


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
