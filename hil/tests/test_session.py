"""Full host `Session` flow over the loopback: init, write, START, read, compare.

No demoboard and no serial port. `hil.fake_hw.FakeHardware` supplies a fake
DemoBoard, a fake QSPI PMOD over sparse device memory, and a chain engine that
really moves bytes on START, so a dump-and-compare after `start_and_wait` is a
meaningful round-trip rather than a read-back of what the host just wrote.
"""

import pytest

from firmware.constants import HEAD_ADDRESS, HEAD_DEVICE, TCD_BYTES
from firmware.tcd import Tcd, encode_tcd
from hil.fake_hw import FakeHardware
from hil.link import Link, LoopbackTransport, RemoteError
from hil.session import (
    DESIGN_NAME,
    Session,
    SessionError,
    TargetProfile,
    resolve_profile,
)

# Head is PSRAM0 0x000000 (D18); the terminator sits right after it.
QUIT_ADDRESS = HEAD_ADDRESS + TCD_BYTES
SECOND_ADDRESS = QUIT_ADDRESS
SRC_ADDRESS = 0x000100
DEST_ADDRESS = 0x000200
PAYLOAD = bytes(range(0x40, 0x48))


def quit_tcd():
    """The QUIT terminator: end of chain, no copy (address 0 is not a stop)."""
    return encode_tcd(Tcd(quit=True))


def copy_tcd(src_ptr, dest_ptr, length, next_tcd, src_device=0, dest_device=0):
    """One data TCD. `next_tcd` is always explicit so NEXT never defaults."""
    return encode_tcd(
        Tcd(
            src_ptr=src_ptr,
            dest_ptr=dest_ptr,
            transfer_len=length,
            next_tcd=next_tcd,
            src_device=src_device,
            dest_device=dest_device,
            next_device=HEAD_DEVICE,
        )
    )


def copy_chain(
    src_ptr=SRC_ADDRESS,
    dest_ptr=DEST_ADDRESS,
    length=len(PAYLOAD),
    src_device=0,
    dest_device=0,
):
    """Spans for a one-copy chain: head descriptor plus the QUIT terminator."""
    head = copy_tcd(
        src_ptr,
        dest_ptr,
        length,
        QUIT_ADDRESS,
        src_device=src_device,
        dest_device=dest_device,
    )
    return [
        (HEAD_DEVICE, HEAD_ADDRESS, head),
        (HEAD_DEVICE, QUIT_ADDRESS, quit_tcd()),
    ]


def expected_bytes(device, address, data):
    """Byte map in `Session.read_spans` shape: `{(device, address): byte}`."""
    return {
        (device, address + offset): value for offset, value in enumerate(bytes(data))
    }


def make_session(**kwargs):
    """Session over a fresh loopback; returns it with the fake hardware."""
    hardware = FakeHardware(**kwargs)
    return Session(Link(LoopbackTransport(hardware))), hardware


def brought_up(**kwargs):
    """Session with the design enabled and both PSRAMs in QPI."""
    session, hardware = make_session(**kwargs)
    session.init()
    session.bring_up_psram()
    return session, hardware


# --- bring-up ---


def test_init_enables_the_design_and_leaves_the_host_port_idle():
    session, hardware = make_session()
    result = session.init()
    assert result == {
        "design": DESIGN_NAME,
        "clock_hz": 66000000,
        "sck_hz": 20000000,
    }
    assert getattr(hardware.tt.shuttle, DESIGN_NAME).enabled is True
    assert hardware.tt.clock_hz == 66000000
    assert session.get_status() == {
        "done": True,
        "bus_gnt": False,
        "bus_req": False,
        "oe": 0,
        "rst_n_low": False,
        "in_qpi": False,
    }


def test_init_accepts_a_profile_name_or_a_profile_object():
    session, _hardware = make_session()
    assert session.init("fpga")["design"] == DESIGN_NAME
    assert session.profile.name == "fpga"
    profile = TargetProfile("bench", clock_hz=66000000, sck_hz=20000000)
    assert session.init(profile)["clock_hz"] == 66000000
    assert session.profile is profile


def test_resolve_profile_rejects_an_unknown_target():
    with pytest.raises(SessionError, match="unknown target profile"):
        resolve_profile("wishful")


def test_bring_up_psram_enters_qpi_once_then_reports_already_qpi():
    session, hardware = make_session()
    session.init()
    assert session.bring_up_psram() == "qpi"
    assert hardware.transport.qpi == {0: True, 1: True}
    assert session.bring_up_psram() == "already-qpi"
    assert session.get_status()["in_qpi"] is True


def test_bring_up_psram_forced_exits_qpi_before_the_spi_reset():
    session, hardware = brought_up()
    hardware.transport.log.clear()
    assert session.bring_up_psram(force=True) == "qpi"
    kinds = [entry[0] for entry in hardware.transport.log]
    assert kinds[0] == "qpi_write"
    assert "spi" in kinds
    assert hardware.transport.qpi == {0: True, 1: True}


# --- data movement ---


def test_write_spans_reports_the_total_byte_count():
    session, hardware = brought_up()
    spans = copy_chain() + [(0, SRC_ADDRESS, PAYLOAD)]
    assert session.write_spans(spans) == 2 * TCD_BYTES + len(PAYLOAD)
    assert hardware.fetch(0, SRC_ADDRESS, len(PAYLOAD)) == PAYLOAD


def test_read_spans_maps_every_address_it_read():
    session, _hardware = brought_up()
    session.write_spans([(1, 0x001000, PAYLOAD)])
    assert session.read_spans([(1, 0x001000, len(PAYLOAD))]) == expected_bytes(
        1, 0x001000, PAYLOAD
    )


