"""Unit tests for the `--detail` verbose reporter (`hil.detail`).

No Session and no hardware. Each test drives `DetailReporter` with a directed
case's oracle data and inspects the rendered lines.
"""

from __future__ import annotations

import pytest

from hil.cases import case_length_corners, case_quit, case_smoke
from hil.detail import (
    DEFAULT_DETAIL_BYTES,
    MAX_DETAIL_ROWS,
    DetailReporter,
    null_reporter,
)

IDLE_STATUS = {
    "done": 1,
    "bus_gnt": False,
    "bus_req": False,
    "rst_n_low": False,
    "oe": 0x00,
}


def reporter(**kwargs):
    """An enabled reporter that collects blocks instead of printing them."""
    written: list[str] = []
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("target", "loopback")
    kwargs.setdefault("seed", 0)
    return DetailReporter(writer=written.append, **kwargs), written


def report_smoke(rep, **overrides):
    case = case_smoke()
    payload = dict(
        name=case.name,
        markers=case.markers,
        memory=case.initial_mem,
        expected=case.expected_writes,
        dumped=dict(case.expected_writes),
        spans=case.spans,
        status=IDLE_STATUS,
    )
    payload.update(overrides)
    return rep.report_case(case.id, **payload)


# --- disabled by default -------------------------------------------------


def test_selftest_disabled_reporter_writes_nothing_and_returns_empty():
    written: list[str] = []
    rep = DetailReporter(enabled=False, writer=written.append)
    assert report_smoke(rep) == ()
    assert written == []


def test_selftest_null_reporter_is_disabled():
    assert null_reporter().enabled is False
    assert null_reporter().report_case("TC-SMOKE") == ()


def test_selftest_default_reporter_is_disabled():
    assert DetailReporter().enabled is False


# --- header and sections -------------------------------------------------


def test_selftest_header_carries_case_id_name_target_seed_and_markers():
    rep, written = reporter()
    lines = report_smoke(rep)
    assert len(written) == 1
    assert written[0] == "\n".join(lines)
    assert lines[0].startswith("--- HIL detail: TC-SMOKE (")
    assert "target=loopback" in lines[1]
    assert "seed=0" in lines[1]
    assert "markers=smoke,same_device" in lines[1]


def test_selftest_block_reports_stimulus_oracle_compare_and_host_status():
    rep, _written = reporter()
    text = "\n".join(report_smoke(rep))
    assert "stimulus:" in text
    assert "byte(s) installed on dev0" in text
    assert "oracle:" in text
    assert "descriptor fetch(es)" in text
    assert "dest compare:" in text
    assert "descriptors (fetch order" in text
    assert "oracle transactions (" in text
    assert "host: done=1 bus_gnt=False bus_req=False rst_n_low=False oe=0x00" in text


def test_selftest_matching_dump_reports_every_byte_matching_with_no_diff_row():
    rep, _written = reporter()
    text = "\n".join(report_smoke(rep))
    assert "dest compare: 8/8 byte(s) match across 1 extent(s)" in text
    assert "  match" in text
    assert "expect  " in text
    assert "actual  " in text
    assert "diff " not in text


def test_selftest_mismatch_is_counted_marked_and_located():
    case = case_smoke()
    corrupt = dict(case.expected_writes)
    bad_key = sorted(corrupt)[1]
    corrupt[bad_key] = (~corrupt[bad_key]) & 0xFF
    rep, _written = reporter()
    text = "\n".join(report_smoke(rep, dumped=corrupt))
    assert "dest compare: 7/8 byte(s) match" in text
    assert "1 byte(s) differ (first 0x%06X)" % bad_key[1] in text
    assert "diff    " in text
    assert "^^" in text


def test_selftest_missing_read_back_byte_renders_as_absent_cell():
    case = case_smoke()
    partial = dict(case.expected_writes)
    partial.pop(sorted(partial)[0])
    rep, _written = reporter()
    text = "\n".join(report_smoke(rep, dumped=partial))
    assert "--" in text
    assert "1 byte(s) differ" in text


