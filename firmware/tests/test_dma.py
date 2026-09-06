"""dma.DmaController grant / START / DONE / kill against a mock DemoBoard."""

import pytest

from firmware.board.board import Board
from firmware.board.pins import (
    OE_HIZ,
    OE_QPI,
    OE_QPI_READ,
    OE_SPI,
    PROJECT_CLOCK_HZ,
    SIO_OE_MASK,
)
from firmware.dma import DmaController, DmaError

from mock_board import PROJECT_NAME, MockDemoBoard


def _dma(tt=None, sleep_us=None):
    if tt is None:
        tt = MockDemoBoard()
    board = Board(tt, sleep_us=sleep_us or (lambda us: None))
    return DmaController(board), tt


def test_enable_design_samples_idle_host_port():
    dma, tt = _dma()
    dma.enable_design(PROJECT_NAME)
    assert getattr(tt.shuttle, PROJECT_NAME).enabled is True
    assert tt.clock_hz == PROJECT_CLOCK_HZ
    assert dma.done is True
    assert dma.bus_gnt is False
    assert dma.oe == OE_HIZ
    assert int(tt.ui_in) == 0


def test_enable_design_rejects_done_low_after_reset_release():
    tt = MockDemoBoard()
    dma, _tt = _dma(tt)

    original = tt.reset_project

    def reset_and_stick_busy(asserted):
        original(asserted)
        if not asserted:
            tt.uo_out[0] = 0

    tt.reset_project = reset_and_stick_busy
    with pytest.raises(DmaError, match="DONE=1 expected"):
        dma.enable_design(PROJECT_NAME)


def test_drive_refused_without_grant_or_reset():
    dma, tt = _dma()
    with pytest.raises(DmaError, match="BUS_GNT"):
        dma.enable_drive()
    assert dma.oe == OE_HIZ
    dma.kill_dma()
    assert dma.rst_n_low is True
    dma.enable_drive()
    assert int(tt.uio_oe_pico) == OE_QPI


def test_oe_masks_per_phase():
    dma, _tt = _dma()
    dma.request_bus()
    dma.spi_oe()
    assert dma.oe == OE_SPI
    dma.qpi_write_oe()
    assert dma.oe == OE_QPI
    dma.qpi_read_oe()
    assert dma.oe == OE_QPI_READ
    assert dma.oe & SIO_OE_MASK == 0


def test_timeout_kills_and_clears_ui_in():
    tt = MockDemoBoard(auto_grant=False)
    dma, _tt = _dma(tt)
    with pytest.raises(DmaError, match="timeout"):
        dma.request_bus(timeout_ms=0)
    assert int(tt.ui_in) == 0
    assert dma.bus_req is False
    assert dma.oe == OE_HIZ
    assert dma.rst_n_low is True


def test_start_refused_while_req():
    dma, _tt = _dma()
    dma.request_bus()
    assert dma.bus_req is True
    with pytest.raises(DmaError, match="BUS_REQ"):
        dma.pulse_start()


def test_start_requires_done_high():
    tt = MockDemoBoard()
    dma, _tt = _dma(tt)
    tt.uo_out[0] = 0
    with pytest.raises(DmaError, match="DONE=1"):
        dma.pulse_start()


def test_req_refused_until_done_falls():
    dma, tt = _dma()
    dma.pulse_start()
    assert tt.uo_out[0] == 1
    with pytest.raises(DmaError, match="idle after START"):
        dma.request_bus()
    tt.uo_out[0] = 0
    dma.wait_busy()
    dma.request_bus()
    assert dma.bus_gnt is True


def test_oe_cleared_before_drop_req():
    dma, tt = _dma()
    dma.request_bus()
    assert int(tt.uio_oe_pico) == OE_QPI
    dma.release_bus()
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
    assert dma.bus_req is False
    assert dma.bus_gnt is False


def test_missed_done_low_is_fast_completion_not_timeout():
    tt = MockDemoBoard(auto_ack_start=True, instant_complete=True)
    dma, _tt = _dma(tt)
    dma.pulse_start()
    assert dma.done is True
    dma.wait_idle_after_start()
    dma.request_bus()
    assert dma.bus_gnt is True
    assert not any(event[0] == "reset" for event in tt.events)


def test_wait_idle_after_observed_busy():
    tt = MockDemoBoard(auto_ack_start=True)
    dma, _tt = _dma(tt, sleep_us=lambda us: tt.poll_tick())
    dma.pulse_start()
    assert dma.done is False
    dma.wait_idle_after_start()
    assert dma.done is True
    dma.request_bus()
    assert dma.bus_gnt is True


def test_wait_done_timeout_kills_the_transfer():
    tt = MockDemoBoard(auto_ack_start=True)
    dma, _tt = _dma(tt)
    dma.pulse_start()
    assert dma.wait_busy() is True
    with pytest.raises(DmaError, match="DONE high"):
        dma.wait_done(timeout_ms=0)
    assert int(tt.ui_in) == 0
    assert dma.rst_n_low is True


def test_kill_dma_hi_zs_before_asserting_reset():
    dma, tt = _dma()
    dma.request_bus()
    assert dma.oe == OE_QPI
    dma.kill_dma()
    assert int(tt.uio_oe_pico) == OE_HIZ
    assert int(tt.ui_in) == 0
    assert dma.rst_n_low is True
    reset_at = [event[0] for event in tt.events].index("reset")
    before_reset = tt.events[:reset_at]
    assert any(ev[0] == "oe" and ev[1] == OE_HIZ for ev in before_reset)
    assert any(ev[0] == "ui" and ev[1] == 0 for ev in before_reset)
