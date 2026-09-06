"""board.qspi transports and helpers on CPython (rp2 absent; PIO is untested here)."""

from pathlib import Path

import pytest

from firmware.board.qspi import (
    PIO_TRANSPORT_CLAIMS_PINS_IN_INIT,
    QPI_READ_PIO_INTENT,
    SPI_PIN_MODES,
    QspiError,
    drain_sm,
    make_board_transport,
    park_and_switch_sm,
    rp2,
    sleep_us,
    wait_at_least_us,
)
from firmware.constants import TPU_US

QSPI_SOURCE = Path(__file__).resolve().parents[1] / "board" / "qspi.py"


def test_cpython_import_has_no_rp2_and_board_transport_refuses():
    assert rp2 is None
    with pytest.raises(QspiError, match="rp2"):
        make_board_transport()


def test_sleep_us_runs_on_cpython():
    sleep_us(1)


def test_wait_at_least_us_uses_elapsed_time_not_one_short_sleep():
    calls = []

    def short_sleep(us):
        calls.append(us)

    wait_at_least_us(TPU_US, sleep=short_sleep)
    assert calls
    assert calls[0] == TPU_US


def test_wait_at_least_us_ignores_non_positive():
    calls = []
    wait_at_least_us(0, sleep=calls.append)
    assert calls == []


def test_qpi_read_intent_is_rising_sck():
    # D16: the device launches read data after falling SCK (tACLK, read data
    # valid after falling SCK), so the RP2 samples the nibble on rising SCK.
    assert QPI_READ_PIO_INTENT == (("nop", 0), ("in_", 1, 4))
    src = QSPI_SOURCE.read_text(encoding="utf-8")
    assert "in_(pins, 4).side(1)" in src


def test_pio_transport_does_not_claim_pins_before_grant():
    # D26 bus keeper: while rst_n=1 and BUS_GNT=0 the ASIC owns the QSPI pins.
    src = QSPI_SOURCE.read_text(encoding="utf-8")
    assert PIO_TRANSPORT_CLAIMS_PINS_IN_INIT is False
    assert "self.flash_cs = Pin(PIN_FLASH_CS, Pin.OUT)" not in src.split("def arm")[0]


def test_spi_pin_modes_float_hold_and_wp():
    assert SPI_PIN_MODES["MOSI"] == "OUT"
    assert SPI_PIN_MODES["MISO"] == "IN"
    assert SPI_PIN_MODES["SD2"] == "IN_PULLUP"
    assert SPI_PIN_MODES["SD3"] == "IN_PULLUP"


class _FakeSM:
    def __init__(self):
        self.active_flag = 0
        self.drained = False

    def wait_idle(self):
        self.drained = True

    def active(self, value):
        if value and self.active_flag:
            raise AssertionError("overlapping active(1)")
        if value == 0 and not self.drained:
            raise QspiError("state machine deactivated before drain")
        self.active_flag = value
        if value == 0:
            self.drained = False


def test_park_and_switch_drains_before_activate():
    old = _FakeSM()
    new = _FakeSM()
    old.active_flag = 1
    parked = []
    park_and_switch_sm(old, new, park_sck=lambda: parked.append(1))
    assert old.active_flag == 0
    assert new.active_flag == 1
    assert parked == [1]
    drain_sm(new)
    new.active(0)


def test_drain_sm_refuses_an_undrained_state_machine():
    class _NoWaitIdle:
        drained = False

    with pytest.raises(QspiError, match="before drain"):
        drain_sm(_NoWaitIdle())
