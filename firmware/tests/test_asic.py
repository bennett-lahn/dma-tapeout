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
    with pytest.raises(HostError, match="DONE falls"):
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
