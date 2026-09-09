"""Unit tests for the interactive HIL test runner (``hil.run``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hil.run import (
    CATEGORIES,
    build_pytest_argv,
    categories_with_counts,
    categorize_from_names,
    categorize_tests,
    filter_items,
    interactive_loop,
    load_last_selection,
    parse_collect_output,
    parse_selection_spec,
    save_last_selection,
)


COLLECT_SAMPLE = """\
hil/tests/test_link.py::test_b64_round_trip_covers_every_byte_value
hil/tests/test_session.py::test_loopback_copy_same_device
hil/tests/test_directed.py::test_tc_same_0
hil/tests/test_directed.py::test_tc_cross_01
hil/tests/test_directed.py::test_tc_chain
hil/tests/test_directed.py::test_tc_len_corners
hil/tests/test_directed.py::test_tc_quit
hil/tests/test_directed.py::test_tc_addr_wide
hil/tests/test_directed.py::test_tc_overlap
hil/tests/test_directed.py::test_tc_restart
hil/tests/test_bus.py::test_bus_req_while_idle
hil/tests/test_smoke.py::test_smoke_bringup
hil/tests/test_random.py::test_random_campaign_seeded
hil/tests/test_reset.py::test_reset_recovery
========================= 14 tests collected in 0.12s =========================
"""


def test_parse_collect_output_extracts_nodeids_ignores_summary():
    nodeids = parse_collect_output(COLLECT_SAMPLE)
    assert len(nodeids) == 14
    assert nodeids[0].endswith("::test_b64_round_trip_covers_every_byte_value")
    assert all("::" in n for n in nodeids)
    assert not any(n.startswith("=") for n in nodeids)


def test_parse_collect_output_dedupes_and_skips_blank_lines():
    text = """
hil/tests/a.py::test_one

