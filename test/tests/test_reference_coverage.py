"""Pure-Python unit tests for the ``COV-*`` sampler and fragment aggregator.

Hits count only when checkers and the dual-axis scoreboard pass for that
window. No cocotb import: these run under pytest without a simulator.

Catalog IDs exercised here (defined once):

* ``COV-LEN`` - transfer length class
* ``COV-CHUNK`` - chunk position and size
* ``COV-DEVICE`` / ``COV-NEXTDEV`` - device tuples and fetch-device transitions
* ``COV-CHAINLEN`` / ``COV-END`` - executable count and descriptor outcome
* ``COV-ADDR`` / ``COV-DATA`` - pointer class and payload pattern
* ``COV-DEPTH`` / ``COV-DEPTH-LEN`` / ``COV-DEPTH-DEVICE`` - depth and crosses
* ``COV-CTRL-STATE`` and other L1 points via :meth:`CoverageSampler.record_observation`
"""

import inspect
import json
import sys
from pathlib import Path

import pytest

from reference.coverage import (
    ADDR_AT_OR_ABOVE_64K,
    ADDR_BELOW_64K,
    ADDR_HIGHEST,
    ADDR_PAGE_EDGE,
    ADDR_ZERO,
    CHUNK_FINAL_FULL,
    CHUNK_FINAL_PARTIAL,
    CHUNK_FIRST_FULL,
    CHUNK_MIDDLE_FULL,
    CHUNK_ONLY,
    CLOSURE_SCHEMA,
    COV_ADDR,
    COV_BUS_STATE,
    COV_CHAINLEN,
    COV_CHUNK,
    COV_CTRL_STATE,
    COV_DATA,
    COV_DEPTH,
    COV_DEPTH_DEVICE,
    COV_DEPTH_LEN,
    COV_DEVICE,
    COV_END,
    COV_LEN,
    COV_NEXTDEV,
    COV_QPI_PHASE,
    COV_START_PHASE,
    COV_START_RESULT,
    DATA_INCREMENT,
    DATA_ONES,
    DATA_WALK_ALT,
    DATA_ZERO,
    DEPTH_BINS,
    DMA_BUF_DEPTH_MAX,
    END_MULTI,
    END_ONE,
    END_QUIT,
    END_ZERO,
    FRAGMENT_FILENAME,
    FRAGMENT_SCHEMA,
    CoverageError,
    CoverageSampler,
    aggregate_fragments,
    applicable_len_bins,
    classify_address,
    classify_chunks,
    classify_length,
    classify_payload,
    collapsed_len_bins,
    find_fragments,
    regenerate_closure,
    required_bins,
    structural_exclusions,
)
from reference.generator import (
    PATTERN_INCREMENT,
    PATTERN_ONES,
    PATTERN_WALKING,
    PATTERN_ZERO,
    TcdSpec,
    build_directed_chain,
    len_addr_corner_specs,
)
from reference.scoreboard import RunContext, Scoreboard


def _assert_no_cocotb_import() -> None:
    """Fail if this test module pulled cocotb (sampler must stay sim-free)."""
    assert "cocotb" not in sys.modules


def sampler(depth: int = 1, **kwargs) -> CoverageSampler:
    context = kwargs.pop(
        "context",
        RunContext(level="L1", sim="unit", seed=1, depth=depth, test="unit"),
    )
    return CoverageSampler(depth, context=context, **kwargs)


def passing(depth: int, specs, **kwargs) -> CoverageSampler:
    chain = build_directed_chain(specs, dma_buf_depth=depth, seed=kwargs.pop("seed", 1))
    result = chain.interpret(depth)
    cov = sampler(depth, **kwargs)
    assert cov.record_passing(result, generated=chain) is True
    return cov


def test_coverage_module_does_not_import_cocotb():
    import reference.coverage as coverage

    source = inspect.getsource(coverage)
    assert "import cocotb" not in source
    assert "from cocotb" not in source
    _assert_no_cocotb_import()


def test_l1_adapter_source_imports_shared_fsm_tables_without_this_test_importing_it():
    """The adapter must use the shared FSM name tables; this test only reads the file."""
    path = Path(__file__).resolve().parents[1] / "common" / "coverage_l1.py"
    text = path.read_text(encoding="utf-8")
    assert "from common.constants import" in text
    assert "QSPI_ENGINE_STATES" in text
    assert "SYS_CONTROL_STATES" in text
    _assert_no_cocotb_import()


