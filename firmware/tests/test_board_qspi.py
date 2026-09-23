"""board.qspi transports and helpers on CPython (rp2 absent; PIO is untested here)."""

import ast
from pathlib import Path

import pytest

from firmware.board.pins import (
    PIN_FLASH_CS,
    PIN_MISO,
    PIN_MOSI,
    PIN_RAM_A_CS,
    PIN_RAM_B_CS,
    PIN_SCK,
    PIN_SD2,
    PIN_SD3,
)
from firmware.board import qspi
from firmware.board.qspi import (
    PINDIRS_QPI_RX,
    PINDIRS_QPI_TX,
    PINDIRS_SPI,
    PIO_TRANSPORT_CLAIMS_PINS_IN_INIT,
    QPI_PIO_BITS,
    QPI_READ_PIO_INTENT,
    RELEASED_PINS,
    SPI_PIN_MODES,
    QspiError,
    drain_sm,
    make_board_transport,
    pack_qpi_byte,
    park_and_switch_sm,
    realign_qpi_nibbles,
    require_read_fits_fifo,
    require_write_fits_fifo,
    rp2,
    sleep_us,
    unpack_qpi_word,
    wait_at_least_us,
)
from firmware.constants import (
    MCU_QPI_PAYLOAD_MAX,
    MCU_QPI_READ_FRAME_MAX,
    MCU_QPI_WRITE_FRAME_MAX,
    MCU_QPI_WRITE_PAYLOAD_FIFO_MAX,
    QPI_DUMMY_CYCLES,
    QPI_READ_LAG_NIBBLES,
    QPI_WRITE_CMD_ADDR_BYTES,
    QPI_WRITE_TX_FIFO_WORDS,
    TPU_US,
)

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


def test_qpi_write_joins_tx_fifo_for_idle_prefill():
    src = QSPI_SOURCE.read_text(encoding="utf-8")
    write_prog, _, read_prog = src.partition("def qpi_read_cpha0")
    assert "fifo_join=rp2.PIO.JOIN_TX" in write_prog
    assert "fifo_join=rp2.PIO.JOIN_TX" not in read_prog
    assert "out(pins, 5).side(0)" in write_prog
    assert "out(pins, QPI_PIO_BITS)" not in write_prog
    assert "pull_thresh=QPI_PIO_BITS_PER_BYTE" in write_prog
    assert MCU_QPI_PAYLOAD_MAX + QPI_WRITE_CMD_ADDR_BYTES <= QPI_WRITE_TX_FIFO_WORDS
    assert QPI_WRITE_TX_FIFO_WORDS == 8
    assert QPI_WRITE_CMD_ADDR_BYTES == 4
    assert MCU_QPI_WRITE_FRAME_MAX == QPI_WRITE_TX_FIFO_WORDS
    assert MCU_QPI_WRITE_PAYLOAD_FIFO_MAX == 4


def test_require_write_fits_fifo_refuses_a_ninth_word():
    require_write_fits_fifo(bytes(MCU_QPI_WRITE_FRAME_MAX))
    with pytest.raises(QspiError, match="joined TX FIFO"):
        require_write_fits_fifo(bytes(MCU_QPI_WRITE_FRAME_MAX + 1))


def test_realign_recovers_the_frame_measured_on_hardware():
    # Captured on ETR after writing 12 34 56 78 to 0:0x001000. The leading 8
    # is the bus turnaround sample and every written nibble follows in order,
    # one position late, so the payload is recoverable by re-pairing.
    assert QPI_READ_LAG_NIBBLES == 1
    assert realign_qpi_nibbles(b"\x81\x23\x45\x67\x88", 4) == b"\x12\x34\x56\x78"


def test_realign_at_zero_lag_is_a_passthrough():
    assert realign_qpi_nibbles(b"\x12\x34\x56\x00", 3, lag=0) == b"\x12\x34\x56"


def test_realign_refuses_a_capture_without_the_spare_byte():
    # Without the trailing word the last byte's low nibble was never clocked.
    with pytest.raises(QspiError, match="realigning"):
        realign_qpi_nibbles(b"\x81\x23", 2)


def test_read_frame_words_count_dummy_and_the_realign_spare():
    assert require_read_fits_fifo(QPI_DUMMY_CYCLES, 0) == 3
    assert require_read_fits_fifo(QPI_DUMMY_CYCLES, MCU_QPI_READ_FRAME_MAX) == 8
    with pytest.raises(QspiError, match="stall mid-burst"):
        require_read_fits_fifo(QPI_DUMMY_CYCLES, MCU_QPI_READ_FRAME_MAX + 1)


def test_qpi_read_intent_is_rising_sck():
    # D16: the device launches read data after falling SCK (tACLK, read data
    # valid after falling SCK), so the RP2 samples the symbol on rising SCK.
    assert QPI_READ_PIO_INTENT == (("nop", 0), ("in_", 1, QPI_PIO_BITS))
    src = QSPI_SOURCE.read_text(encoding="utf-8")
    assert "in_(pins, 5).side(1)" in src
    assert "in_(pins, QPI_PIO_BITS)" not in src
    assert "push_thresh=QPI_PIO_BITS_PER_BYTE" in src
    assert "fifo_join=rp2.PIO.JOIN_RX" in src


def test_qpi_pio_range_includes_sck_but_side_set_owns_it():
    src = QSPI_SOURCE.read_text(encoding="utf-8")
    assert QPI_PIO_BITS == 5
    assert "sideset_base=Pin(PIN_SCK)" in src
    assert "out_base=Pin(PIN_MOSI)" in src
    assert "in_base=Pin(PIN_MOSI)" in src
    # set_base sizes the same GPIO26..30 window for set(pindirs, ...).
    assert src.count("set_base=Pin(PIN_MOSI)") == 2


