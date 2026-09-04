"""Pure-Python unit tests for handshake/controller disposition (no simulator).

Covers tb-hs-01 (L2 hierarchy ``na``, pin rows live), tb-hs-11 (L0 OPCODE
allowlist not blocked without a pin monitor), and retired-ID absence.
"""

from common.constants import RESULT_BLOCKED, RESULT_NA, RESULT_PASS
from monitors.handshake import (
    CHK_CTRL_DATA_PAIR,
    CHK_CTRL_FETCH_HEAD,
    CHK_CTRL_REQ_GATE,
    CHK_CTRL_STATE_VALID,
    CHK_HS_OPCODE,
    CHK_HS_RDATA_COUNT,
    CHK_HS_REQ_STABLE,
    CONTROLLER_CHECK_IDS,
    CTRL_HIERARCHY_CHECK_IDS,
    HANDSHAKE_CHECK_IDS,
    HS_HIERARCHY_CHECK_IDS,
    ControllerMonitor,
    HandshakeMonitor,
)

class _Handle:
    def __init__(self, value=0):
        self.value = value

class _Pin:
    blocked = False
    intervals = ()

import monitors.handshake as handshake_mod

def test_retired_data_cnt_removed_from_code():
    assert not hasattr(handshake_mod, "CHK_CTRL_DATA_CNT")
    assert "DATA-CNT" not in "".join(CONTROLLER_CHECK_IDS)

def test_l2_hierarchy_hs_is_na_not_blocked():
    """tb-hs-01: L2 lacks engine hierarchy; HS port rows are na, not blocked."""
    monitor = HandshakeMonitor(
        level="L2",
        na=HS_HIERARCHY_CHECK_IDS,
        missing=("qspi_engine hierarchy",),
    )
    assert not monitor.blocked
    results = monitor.results()
    for check_id in HS_HIERARCHY_CHECK_IDS:
        assert results[check_id] == RESULT_NA, check_id
    assert results[CHK_HS_OPCODE] == RESULT_NA
    assert CHK_HS_RDATA_COUNT not in monitor.blocked_reasons()

def test_l2_opcode_live_when_pin_attached():
    """tb-hs-01: pin OPCODE wait-cycle evidence stays live at L2."""
    monitor = HandshakeMonitor(
        level="L2",
        na=HS_HIERARCHY_CHECK_IDS,
        missing=("qspi_engine hierarchy",),
        pin=_Pin(),
    )
    assert monitor.results()[CHK_HS_OPCODE] == RESULT_PASS

def test_l0_opcode_allowlist_not_blocked_without_pin():
    """tb-hs-11 / tb-life-08: command allowlist stays live; wait half is not blocked."""
    monitor = HandshakeMonitor(
        level="L0",
        cmd=_Handle(0xEB),
        txn_valid=_Handle(0),
    )
    assert monitor.results()[CHK_HS_OPCODE] == RESULT_PASS
    assert CHK_HS_OPCODE not in monitor.blocked_reasons()
    assert monitor.results()[CHK_HS_REQ_STABLE] == RESULT_PASS

def test_l2_ctrl_hierarchy_na_pin_rows_not_blanket_na():
    """tb-hs-01: L2 CTRL hierarchy is na; FETCH-HEAD / DATA-PAIR stay applicable."""
    monitor = ControllerMonitor(
        level="L2",
        na=CTRL_HIERARCHY_CHECK_IDS,
        clk=_Handle(0),
        rst_n=_Handle(1),
        done=_Handle(1),
    )
    results = monitor.results()
    assert results[CHK_CTRL_REQ_GATE] == RESULT_NA
    assert results[CHK_CTRL_STATE_VALID] == RESULT_NA
    assert results[CHK_CTRL_FETCH_HEAD] == RESULT_BLOCKED
    assert results[CHK_CTRL_DATA_PAIR] == RESULT_BLOCKED
    monitor.attach_pin(_Pin())
    results = monitor.results()
    assert results[CHK_CTRL_FETCH_HEAD] == RESULT_PASS
    assert results[CHK_CTRL_DATA_PAIR] == RESULT_PASS

def test_handshake_check_ids_stable():
    assert CHK_HS_OPCODE in HANDSHAKE_CHECK_IDS
    assert len(HANDSHAKE_CHECK_IDS) == 7