def test_failing_window_does_not_count_hits():
    chain = build_directed_chain([TcdSpec(transfer_len=1)])
    result = chain.interpret()
    cov = sampler(1)
    cov.record_chain(result, generated=chain)
    assert cov.pending_hits()[COV_LEN]["1"] == 1
    assert cov.commit_window(checkers_ok=True, scoreboard_ok=False) is False
    assert cov.hits == {}
    assert cov.fragment()["windows"][0]["counted"] is False


def test_checkers_fail_does_not_count_hits():
    chain = build_directed_chain([TcdSpec(transfer_len=1)])
    result = chain.interpret()
    cov = sampler(1)
    assert cov.record_passing(result, generated=chain, checkers_ok=False) is False
    assert cov.hits == {}


def test_passing_window_counts_len_device_end_and_depth():
    cov = passing(1, [TcdSpec(transfer_len=1, src_device=0, dest_device=1)])
    assert cov.hits[COV_LEN]["1"] == 1
    assert cov.hits[COV_DEVICE]["0x1"] == 1
    assert cov.hits[COV_END][END_ONE] == 1
    assert cov.hits[COV_END][END_QUIT] == 1
    assert cov.hits[COV_CHAINLEN]["1"] == 1
    assert cov.hits[COV_DEPTH]["1"] == 1
    assert cov.hits[COV_DEPTH_DEVICE]["1:0x1"] == 1
    assert cov.hits[COV_DEPTH_LEN]["1:1"] == 1
    assert cov.hits[COV_CHUNK][CHUNK_ONLY] == 1


def test_empty_chain_hits_chainlen_zero_and_quit():
    cov = passing(1, ())
    assert cov.hits[COV_CHAINLEN]["0"] == 1
    assert cov.hits[COV_END] == {END_QUIT: 1}
    assert COV_LEN not in cov.hits
    assert COV_DEVICE not in cov.hits


def test_zero_length_follow_and_nextdev_transition():
    specs = [
        TcdSpec(transfer_len=0, next_device=1),
        TcdSpec(transfer_len=1, src_device=1, dest_device=1, next_device=0),
    ]
    cov = passing(1, specs)
    assert cov.hits[COV_END][END_ZERO] == 1
    assert cov.hits[COV_END][END_ONE] == 1
    assert cov.hits[COV_LEN]["0"] == 1
    assert cov.hits[COV_NEXTDEV]["0x1"] == 1
    assert cov.hits[COV_NEXTDEV]["1x0"] == 1
    assert cov.hits[COV_CHAINLEN]["2"] == 1


def test_chainlen_three_plus():
    specs = [TcdSpec(transfer_len=1) for _ in range(3)]
    cov = passing(1, specs)
    assert cov.hits[COV_CHAINLEN]["3+"] == 1


def test_length_class_priority_and_middle():
    assert classify_length(0, 1) == "0"
    assert classify_length(1, 1) == "1"
    assert classify_length(2, 1) == "N+1"
    assert classify_length(3, 1) == "2N+1"
    assert classify_length(4, 1) == "middle"
    assert classify_length(255, 1) == "255"
    assert classify_length(3, 4) == "N-1"
    assert classify_length(4, 4) == "N"
    assert classify_length(7, 4) == "2N-1"
    assert classify_length(8, 4) == "2N"
    assert classify_length(9, 4) == "2N+1"


def test_n_minus_one_at_depth_1_is_recorded_exclusion_not_a_hit():
    """``COV-LEN`` bin N-1 collapses onto 0 at depth 1; do not silent-skip."""
    records = structural_exclusions(1, level="L1", sim="unit")
    n_minus_one = [row for row in records if row["id"] == COV_LEN and row["bin"] == "N-1"]
    assert len(n_minus_one) == 1
    assert "duplicates" in n_minus_one[0]["reason"]
    assert n_minus_one[0]["architecture_citation"]
    assert n_minus_one[0]["depth"] == 1
    assert n_minus_one[0]["unreachability"]
    assert "N-1" not in applicable_len_bins(1)

    cov = passing(1, [TcdSpec(transfer_len=0)])
    assert cov.hits[COV_LEN] == {"0": 1}
    assert "N-1" not in cov.hits[COV_LEN]
    assert any(row["id"] == COV_LEN and row["bin"] == "N-1" for row in cov.exclusions)
    assert any(
        row["id"] == COV_DEPTH_LEN and row["bin"] == "1:N-1" for row in cov.exclusions
    )


