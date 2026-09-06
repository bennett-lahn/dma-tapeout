"""board.Board against a mock DemoBoard (no serial, no DMA protocol)."""

import pytest

from firmware.board.board import Board, BoardError
from firmware.board.pins import (
    OE_HIZ,
    OE_QPI,
    PROJECT_CLOCK_HZ,
    UI_IN_UNUSED_MASK,
)

from mock_board import PROJECT_NAME, MockDemoBoard


class IntPortBoard:
    """DemoBoard-shaped mock whose ports are plain ints and with no _in_reset."""

    def __init__(self):
        self.ui_in = 0
        self.uo_out = 0x01
        self.uio_oe_pico = 0
        self.mode = "ASIC_RP_CONTROL"
        self.clock_hz = None
        self.held = None

    def clock_project_PWM(self, freq):
        self.clock_hz = freq

    def reset_project(self, asserted):
        self.held = bool(asserted)


def _board(tt=None, **kwargs):
    if tt is None:
        tt = MockDemoBoard()
    return Board(tt, sleep_us=lambda us: None, **kwargs), tt


def test_enable_design_muxes_clocks_and_brackets_reset():
    board, tt = _board()
    board.enable_design(PROJECT_NAME)
    assert getattr(tt.shuttle, PROJECT_NAME).enabled is True
    assert tt.clock_hz == PROJECT_CLOCK_HZ
    assert int(tt.ui_in) == 0
    assert int(tt.uio_oe_pico) == OE_HIZ
    assert board.rst_n_low is False
    resets = [event[1] for event in tt.events if event[0] == "reset"]
    assert resets[0] is True
    assert resets[-1] is False


def test_enable_design_accepts_a_clock_override():
    board, tt = _board()
    board.enable_design(PROJECT_NAME, clock_hz=10_000_000)
    assert tt.clock_hz == 10_000_000


def test_enable_design_requires_a_project_name():
    board, _tt = _board()
    with pytest.raises(BoardError, match="project name"):
        board.enable_design("")


def test_enable_design_rejects_unknown_design():
    board, _tt = _board()
    with pytest.raises(BoardError, match="no design"):
        board.enable_design("tt_um_not_on_this_shuttle")


def test_enable_design_rejects_wrong_mux_mode():
    tt = MockDemoBoard()
    tt.mode = "SAFE"
    board, _tt = _board(tt)
    with pytest.raises(BoardError, match="ASIC_RP_CONTROL"):
        board.enable_design(PROJECT_NAME)


def test_rst_n_low_tracks_reset_project_without_in_reset_attr():
    tt = IntPortBoard()
    assert not hasattr(tt, "_in_reset")
    board = Board(tt, sleep_us=lambda us: None)
    assert board.rst_n_low is False
    board.reset_design(True)
    assert board.rst_n_low is True
    assert tt.held is True
    assert board.read_uio_oe() == OE_HIZ
    board.reset_design(False)
    assert board.rst_n_low is False
    assert tt.held is False


def test_reset_design_requires_the_sdk_api():
    class NoReset:
        ui_in = 0
        uo_out = 0
        uio_oe_pico = 0

    board = Board(NoReset(), sleep_us=lambda us: None)
    with pytest.raises(BoardError, match="reset_project"):
        board.reset_design(True)


def test_integer_ports_read_and_write_by_bit():
    tt = IntPortBoard()
    board = Board(tt, sleep_us=lambda us: None)
    board.write_ui_in_bit(2, 1)
    assert tt.ui_in == 0x04
    assert board.read_ui_in_bit(2) == 1
    board.write_ui_in_bit(2, 0)
    assert board.read_ui_in() == 0
    assert board.read_uo_out() == 0x01
    assert board.read_uo_out_bit(0) == 1
    board.write_uio_oe(OE_QPI)
    assert board.read_uio_oe() == OE_QPI
    board.hiz()
    assert board.read_uio_oe() == OE_HIZ


def test_zero_ui_in_clears_unused_bits():
    board, tt = _board()
    board.write_ui_in(0xFF)
    assert board.read_ui_in() & UI_IN_UNUSED_MASK == UI_IN_UNUSED_MASK
    board.zero_ui_in()
    assert int(tt.ui_in) == 0


def test_poll_until_returns_false_on_timeout_and_true_on_success():
    board, _tt = _board()
    assert board.poll_until(lambda: False, timeout_ms=0) is False
    assert board.poll_until(lambda: True, timeout_ms=0) is True

    ticks = []

    def ready():
        ticks.append(1)
        return len(ticks) >= 3

    assert board.poll_until(ready, timeout_ms=1000, poll_us=1) is True
    assert len(ticks) == 3
