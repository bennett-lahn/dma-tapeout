"""End-to-end directed HIL cases against the selected `--target`.

Every test here takes the `session` fixture, so every one of them is a
hardware/DUT test: it drives whatever `--target` was chosen (loopback fake,
FPGA, or ASIC) and is auto-tagged `hw` by `hil/conftest.py`. Names carry a
`test_hw_` prefix for the same reason, so this is never mistaken for a
host-only self-test.

For each `TC-*` case: write spans, poison destination extents, START, dump,
and compare against the catalog's `interpret_chain` expected writes. Poisoning
prevents a false pass when DMA never wrote the destination.

Every case peeks the head TCD after the last install and before START, then
ends on the same three `hil.checks` assertions: the destination bytes match
the oracle, no installed 11-byte TCD was overwritten, and the host port came
back idle (DONE=1, BUS_GNT / BUS_REQ low, rst_n released, MCU Hi-Z).

Under `--detail` each case also prints its stimulus, descriptors, oracle
transaction log, and an expected-versus-actual dest dump before those
assertions run, so a passing case still leaves evidence.
"""

from __future__ import annotations

import pytest

from hil.cases import (
    case_address_corners,
    case_chain,
    case_cross_01,
    case_cross_10,
    case_empty,
    case_length_corners,
    case_next_device,
    case_overlap,
    case_quit,
    case_restart,
    case_same_0,
    case_same_1,
    case_smoke,
    get_all_cases,
)
from hil.checks import (
    check_descriptors_intact,
    check_dest_writes,
    check_head_tcd_installed,
    check_host_status,
)
from hil.session import Session


def _poison_extents(session: Session, extents) -> None:
    """Overwrite destination windows with inverted/zero bytes before START."""
    if not extents:
        return
    current = session.read_spans(extents)
    poison_spans = []
    for device, addr, length in extents:
        payload = bytearray(length)
        for offset in range(length):
            value = current.get((device, addr + offset), 0)
            payload[offset] = (~value) & 0xFF
        poison_spans.append((device, addr, bytes(payload)))
    session.write_spans(poison_spans)


def _run_case(session: Session, case, detail, *, restart: bool = False) -> None:
    session.write_spans(case.spans)
    _poison_extents(session, case.dump_extents)
    # Re-install stimulus so overlapping src/dest cases keep a correct source
    # image after poison; non-overlapping destinations are not in spans, so
    # they stay poisoned.
    session.write_spans(case.spans)
    check_head_tcd_installed(case, session)
    session.start_and_wait(timeout_ms=5000)
    if restart:
        # Second START must re-fetch the fixed head (PSRAM0 0x000000) with no
        # reset; poison again so a no-op second run cannot falsely pass.
        _poison_extents(session, case.dump_extents)
        session.write_spans(case.spans)
        check_head_tcd_installed(case, session)
        session.start_and_wait(timeout_ms=5000)
    dumped = session.read_spans(case.dump_extents) if case.dump_extents else {}
    status = session.get_status()
    # Report before asserting so a failing case still prints its full block.
    detail.report_case(
        case.id,
        name=case.name,
        markers=case.markers,
        memory=case.initial_mem,
        expected=case.expected_writes,
        dumped=dumped,
        spans=case.spans,
        status=status,
        notes=("second START re-fetched the fixed head",) if restart else (),
    )
    check_dest_writes(case.expected_writes, dumped, label=f"{case.id} dest writes")
    check_descriptors_intact(case, session)
    check_host_status(status)


@pytest.mark.smoke
@pytest.mark.same_device
def test_hw_smoke(session, detail):
    _run_case(session, case_smoke(), detail)


@pytest.mark.same_device
def test_hw_same_0(session, detail):
    _run_case(session, case_same_0(), detail)


@pytest.mark.same_device
def test_hw_same_1(session, detail):
    _run_case(session, case_same_1(), detail)


@pytest.mark.cross_device
def test_hw_cross_01(session, detail):
    _run_case(session, case_cross_01(), detail)


@pytest.mark.cross_device
def test_hw_cross_10(session, detail):
    _run_case(session, case_cross_10(), detail)


@pytest.mark.chain
def test_hw_chain(session, detail):
    _run_case(session, case_chain(), detail)


@pytest.mark.next_device
@pytest.mark.chain
def test_hw_next_device(session, detail):
    _run_case(session, case_next_device(), detail)


@pytest.mark.length
def test_hw_length_corners(session, detail):
    _run_case(session, case_length_corners(), detail)


@pytest.mark.quit
def test_hw_quit(session, detail):
    _run_case(session, case_quit(), detail)


@pytest.mark.quit
@pytest.mark.smoke
def test_hw_empty(session, detail):
    _run_case(session, case_empty(), detail)


@pytest.mark.restart
@pytest.mark.start
def test_hw_restart(session, detail):
    _run_case(session, case_restart(), detail, restart=True)


@pytest.mark.address
def test_hw_address_corners(session, detail):
    _run_case(session, case_address_corners(), detail)


@pytest.mark.overlap
def test_hw_overlap(session, detail):
    _run_case(session, case_overlap(), detail)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            case,
            id=case.id,
            marks=[getattr(pytest.mark, marker) for marker in case.markers],
        )
        for case in get_all_cases()
    ],
)
def test_hw_all_cases_parametrized(session, case, detail):
    """Catch any catalog entry missing a dedicated test function above."""
    _run_case(session, case, detail, restart=(case.id == "TC-RESTART"))