def test_stall_bus_state_is_recorded_exclusion():
    """``COV-BUS-STATE`` STALL is excluded because the request is already high."""
    records = structural_exclusions(5, level="L1", sim="*")
    stall = [row for row in records if row["id"] == COV_BUS_STATE and row["bin"] == "STALL"]
    assert len(stall) == 1
    assert "already requires" in stall[0]["reason"]
    assert stall[0]["depth"] == "*"
    assert stall[0]["architecture_citation"]
    assert stall[0]["unreachability"]

    cov = sampler(1)
    cov.record_observation(COV_BUS_STATE, "STALL")
    cov.commit_window(checkers_ok=True, scoreboard_ok=True)
    assert COV_BUS_STATE not in cov.hits
    assert any(row["id"] == COV_BUS_STATE and row["bin"] == "STALL" for row in cov.exclusions)


def test_depth_bins_are_full_harness_range_including_tapeout_five():
    assert DMA_BUF_DEPTH_MAX == 8
    assert DEPTH_BINS == (1, 2, 3, 4, 5, 6, 7, 8)
    assert "5" in required_bins()[COV_DEPTH]
    cov = passing(5, [TcdSpec(transfer_len=5)])
    assert cov.hits[COV_DEPTH] == {"5": 1}
    assert cov.hits[COV_LEN]["N"] == 1
    assert cov.hits[COV_DEPTH_LEN]["5:N"] == 1


def test_chunk_classification_multi_and_partial():
    assert classify_chunks(1, 4) == (CHUNK_ONLY,)
    assert classify_chunks(4, 4) == (CHUNK_ONLY,)
    assert classify_chunks(5, 4) == (CHUNK_FIRST_FULL, CHUNK_FINAL_PARTIAL)
    assert classify_chunks(8, 4) == (CHUNK_FIRST_FULL, CHUNK_FINAL_FULL)
    assert classify_chunks(9, 4) == (
        CHUNK_FIRST_FULL,
        CHUNK_MIDDLE_FULL,
        CHUNK_FINAL_PARTIAL,
    )
    cov = passing(4, [TcdSpec(transfer_len=9)])
    assert cov.hits[COV_CHUNK][CHUNK_FIRST_FULL] == 1
    assert cov.hits[COV_CHUNK][CHUNK_MIDDLE_FULL] == 1
    assert cov.hits[COV_CHUNK][CHUNK_FINAL_PARTIAL] == 1
    assert cov.hits[COV_END][END_MULTI] == 1


def test_address_classes_overlap_and_roles():
    assert ADDR_ZERO in classify_address(0)
    assert ADDR_PAGE_EDGE in classify_address(0)
    assert ADDR_BELOW_64K in classify_address(0x00FFFF)
    assert ADDR_AT_OR_ABOVE_64K in classify_address(0x010000)
    assert ADDR_PAGE_EDGE in classify_address(0x010000)
    assert ADDR_HIGHEST in classify_address(0x7FFFF0)
    chain = build_directed_chain(
        [
            TcdSpec(
                transfer_len=1,
                src_addr=0x000000,
                dest_addr=0x010000,
                next_tcd_addr=0x7FFFF5,
            )
        ],
        seed=3,
    )
    result = chain.interpret()
    cov = sampler(1)
    cov.record_passing(result, generated=chain)
    assert cov.hits[COV_ADDR]["src:zero"] == 1
    assert cov.hits[COV_ADDR]["dest:at_or_above_64k"] == 1
    assert cov.hits[COV_ADDR]["next:highest"] == 1


