"""asic.Host against a mock DemoBoard (no serial)."""

import pytest

from firmware.asic import OE_HIZ, OE_QPI, Host, HostError, PROJECT_CLOCK_HZ

from mock_board import MockDemoBoard


def _host(tt=None, **kwargs):
    if tt is None:
        tt = MockDemoBoard()
    return Host(tt, sleep_us=lambda us: None, **kwargs), tt


def test_enable_project_clock_and_unused_ui_in_zero():
    host, tt = _host()
    host.enable_project()
    assert tt.shuttle.tt_um_lahnb_sgdma.enabled is True
    assert tt.clock_hz == PROJECT_CLOCK_HZ
    assert int(tt.ui_in) == 0
    assert int(tt.uio_oe_pico) == OE_HIZ
    assert host.done is True
    assert host.bus_gnt is False
    resets = [ev for ev in tt.events if ev[0] == "reset"]
    assert (True,) in [(r[1],) for r in resets] or any(r[1] is True for r in resets)
    assert any(r[1] is False for r in resets)


def test_rst_n_low_does_not_use_in_reset_attr():
    class BoardNoInReset:
        def __init__(self):
            self.ui_in = 0
            self.uo_out = 0x01
            self.uio_oe_pico = 0
            self.shuttle = MockDemoBoard().shuttle
            self.mode = "ASIC_RP_CONTROL"
            self.clock_hz = None
            self._held = False

        def clock_project_PWM(self, freq):
            self.clock_hz = freq

        def reset_project(self, asserted):
            self._held = bool(asserted)
            if asserted:
                self.uo_out = 0x01

    tt = BoardNoInReset()
    assert not hasattr(tt, "_in_reset")
    host = Host(tt, sleep_us=lambda us: None)
    host.reset_asic(True)
    assert host.rst_n_low is True
    host.enable_drive()
    host.reset_asic(False)
    assert host.rst_n_low is False
    with pytest.raises(HostError, match="BUS_GNT"):
        host.enable_drive()


def test_timeout_clears_ui_in():
    tt = MockDemoBoard(auto_grant=False)
    host = Host(tt, sleep_us=lambda us: None)
    with pytest.raises(HostError, match="timeout"):
        host.request_bus(timeout_ms=0)
    assert int(tt.ui_in) == 0
    assert host.bus_req is False


def test_wrong_mode_is_rejected():
    tt = MockDemoBoard()
    tt.mode = "SAFE"
    host = Host(tt, sleep_us=lambda us: None)
    with pytest.raises(HostError, match="ASIC_RP_CONTROL"):
        host.enable_project()


def test_start_refused_while_req():
    host, tt = _host()
    host.request_bus()
    assert host.bus_req is True
    with pytest.raises(HostError, match="BUS_REQ"):
        host.pulse_start()


def test_req_refused_until_done_falls():
    host, tt = _host()
    host.pulse_start()
    assert tt.uo_out[0] == 1
    with pytest.raises(HostError, match="idle after START"):
        host.request_bus()
    tt.uo_out[0] = 0
    host.wait_busy()
    host.request_bus()
    assert host.bus_gnt is True


def test_oe_cleared_before_drop_req():
    host, tt = _host()
    host.request_bus()
    assert int(tt.uio_oe_pico) == OE_QPI
    host.release_bus()
    # Walk events: when REQ first becomes 0 after grant, OE must already be 0.
    saw_grant = False
    for event in tt.events:
        if event[0] == "oe":
            _kind, oe, _reqk, req = event
            if req == 1 and oe == OE_QPI:
                saw_grant = True
            if saw_grant and req == 0:
                assert oe == OE_HIZ
                break
        if event[0] == "ui":
            _kind, ui, _oek, oe = event
            req = (ui >> 2) & 1
            if saw_grant and req == 0:
                assert oe == OE_HIZ
                break
    else:
        raise AssertionError("never observed REQ drop")
    assert int(tt.uio_oe_pico) == 0
    assert host.bus_req is False
    assert host.bus_gnt is False


def test_drive_refused_without_grant_or_reset():
    host, tt = _host()
    with pytest.raises(HostError, match="BUS_GNT"):
        host.enable_drive()
    host.kill_dma()
    host.enable_drive()
    assert int(tt.uio_oe_pico) == OE_QPI


def test_missed_done_low_is_fast_completion_not_timeout():
    tt = MockDemoBoard(auto_ack_start=True, instant_complete=True)
    host, _ = _host(tt)
    host.pulse_start()
    assert host.done is True
    host.wait_idle_after_start()
    host.request_bus()
    assert host.bus_gnt is True
    assert not any(ev[0] == "reset" for ev in tt.events)


def test_wait_idle_after_observed_busy():
    tt = MockDemoBoard(auto_ack_start=True)
    host = Host(tt, sleep_us=lambda us: tt.poll_tick())
    host.pulse_start()
    assert host.done is False
    host.wait_idle_after_start()
    assert host.done is True
    host.request_bus()
    assert host.bus_gnt is True