hil/tests/a.py::test_one
hil/tests/a.py::test_two
"""
    assert parse_collect_output(text) == [
        "hil/tests/a.py::test_one",
        "hil/tests/a.py::test_two",
    ]


def test_parse_collect_output_rejects_non_nodeid_noise():
    text = "ERROR collecting hil/tests/broken.py\nsome traceback\n"
    assert parse_collect_output(text) == []


def test_categorize_from_names_groups_by_substring_heuristics():
    nodeids = parse_collect_output(COLLECT_SAMPLE)
    groups = categorize_from_names(nodeids)
    assert list(groups.keys()) == list(CATEGORIES)
    assert "hil/tests/test_smoke.py::test_smoke_bringup" in groups["smoke"]
    assert any("tc_same" in n for n in groups["same_device"])
    assert any("tc_cross" in n for n in groups["cross_device"])
    assert any("tc_chain" in n for n in groups["chain"])
    assert any("tc_len" in n for n in groups["length"])
    assert any("tc_quit" in n for n in groups["quit"])
    assert any("tc_addr" in n for n in groups["address"])
    assert any("tc_overlap" in n for n in groups["overlap"])
    assert any("restart" in n for n in groups["start"])
    assert any("bus_req" in n for n in groups["bus"])
    assert any("reset" in n for n in groups["reset"])
    assert any("random" in n for n in groups["random"])
    assert any("test_b64" in n for n in groups["unit"])


def test_categorize_tests_prefers_markers_when_provided():
    nodeids = [
        "hil/tests/x.py::test_alpha",
        "hil/tests/x.py::test_beta",
        "hil/tests/x.py::test_gamma",
    ]
    markers = {
        "hil/tests/x.py::test_alpha": ["smoke", "same_device"],
        "hil/tests/x.py::test_beta": ["cross_device"],
    }
    groups = categorize_tests(nodeids, markers)
    assert "hil/tests/x.py::test_alpha" in groups["smoke"]
    assert "hil/tests/x.py::test_alpha" in groups["same_device"]
    assert "hil/tests/x.py::test_beta" in groups["cross_device"]
    assert "hil/tests/x.py::test_gamma" in groups["unit"]


def test_categories_with_counts_skips_empty():
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_smoke_a",
            "hil/tests/test_link.py::test_unitish",
        ]
    )
    rows = categories_with_counts(groups)
    names = [n for n, _ in rows]
    assert "smoke" in names
    assert "unit" in names
    assert "overlap" not in names
    assert dict(rows)["smoke"] == 1


@pytest.mark.parametrize(
    "spec,count,expected",
    [
        ("a", 10, "all"),
        ("all", 10, "all"),
        ("q", 10, "quit"),
        ("quit", 10, "quit"),
        ("last", 10, "last"),
        ("", 10, "last"),
        ("b", 10, "back"),
        ("back", 10, "back"),
        ("..", 10, "back"),
        ("/smoke", 10, ("filter", "smoke")),
        ("1", 5, {1}),
        ("1,3,5", 5, {1, 3, 5}),
        ("5-7", 10, {5, 6, 7}),
        ("1,3,5-7", 10, {1, 3, 5, 6, 7}),
        ("7-5", 10, {5, 6, 7}),
        (" 2 , 4-5 ", 10, {2, 4, 5}),
    ],
)
def test_parse_selection_spec_commands_ranges_and_filters(spec, count, expected):
    assert parse_selection_spec(spec, count) == expected


def test_parse_selection_spec_out_of_range_only_keeps_valid():
    assert parse_selection_spec("0,1,99", 3) == {1}


def test_parse_selection_spec_raises_when_nothing_valid():
    with pytest.raises(ValueError):
        parse_selection_spec("99", 3)


def test_filter_items_substring_case_insensitive():
    items = ["smoke_a", "Same_Device", "other"]
    assert filter_items(items, "same") == ["Same_Device"]
    assert filter_items(items, "SMOKE") == ["smoke_a"]


def test_save_and_load_last_selection_round_trip(tmp_path: Path):
    path = tmp_path / ".last-selection"
    nodeids = [
        "hil/tests/a.py::test_one",
        "hil/tests/b.py::test_two",
    ]
    save_last_selection(nodeids, path)
    assert path.is_file()
    assert load_last_selection(path) == nodeids


def test_load_last_selection_missing_or_blank(tmp_path: Path):
    missing = tmp_path / "missing"
    assert load_last_selection(missing) == []
    blank = tmp_path / "blank"
    blank.write_text("\n# comment\n\n", encoding="utf-8")
    assert load_last_selection(blank) == []


def test_build_pytest_argv_forwards_extra_args():
    nodeids = ["hil/tests/t.py::test_a", "hil/tests/t.py::test_b"]
    extra = ["--target=asic", "--port=/dev/ttyACM0", "--seed=42", "-v"]
    argv = build_pytest_argv(nodeids, extra, python="/venv/bin/python")
    assert argv[:3] == ["/venv/bin/python", "-m", "pytest"]
    assert argv[3:5] == nodeids
    assert argv[5:] == extra


def test_build_pytest_argv_without_extras():
    argv = build_pytest_argv(["hil/tests/t.py::test_a"], python="python")
    assert argv == ["python", "-m", "pytest", "hil/tests/t.py::test_a"]


def test_interactive_expand_then_run_selection(tmp_path: Path):
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_smoke_a",
            "hil/tests/test_smoke.py::test_smoke_b",
            "hil/tests/test_link.py::test_unit_a",
        ]
    )
    all_ids = [n for members in groups.values() for n in members]
    last = tmp_path / ".last-selection"
    runs: list[list[str]] = []

    def fake_run(nodeids, extra_args=None):
        runs.append(list(nodeids))
        return 0

    inputs = iter(["1", "1-2"])
    code = interactive_loop(
        groups,
        all_nodeids=all_ids,
        extra_args=["-v"],
        last_path=last,
        input_fn=lambda _prompt: next(inputs),
        print_fn=lambda *_a, **_k: None,
        run_fn=fake_run,
    )
    assert code == 0
    assert runs == [
        [
            "hil/tests/test_smoke.py::test_smoke_a",
            "hil/tests/test_smoke.py::test_smoke_b",
        ]
    ]
    assert load_last_selection(last) == runs[0]


def test_interactive_all_and_last_recall(tmp_path: Path):
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_smoke_a",
            "hil/tests/test_link.py::test_unit_a",
        ]
    )
    all_ids = [n for members in groups.values() for n in members]
    last = tmp_path / ".last-selection"
    runs: list[tuple[list[str], list[str] | None]] = []

    def fake_run(nodeids, extra_args=None):
        runs.append((list(nodeids), list(extra_args) if extra_args else None))
        return 7

    inputs = iter(["all"])
    code = interactive_loop(
        groups,
        all_nodeids=all_ids,
        extra_args=["--seed=42"],
        last_path=last,
        input_fn=lambda _prompt: next(inputs),
        print_fn=lambda *_a, **_k: None,
        run_fn=fake_run,
    )
    assert code == 7
    assert runs[0][0] == all_ids
    assert runs[0][1] == ["--seed=42"]

    runs.clear()
    inputs = iter([""])
    code = interactive_loop(
        groups,
        all_nodeids=all_ids,
        extra_args=["--seed=99"],
        last_path=last,
        input_fn=lambda _prompt: next(inputs),
        print_fn=lambda *_a, **_k: None,
        run_fn=fake_run,
    )
    assert code == 7
    assert runs[0][0] == all_ids
    assert runs[0][1] == ["--seed=99"]


def test_interactive_quit_without_running(tmp_path: Path):
    groups = categorize_from_names(["hil/tests/a.py::test_x"])
    called = []

    code = interactive_loop(
        groups,
        all_nodeids=["hil/tests/a.py::test_x"],
        last_path=tmp_path / ".last-selection",
        input_fn=lambda _prompt: "q",
        print_fn=lambda *_a, **_k: None,
        run_fn=lambda *a, **k: called.append(1) or 0,
    )
    assert code == 0
    assert called == []


def test_interactive_category_range_runs_multiple(tmp_path: Path):
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_smoke_a",
            "hil/tests/test_bus.py::test_bus_req",
            "hil/tests/test_link.py::test_unit_a",
        ]
    )
    all_ids = [n for members in groups.values() for n in members]
    runs: list[list[str]] = []

    code = interactive_loop(
        groups,
        all_nodeids=all_ids,
        last_path=tmp_path / ".last-selection",
        input_fn=lambda _prompt: "1,3",
        print_fn=lambda *_a, **_k: None,
        run_fn=lambda nodeids, extra_args=None: runs.append(list(nodeids)) or 0,
    )
    assert code == 0
    assert "hil/tests/test_smoke.py::test_smoke_a" in runs[0]
    assert "hil/tests/test_link.py::test_unit_a" in runs[0]
    assert "hil/tests/test_bus.py::test_bus_req" not in runs[0]


def test_interactive_filter_then_select(tmp_path: Path):
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_smoke_a",
            "hil/tests/test_bus.py::test_bus_req",
            "hil/tests/test_link.py::test_unit_a",
        ]
    )
    all_ids = [n for members in groups.values() for n in members]
    runs: list[list[str]] = []
    inputs = iter(["/smoke", "1", "a"])
    code = interactive_loop(
        groups,
        all_nodeids=all_ids,
        last_path=tmp_path / ".last-selection",
        input_fn=lambda _prompt: next(inputs),
        print_fn=lambda *_a, **_k: None,
        run_fn=lambda nodeids, extra_args=None: runs.append(list(nodeids)) or 0,
    )
    assert code == 0
    assert runs == [["hil/tests/test_smoke.py::test_smoke_a"]]