def test_len_addr_corner_chain_fills_depth_len_and_addr_holes():
    chain = build_directed_chain(len_addr_corner_specs(5), seed=99, dma_buf_depth=5)
    result = chain.interpret(5)
    cov = sampler(5)
    cov.record_passing(result, generated=chain)
    assert cov.hits[COV_ADDR]["src:zero"] == 1
    assert cov.hits[COV_ADDR]["next:highest"] == 1
    assert cov.hits[COV_DEPTH_LEN]["5:2N-1"] == 1
    assert cov.hits[COV_DEPTH_LEN]["5:2N"] == 1
    assert cov.hits[COV_DEPTH_LEN]["5:2N+1"] == 1


def test_payload_patterns():
    assert classify_payload(b"\x00\x00") == DATA_ZERO
    assert classify_payload(b"\xff\xff") == DATA_ONES
    assert classify_payload(bytes(1 << (i % 8) for i in range(8))) == DATA_WALK_ALT
    assert classify_payload(b"\xa5\x5a\xa5\x5a") == DATA_WALK_ALT
    assert classify_payload(b"\x01\x02\x03") == DATA_INCREMENT
    assert classify_payload(b"\x11\x22\x33") == "random"
    assert classify_payload(b"") is None

    zero = passing(1, [TcdSpec(transfer_len=2, pattern=PATTERN_ZERO)], seed=11)
    ones = passing(1, [TcdSpec(transfer_len=2, pattern=PATTERN_ONES)], seed=12)
    walk = passing(1, [TcdSpec(transfer_len=8, pattern=PATTERN_WALKING)], seed=13)
    incr = passing(1, [TcdSpec(transfer_len=4, pattern=PATTERN_INCREMENT)], seed=14)
    assert zero.hits[COV_DATA][DATA_ZERO] == 1
    assert ones.hits[COV_DATA][DATA_ONES] == 1
    assert walk.hits[COV_DATA][DATA_WALK_ALT] == 1
    assert incr.hits[COV_DATA][DATA_INCREMENT] == 1


def test_record_compare_accepts_scoreboard_and_still_needs_commit():
    chain = build_directed_chain([TcdSpec(transfer_len=1)])
    result = chain.interpret()
    board = Scoreboard.from_result(result)
    board.compare(result.transactions, result.final_memory)
    cov = sampler(1)
    cov.record_compare(result, generated=chain, scoreboard=board)
    assert cov.hits == {}
    assert cov.commit_window(checkers_ok=True, scoreboard_ok=True) is True
    assert cov.hits[COV_LEN]["1"] == 1


def test_l1_observations_count_only_after_commit():
    cov = sampler(1)
    cov.record_observation(COV_CTRL_STATE, "FETCH")
    cov.record_observation(COV_QPI_PHASE, "READ_DATA")
    cov.record_observation(COV_START_PHASE, "near-edge before")
    cov.record_observation(COV_START_RESULT, "idle accepted")
    assert cov.hits == {}
    cov.commit_window(checkers_ok=True, scoreboard_ok=True)
    assert cov.hits[COV_CTRL_STATE]["FETCH"] == 1
    assert cov.hits[COV_QPI_PHASE]["READ_DATA"] == 1
    assert cov.hits[COV_START_PHASE]["near_edge_before"] == 1
    assert cov.hits[COV_START_RESULT]["idle_accepted"] == 1


def test_unknown_observation_raises():
    cov = sampler(1)
    with pytest.raises(CoverageError):
        cov.record_observation("COV-NOT-A-POINT", "x")
    with pytest.raises(CoverageError):
        cov.record_observation(COV_CTRL_STATE, "NOT_A_STATE")


def test_depth_mismatch_raises():
    chain = build_directed_chain([TcdSpec(transfer_len=1)], dma_buf_depth=1)
    result = chain.interpret(1)
    cov = sampler(2)
    with pytest.raises(CoverageError, match="does not match"):
        cov.record_chain(result, generated=chain)


def test_absorb_fragment_appends_without_losing_prior_hits(tmp_path):
    first = passing(5, [TcdSpec(transfer_len=5)], run_dir=str(tmp_path))
    path = first.write_fragment()
    second = sampler(5, run_dir=str(tmp_path))
    second.absorb_fragment(path)
    second.record_observation(COV_START_RESULT, "idle_uncaptured")
    second.record_observation(COV_LEN, "2N+1")
    second.commit_window(checkers_ok=True, scoreboard_ok=True)
    assert second.hits[COV_LEN]["N"] == 1
    assert second.hits[COV_LEN]["2N+1"] == 1
    assert second.hits[COV_START_RESULT]["idle_uncaptured"] == 1
    assert second.fragment()["windows"][0]["counted"] is True


