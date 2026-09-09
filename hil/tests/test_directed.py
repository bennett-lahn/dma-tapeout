"""End-to-end directed HIL cases against Session.loopback().

For each `TC-*` case: write spans, poison destination extents, START, dump,
and compare against the catalog's `interpret_chain` expected writes. Poisoning
prevents a false pass when DMA never wrote the destination.

Every case ends on the same three `hil.checks` assertions: the destination
bytes match the oracle, no installed 11-byte TCD was overwritten, and the host
port came back idle (DONE=1, BUS_GNT / BUS_REQ low, rst_n released, MCU Hi-Z).
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
    check_host_status,
)
from hil.session import Session


@pytest.fixture
def session():
    """Brought-up loopback session (fake demoboard + chain engine)."""
    sess = Session.loopback()
    sess.init()
    sess.bring_up_psram()
    yield sess
    sess.close()


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


def _run_case(session: Session, case, *, restart: bool = False) -> None:
    session.write_spans(case.spans)
    _poison_extents(session, case.dump_extents)
    # Re-install stimulus so overlapping src/dest cases keep a correct source
    # image after poison; non-overlapping destinations are not in spans, so
    # they stay poisoned.
    session.write_spans(case.spans)
    session.start_and_wait(timeout_ms=5000)
    if restart:
        # Second START must re-fetch the fixed head (PSRAM0 0x000000) with no
        # reset; poison again so a no-op second run cannot falsely pass.
        _poison_extents(session, case.dump_extents)
        session.write_spans(case.spans)
        session.start_and_wait(timeout_ms=5000)
    dumped = session.read_spans(case.dump_extents) if case.dump_extents else {}
    check_dest_writes(case.expected_writes, dumped, label=f"{case.id} dest writes")
    check_descriptors_intact(case, session)
    check_host_status(session.get_status())


@pytest.mark.smoke
@pytest.mark.same_device
def test_smoke(session):
    _run_case(session, case_smoke())


@pytest.mark.same_device
def test_same_0(session):
    _run_case(session, case_same_0())


@pytest.mark.same_device
def test_same_1(session):
    _run_case(session, case_same_1())


@pytest.mark.cross_device
def test_cross_01(session):
    _run_case(session, case_cross_01())


@pytest.mark.cross_device
def test_cross_10(session):
    _run_case(session, case_cross_10())


@pytest.mark.chain
def test_chain(session):
    _run_case(session, case_chain())


@pytest.mark.next_device
@pytest.mark.chain
def test_next_device(session):
    _run_case(session, case_next_device())


@pytest.mark.length
def test_length_corners(session):
    _run_case(session, case_length_corners())


@pytest.mark.quit
def test_quit(session):
    _run_case(session, case_quit())


@pytest.mark.quit
@pytest.mark.smoke
def test_empty(session):
    _run_case(session, case_empty())


@pytest.mark.restart
def test_restart(session):
    _run_case(session, case_restart(), restart=True)


@pytest.mark.address
def test_address_corners(session):
    _run_case(session, case_address_corners())


@pytest.mark.overlap
def test_overlap(session):
    _run_case(session, case_overlap())


@pytest.mark.parametrize("case", get_all_cases(), ids=lambda c: c.id)
def test_all_cases_parametrized(session, case):
    """Catch any catalog entry missing a dedicated test function above."""
    _run_case(session, case, restart=(case.id == "TC-RESTART"))