def test_selftest_quit_case_reports_that_nothing_moved():
    case = case_quit()
    rep, _written = reporter()
    text = "\n".join(
        rep.report_case(
            case.id,
            memory=case.initial_mem,
            expected=case.expected_writes,
            dumped={},
        )
    )
    assert "dest compare: no destination bytes (chain moved nothing)" in text
    assert "memory compare" not in text


# --- budgets and truncation ---------------------------------------------


def test_selftest_detail_bytes_zero_keeps_summary_and_drops_the_dump():
    rep, _written = reporter(max_bytes=0)
    text = "\n".join(report_smoke(rep))
    assert "dest compare:" in text
    assert "memory compare" not in text
    assert "expect  " not in text


def test_selftest_dump_truncates_at_the_byte_budget_and_reports_the_remainder():
    case = case_length_corners()
    rep, _written = reporter(max_bytes=8)
    text = "\n".join(
        rep.report_case(
            case.id,
            memory=case.initial_mem,
            expected=case.expected_writes,
            dumped=dict(case.expected_writes),
        )
    )
    hidden = len(case.expected_writes) - 8
    assert "... %d more byte(s) not shown (raise --detail-bytes)" % hidden in text


def test_selftest_long_case_caps_descriptor_and_transaction_rows():
    case = case_length_corners()
    rep, _written = reporter(max_bytes=0)
    text = "\n".join(
        rep.report_case(
            case.id,
            memory=case.initial_mem,
            expected=case.expected_writes,
            dumped=dict(case.expected_writes),
        )
    )
    assert "more transaction(s)" in text
    assert text.count("    #") <= MAX_DETAIL_ROWS


def test_selftest_negative_byte_budget_is_clamped_to_zero():
    assert DetailReporter(max_bytes=-5).max_bytes == 0


def test_selftest_default_byte_budget_matches_the_cli_default():
    assert DetailReporter().max_bytes == DEFAULT_DETAIL_BYTES


# --- optional sections ---------------------------------------------------


def test_selftest_sections_without_data_are_skipped():
    rep, _written = reporter()
    lines = rep.report_case("TC-BARE")
    assert lines[0] == "--- HIL detail: TC-BARE ---"
    text = "\n".join(lines)
    assert "stimulus:" not in text
    assert "oracle:" not in text
    assert "guards:" not in text
    assert "host:" not in text


def test_selftest_guard_and_note_lines_appear_when_supplied():
    rep, _written = reporter()
    text = "\n".join(
        report_smoke(rep, guard={(0, 0x100): 0, (0, 0x101): 0}, notes=("child seed 7",))
    )
    assert "guards: 2 byte(s) checked outside the destination extents" in text
    assert "  note: child seed 7" in text


def test_selftest_expected_defaults_to_the_oracle_when_only_memory_is_given():
    case = case_smoke()
    rep, _written = reporter(max_bytes=0)
    text = "\n".join(
        rep.report_case(case.id, memory=case.initial_mem, dumped=case.expected_writes)
    )
    assert "dest compare: 8/8 byte(s) match" in text


# --- pytest wiring -------------------------------------------------------


def test_selftest_detail_fixture_mirrors_the_cli_flags(request, detail):
    """Verbose output is opt-in: the fixture is exactly what `--detail` says."""
    assert isinstance(detail, DetailReporter)
    assert detail.enabled is request.config.getoption("--detail")
    assert detail.max_bytes == max(request.config.getoption("--detail-bytes"), 0)
    if not detail.enabled:
        assert detail.report_case("TC-SMOKE") == ()


@pytest.mark.parametrize("case_id", ["TC-SMOKE", "random-0"])
def test_selftest_report_case_returns_the_same_lines_it_writes(case_id):
    rep, written = reporter()
    lines = rep.report_case(case_id, status=IDLE_STATUS)
    assert written == ["\n".join(lines)]