def test_write_fragment_uses_run_dir(tmp_path):
    cov = passing(1, [TcdSpec(transfer_len=1)], run_dir=str(tmp_path))
    path = cov.write_fragment()
    assert path == str(tmp_path / FRAGMENT_FILENAME)
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["schema"] == FRAGMENT_SCHEMA
    assert payload["run"]["depth"] == 1
    assert payload["hits"][COV_LEN]["1"] == 1
    assert payload["uncommitted_pending"] is False
    assert any(row["bin"] == "STALL" for row in payload["exclusions"])
    assert any(row["bin"] == "N-1" for row in payload["exclusions"])


def test_write_fragment_requires_run_dir():
    cov = sampler(1)
    with pytest.raises(CoverageError, match="run_dir"):
        cov.write_fragment()


def test_aggregator_sums_fragments_and_does_not_hand_edit_counts(tmp_path):
    first = passing(1, [TcdSpec(transfer_len=1)], run_dir=str(tmp_path / "a"))
    second = passing(
        5,
        [TcdSpec(transfer_len=5, src_device=1, dest_device=0)],
        run_dir=str(tmp_path / "b"),
        seed=2,
    )
    first.write_fragment()
    second.write_fragment()
    dest = tmp_path / "closure.json"
    report = regenerate_closure(str(tmp_path), str(dest))
    assert dest.is_file()
    assert report.schema == CLOSURE_SCHEMA
    assert report.hits[COV_LEN]["1"] == 1
    assert report.hits[COV_LEN]["N"] == 1
    assert report.hits[COV_DEPTH]["1"] == 1
    assert report.hits[COV_DEPTH]["5"] == 1
    assert report.hits[COV_DEPTH_DEVICE]["5:1x0"] == 1
    assert report.windows_counted == 2
    assert "5" not in report.missing.get(COV_DEPTH, [])
    assert "1" not in report.missing.get(COV_DEPTH, [])
    # Full harness range still missing the unswept depths.
    assert "2" in report.missing[COV_DEPTH]
    assert "4" in report.missing[COV_DEPTH]
    assert "8" in report.missing[COV_DEPTH]
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written["hits"][COV_LEN]["1"] == report.hits[COV_LEN]["1"]
    assert written["closed"] is False
    assert find_fragments(str(tmp_path)) == sorted(
        [str(tmp_path / "a" / FRAGMENT_FILENAME), str(tmp_path / "b" / FRAGMENT_FILENAME)]
    )


def test_aggregator_rejects_hand_broken_schema(tmp_path):
    path = tmp_path / FRAGMENT_FILENAME
    path.write_text(json.dumps({"schema": "hand-edited", "hits": {COV_LEN: {"1": 99}}}), encoding="utf-8")
    with pytest.raises(CoverageError, match="schema"):
        aggregate_fragments(str(tmp_path))


def test_from_config_uses_artifacts_run_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "level": "top",
        "dut_level": "L1",
        "sim": "icarus",
        "dma_buf_depth": 1,
        "timing_profile": "ideal",
        "seed": 7,
        "run_dir": str(tmp_path / "run"),
    }
    cov = CoverageSampler.from_config(config)
    chain = build_directed_chain([TcdSpec(transfer_len=1)], seed=7)
    cov.record_passing(chain.interpret(), generated=chain)
    path = cov.write_fragment()
    assert Path(path).parent == Path(config["run_dir"])


def test_uncommitted_pending_flag_on_fragment(tmp_path):
    chain = build_directed_chain([TcdSpec(transfer_len=1)])
    cov = sampler(1, run_dir=str(tmp_path))
    cov.record_chain(chain.interpret(), generated=chain)
    payload = json.loads(Path(cov.write_fragment()).read_text(encoding="utf-8"))
    assert payload["uncommitted_pending"] is True
    assert payload["hits"] == {}


def test_collapsed_len_bins_at_depth_two():
    collapsed = {name: owner for name, _value, owner in collapsed_len_bins(2)}
    assert collapsed["N-1"] == "1"
    assert collapsed["2N-1"] == "N+1"
    assert "N" not in collapsed