def test_full_round_trip_same_device():
    session, hardware = brought_up()
    session.write_spans(copy_chain() + [(0, SRC_ADDRESS, PAYLOAD)])

    extents = [(0, DEST_ADDRESS, len(PAYLOAD))]
    assert session.read_spans(extents) == expected_bytes(
        0, DEST_ADDRESS, bytes(len(PAYLOAD))
    )

    session.start_and_wait()

    assert session.read_spans(extents) == expected_bytes(0, DEST_ADDRESS, PAYLOAD)
    assert hardware.tt.starts == 1
    assert hardware.tt.chain_nodes == [2]
    assert session.get_status()["rst_n_low"] is False


def test_full_round_trip_cross_device():
    session, _hardware = brought_up()
    session.write_spans(
        copy_chain(dest_device=1) + [(0, SRC_ADDRESS, PAYLOAD)]
    )
    session.start_and_wait()
    assert session.read_spans([(1, DEST_ADDRESS, len(PAYLOAD))]) == expected_bytes(
        1, DEST_ADDRESS, PAYLOAD
    )


def test_multi_tcd_chain_runs_every_descriptor():
    half = len(PAYLOAD) // 2
    third_address = SECOND_ADDRESS + TCD_BYTES
    spans = [
        (
            HEAD_DEVICE,
            HEAD_ADDRESS,
            copy_tcd(SRC_ADDRESS, DEST_ADDRESS, half, SECOND_ADDRESS),
        ),
        (
            HEAD_DEVICE,
            SECOND_ADDRESS,
            copy_tcd(
                SRC_ADDRESS + half,
                DEST_ADDRESS,
                half,
                third_address,
                dest_device=1,
            ),
        ),
        (HEAD_DEVICE, third_address, quit_tcd()),
        (0, SRC_ADDRESS, PAYLOAD),
    ]
    session, hardware = brought_up()
    session.write_spans(spans)
    session.start_and_wait()

    assert session.read_spans([(0, DEST_ADDRESS, half)]) == expected_bytes(
        0, DEST_ADDRESS, PAYLOAD[:half]
    )
    assert session.read_spans([(1, DEST_ADDRESS, half)]) == expected_bytes(
        1, DEST_ADDRESS, PAYLOAD[half:]
    )
    assert hardware.tt.chain_nodes == [3]


def test_quit_at_the_head_moves_nothing():
    session, hardware = brought_up()
    session.write_spans(
        [(HEAD_DEVICE, HEAD_ADDRESS, quit_tcd()), (0, SRC_ADDRESS, PAYLOAD)]
    )
    session.start_and_wait()
    assert hardware.tt.chain_nodes == [1]
    assert session.read_spans([(0, DEST_ADDRESS, len(PAYLOAD))]) == expected_bytes(
        0, DEST_ADDRESS, bytes(len(PAYLOAD))
    )


def test_chain_that_finishes_before_done_is_sampled_low_is_not_a_timeout():
    """A QUIT-only chain can finish in about 1.2 us; a missed DONE low is idle."""
    session, hardware = brought_up(instant_complete=True)
    session.write_spans(copy_chain() + [(0, SRC_ADDRESS, PAYLOAD)])
    session.start_and_wait()
    assert hardware.tt.starts == 1
    assert session.get_status()["rst_n_low"] is False
    assert session.read_spans([(0, DEST_ADDRESS, len(PAYLOAD))]) == expected_bytes(
        0, DEST_ADDRESS, PAYLOAD
    )


# --- recovery ---


def test_timeout_kills_the_transfer_then_reset_recovery_restores_the_session():
    session, hardware = brought_up(never_complete=True)
    session.write_spans(copy_chain() + [(0, SRC_ADDRESS, PAYLOAD)])

    with pytest.raises(RemoteError) as caught:
        session.start_and_wait(timeout_ms=0)
    assert caught.value.exc_type == "DmaError"

    status = session.get_status()
    assert status["rst_n_low"] is True
    assert status["oe"] == 0
    assert status["bus_req"] is False
    assert hardware.fetch(0, DEST_ADDRESS, len(PAYLOAD)) == bytes(len(PAYLOAD))

    hardware.tt.never_complete = False
    assert session.reset_recovery() == "qpi"
    assert session.get_status()["rst_n_low"] is False
    assert hardware.transport.qpi == {0: True, 1: True}

    session.start_and_wait()
    assert session.read_spans([(0, DEST_ADDRESS, len(PAYLOAD))]) == expected_bytes(
        0, DEST_ADDRESS, PAYLOAD
    )


# --- state and teardown ---


def test_every_call_before_init_reports_a_session_error():
    session, _hardware = make_session()
    for call in (
        session.bring_up_psram,
        session.get_status,
        session.start_and_wait,
        lambda: session.write_spans([(0, 0, b"\x01")]),
        lambda: session.read_spans([(0, 0, 1)]),
        session.reset_recovery,
    ):
        with pytest.raises(RemoteError) as caught:
            call()
        assert caught.value.exc_type == "SessionError"


def test_shutdown_exits_qpi_leaves_the_board_safe_and_drops_the_session():
    session, hardware = brought_up()
    session.shutdown(exit_qpi=True)
    assert hardware.transport.qpi == {0: False, 1: False}
    assert int(hardware.tt.uio_oe_pico) == 0
    assert int(hardware.tt.ui_in) == 0
    with pytest.raises(RemoteError, match="SessionError"):
        session.get_status()