def test_pindir_masks_cover_the_five_pin_window():
    # bit0 SIO0, bit1 SIO1, bit2 SCK, bit3 SIO2, bit4 SIO3.
    sck = 1 << 2
    sio = (1 << 0) | (1 << 1) | (1 << 3) | (1 << 4)
    assert PINDIRS_QPI_TX == sck | sio
    # QPI read floats every SIO so the PSRAM drives the data phase, and keeps
    # driving SCK because the MCU still clocks the burst.
    assert PINDIRS_QPI_RX == sck
    assert PINDIRS_QPI_RX & sio == 0
    # SPI drives MOSI only; MISO/SD2/SD3 stay inputs (HOLD#/WP# pulled up).
    assert PINDIRS_SPI == sck | (1 << 0)


def test_pio_instructions_are_assembled_once_and_then_cached(monkeypatch):
    # Isolated string exec is 8.6 ms on the ETR against 13.3 us for an already
    # encoded word; the write path must use the word form.
    calls = []

    class FakeRp2:
        @staticmethod
        def asm_pio_encode(instr, sideset_count):
            calls.append((instr, sideset_count))
            return 0xA042

    monkeypatch.setattr(qspi, "rp2", FakeRp2)
    monkeypatch.setattr(qspi, "_PIO_WORD_CACHE", {})
    assert qspi.pio_word("nop()") == 0xA042
    assert qspi.pio_word("nop()") == 0xA042
    assert qspi.pindirs_word(PINDIRS_QPI_RX) == 0xA042
    assert qspi.pindirs_word(PINDIRS_QPI_RX) == 0xA042
    assert calls == [
        ("nop()", 1),
        ("set(pindirs, %d)" % PINDIRS_QPI_RX, 1),
    ]
    # Every program here declares one side-set pin, which is also the count
    # StateMachine.exec would have used for the string form.
    assert qspi.PIO_SIDESET_COUNT == 1


def test_pio_word_without_rp2_is_refused_not_silently_skipped():
    with pytest.raises(QspiError, match="requires rp2"):
        qspi.pio_word("nop()")


def test_no_instruction_string_reaches_state_machine_exec():
    # A string makes StateMachine.exec re-run the PIO assembler on every call,
    # so only pio_word output may be exec'd. CPython cannot catch a regression
    # at runtime because every exec call site needs rp2.
    tree = ast.parse(QSPI_SOURCE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "exec"
    ]
    assert calls
    for call in calls:
        for arg in call.args:
            assert not (isinstance(arg, ast.Constant) and isinstance(arg.value, str))


def test_transactions_never_reinit_the_pio_owned_pins():
    # machine.Pin(n, ...) selects SIO and disconnects the PIO from that pad,
    # so a Pin() call inside a transfer silently stops the PIO driving it.
    src = QSPI_SOURCE.read_text(encoding="utf-8")
    _, _, transactions = src.partition("def spi_write")
    assert transactions
    for pin in ("PIN_MOSI", "PIN_MISO", "PIN_SCK", "PIN_SD2", "PIN_SD3"):
        assert "Pin(%s" % pin not in transactions
    assert "_set_pindirs(PINDIRS_QPI_TX)" in transactions
    assert "_set_pindirs(PINDIRS_QPI_RX)" in transactions


def test_release_pins_hands_every_claimed_pad_back_to_sio():
    # arm() claims all eight uio pads, so release_pins must give back all
    # eight. uio_oe_pico is not a fallback for the three CS pads: the frozen
    # ttboard build on the ETR preserves GPIO25 when clearing that register,
    # so flash CS survives every OE write and only machine.Pin(.., IN) frees it.
    assert RELEASED_PINS == (
        PIN_FLASH_CS,
        PIN_MOSI,
        PIN_MISO,
        PIN_SCK,
        PIN_SD2,
        PIN_SD3,
        PIN_RAM_A_CS,
        PIN_RAM_B_CS,
    )
    src = QSPI_SOURCE.read_text(encoding="utf-8")
    release = src.partition("def release_pins")[2].partition("def _park_sck")[0]
    assert release
    assert "for pin in RELEASED_PINS:" in release
    assert "Pin.IN" in release
    assert "self._armed = False" in release


def test_release_pins_restores_pads_even_when_it_did_not_arm_them():
    # A killed run leaves pads driven; the next session must still float them,
    # so only the state-machine teardown may sit behind the armed guard.
    src = QSPI_SOURCE.read_text(encoding="utf-8")
    release = src.partition("def release_pins")[2].partition("def _park_sck")[0]
    guarded = release.partition("if self._armed:")[2].partition("for pin in RELEASED_PINS:")[0]
    assert "sm.active(0)" in guarded
    assert "Pin(" not in guarded


def test_qpi_byte_packing_inserts_sck_placeholder():
    # 0xA5 nibbles: 1010 and 0101. Insert zero at bit 2 of each five-bit
    # symbol: 10010 and 01001.
    assert pack_qpi_byte(0xA5) == 0b10010_01001
    assert unpack_qpi_word(0b10010_01001) == 0xA5


def test_qpi_byte_unpacking_discards_sck_capture_bits():
    packed = pack_qpi_byte(0xA5)
    assert unpack_qpi_word(packed | (1 << 2) | (1 << 7)) == 0xA5


def test_qpi_byte_packing_round_trips_all_values():
    for value in range(256):
        assert unpack_qpi_word(pack_qpi_byte(value)) == value


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
