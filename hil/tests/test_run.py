"""Unit tests for the interactive HIL test runner (``hil.run``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hil import run
from hil.run import (
    CATEGORIES,
    HARDWARE_CATEGORIES,
    SELF_TEST_CATEGORY,
    build_pytest_argv,
    categories_with_counts,
    categorize_from_names,
    categorize_tests,
    collect_catalog,
    filter_items,
    format_category_menu,
    has_target_arg,
    interactive_loop,
    load_last_selection,
    parse_collect_output,
    parse_selection_spec,
    save_last_selection,
)


COLLECT_SAMPLE = """\
hil/tests/test_link.py::test_b64_round_trip_covers_every_byte_value
hil/tests/test_session.py::test_selftest_loopback_copy_same_device
hil/tests/test_directed.py::test_hw_same_0
hil/tests/test_directed.py::test_hw_cross_01
hil/tests/test_directed.py::test_hw_chain
hil/tests/test_directed.py::test_hw_len_corners
hil/tests/test_directed.py::test_hw_quit
hil/tests/test_directed.py::test_hw_addr_wide
hil/tests/test_directed.py::test_hw_overlap
hil/tests/test_directed.py::test_hw_restart
hil/tests/test_bus.py::test_hw_bus_req_while_idle
hil/tests/test_smoke.py::test_hw_smoke_bringup
hil/tests/test_random.py::test_hw_random_campaign_seeded
hil/tests/test_reset.py::test_hw_reset_recovery
========================= 14 tests collected in 0.12s =========================
"""


def test_selftest_parse_collect_output_extracts_nodeids_ignores_summary():
    nodeids = parse_collect_output(COLLECT_SAMPLE)
    assert len(nodeids) == 14
    assert nodeids[0].endswith("::test_b64_round_trip_covers_every_byte_value")
    assert all("::" in n for n in nodeids)
    assert not any(n.startswith("=") for n in nodeids)


def test_selftest_parse_collect_output_dedupes_and_skips_blank_lines():
    text = """
hil/tests/a.py::test_one

hil/tests/a.py::test_one
hil/tests/a.py::test_two
"""
    assert parse_collect_output(text) == [
        "hil/tests/a.py::test_one",
        "hil/tests/a.py::test_two",
    ]


def test_selftest_parse_collect_output_rejects_non_nodeid_noise():
    text = "ERROR collecting hil/tests/broken.py\nsome traceback\n"
    assert parse_collect_output(text) == []


def test_selftest_categorize_from_names_groups_by_substring_heuristics():
    nodeids = parse_collect_output(COLLECT_SAMPLE)
    groups = categorize_from_names(nodeids)
    assert list(groups.keys()) == list(CATEGORIES)
    assert "hil/tests/test_smoke.py::test_hw_smoke_bringup" in groups["hw_smoke"]
    assert any("test_hw_same_0" in n for n in groups["hw_same_device"])
    assert any("test_hw_cross_01" in n for n in groups["hw_cross_device"])
    assert any("test_hw_chain" in n for n in groups["hw_chain"])
    assert any("test_hw_len_corners" in n for n in groups["hw_length"])
    assert any("test_hw_quit" in n for n in groups["hw_quit"])
    assert any("test_hw_addr_wide" in n for n in groups["hw_address"])
    assert any("test_hw_overlap" in n for n in groups["hw_overlap"])
    assert any("test_hw_restart" in n for n in groups["hw_start"])
    assert any("test_hw_bus_req" in n for n in groups["hw_bus"])
    assert any("test_hw_reset_recovery" in n for n in groups["hw_reset"])
    assert any("test_hw_random_campaign" in n for n in groups["hw_random"])
    assert any("test_b64" in n for n in groups["self_test"])
    # A self-test whose name happens to share hardware vocabulary (here,
    # "same_device") must not leak into a hardware category on that alone:
    # only the `test_hw_` prefix (or, on the marker path, the `hw` marker)
    # ever puts a node in a hardware category.
    assert (
        "hil/tests/test_session.py::test_selftest_loopback_copy_same_device"
        in groups["self_test"]
    )


def test_selftest_has_target_arg_accepts_both_argparse_spellings():
    assert has_target_arg(["--target=fpga"]) is True
    assert has_target_arg(["-v", "--target", "loopback"]) is True
    assert has_target_arg([]) is False
    assert has_target_arg(["-v", "--seed=3", "--targeted"]) is False


def test_selftest_runner_refuses_to_start_without_a_target(capsys):
    assert run.main(["-v"]) == 2
    message = capsys.readouterr().err
    assert "needs a target" in message
    assert "loopback is in-process fake hardware" in message


def test_selftest_name_heuristics_place_hw_tests_and_keep_self_tests_separate():
    nodeids = [
        "hil/tests/test_directed.py::test_hw_same_0",
        "hil/tests/test_directed.py::test_hw_same_1",
        "hil/tests/test_directed.py::test_hw_cross_01",
        "hil/tests/test_directed.py::test_hw_cross_10",
        "hil/tests/test_directed.py::test_hw_empty",
        "hil/tests/test_random.py::test_hw_random_chain[0]",
        "hil/tests/test_random.py::test_selftest_random_chain_is_reproducible[0]",
        "hil/tests/test_random.py::test_selftest_child_seeds_are_distinct_and_deterministic",
        "hil/tests/test_link.py::test_selftest_b64_round_trip_of_empty_and_unpadded_lengths",
        "hil/tests/test_run.py::test_selftest_interactive_quit_without_running",
        "hil/tests/test_checks.py::test_selftest_guard_bytes_clamp_at_address_zero",
        "hil/tests/test_checks.py::test_selftest_host_status_rejects_non_idle_signal[bus_gnt-True]",
        "hil/tests/test_link.py::test_selftest_link_reset_reboots_the_remote_and_resends_the_preamble",
        "hil/tests/test_session.py::"
        "test_selftest_timeout_kills_the_transfer_then_reset_recovery_restores_the_session",
    ]
    groups = categorize_from_names(nodeids)
    assert groups["hw_same_device"] == nodeids[0:2]
    assert groups["hw_cross_device"] == nodeids[2:4]
    assert groups["hw_quit"] == [nodeids[4]]
    # Only the `test_hw_random_chain` node is hardware; the two generator
    # self-tests share "random" in their name but take no `session` fixture
    # and must not be pulled into hw_random on name alone.
    assert groups["hw_random"] == [nodeids[5]]
    assert not any("random_chain" in n for n in groups["hw_chain"])
    # The loopback self-test keeps "reset_recovery" in its name but, lacking
    # the `test_hw_` prefix, must land in self_test rather than hw_reset -
    # this is the exact bug that once put it in a hardware category.
    assert groups["hw_reset"] == []
    self_test = groups[SELF_TEST_CATEGORY]
    assert any("random_chain_is_reproducible" in n for n in self_test)
    assert any("child_seeds" in n for n in self_test)
    assert any("unpadded_lengths" in n for n in self_test)
    assert any("quit_without_running" in n for n in self_test)
    assert any("address_zero" in n for n in self_test)
    assert any("bus_gnt" in n for n in self_test)
    assert any("link_reset" in n for n in self_test)
    assert any("reset_recovery" in n for n in self_test)


def test_selftest_categorize_tests_prefers_markers_when_provided():
    nodeids = [
        "hil/tests/x.py::test_alpha",
        "hil/tests/x.py::test_beta",
        "hil/tests/x.py::test_gamma",
        "hil/tests/x.py::test_delta",
    ]
    markers = {
        "hil/tests/x.py::test_alpha": ["hw", "smoke", "same_device"],
        "hil/tests/x.py::test_beta": ["hw", "cross_device"],
        # A feature marker alone, with no `hw` marker, must not imply a
        # hardware category. This is what keeps `test_session.py`'s
        # loopback self-tests out of the hardware categories even where
        # they still carried a feature marker.
        "hil/tests/x.py::test_delta": ["same_device"],
    }
    groups = categorize_tests(nodeids, markers)
    assert "hil/tests/x.py::test_alpha" in groups["hw_smoke"]
    assert "hil/tests/x.py::test_alpha" in groups["hw_same_device"]
    assert "hil/tests/x.py::test_beta" in groups["hw_cross_device"]
    assert "hil/tests/x.py::test_gamma" in groups[SELF_TEST_CATEGORY]
    assert "hil/tests/x.py::test_delta" in groups[SELF_TEST_CATEGORY]
    assert "hil/tests/x.py::test_delta" not in groups["hw_same_device"]


def test_selftest_categorize_tests_maps_marker_aliases():
    nodeids = [
        "hil/tests/x.py::test_restart_head",
        "hil/tests/x.py::test_next_fetch",
        "hil/tests/x.py::test_random_chain[0]",
    ]
    markers = {
        "hil/tests/x.py::test_restart_head": ["hw", "restart"],
        "hil/tests/x.py::test_next_fetch": ["hw", "next_device"],
        "hil/tests/x.py::test_random_chain[0]": ["hw", "random"],
    }
    groups = categorize_tests(nodeids, markers)
    assert groups["hw_start"] == ["hil/tests/x.py::test_restart_head"]
    assert groups["hw_chain"] == ["hil/tests/x.py::test_next_fetch"]
    assert groups["hw_random"] == ["hil/tests/x.py::test_random_chain[0]"]
    assert groups["hw_chain"] != groups["hw_random"]


def test_selftest_live_catalog_keeps_hw_tests_out_of_self_test_and_hw_random_out_of_hw_chain():
    nodeids, markers = collect_catalog()
    groups = categorize_tests(nodeids, markers)
    self_test_text = "\n".join(groups[SELF_TEST_CATEGORY])
    for suffix in (
        "::test_hw_same_0",
        "::test_hw_same_1",
        "::test_hw_cross_01",
        "::test_hw_cross_10",
        "::test_hw_empty",
        "::test_hw_smoke",
        "::test_hw_restart",
    ):
        assert suffix not in self_test_text, suffix
        assert any(n.endswith(suffix) or suffix in n for members in groups.values() for n in members)
    assert any("test_hw_random_chain[" in n for n in groups["hw_random"])
    assert not any("test_hw_random_chain[" in n for n in groups["hw_chain"])
    # The generator self-tests share "random"/"child_seeds" wording but take
    # no `session` fixture, so they belong in self_test, not hw_random.
    assert any("test_child_seeds" in n for n in groups[SELF_TEST_CATEGORY])
    assert not any("test_child_seeds" in n for n in groups["hw_random"])
    # None of `test_session.py`'s loopback self-tests (`test_selftest_*`)
    # may land in any hardware category, even though several share
    # case-feature vocabulary with the real `test_hw_*` cases.
    assert any(
        n.endswith("::test_selftest_full_round_trip_same_device")
        for n in groups[SELF_TEST_CATEGORY]
    )
    assert not any(
        "::test_selftest_" in n
        for category in HARDWARE_CATEGORIES
        for n in groups[category]
    )


def test_selftest_categories_with_counts_skips_empty():
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_hw_smoke_a",
            "hil/tests/test_link.py::test_unitish",
        ]
    )
    rows = categories_with_counts(groups)
    names = [n for n, _ in rows]
    assert "hw_smoke" in names
    assert SELF_TEST_CATEGORY in names
    assert "hw_overlap" not in names
    assert dict(rows)["hw_smoke"] == 1


def test_selftest_category_menu_counts_unique_hardware_tests():
    menu = format_category_menu(
        {
            "hw_smoke": ["hil/tests/test_directed.py::test_hw_smoke"],
            "hw_checks": ["hil/tests/test_directed.py::test_hw_smoke"],
            SELF_TEST_CATEGORY: ["hil/tests/test_link.py::test_selftest_link"],
        }
    )
    assert "Collected 2 unique tests in 3 categories (1 hardware / 1 self-test)" in menu


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
def test_selftest_parse_selection_spec_commands_ranges_and_filters(spec, count, expected):
    assert parse_selection_spec(spec, count) == expected


def test_selftest_parse_selection_spec_out_of_range_only_keeps_valid():
    assert parse_selection_spec("0,1,99", 3) == {1}


def test_selftest_parse_selection_spec_raises_when_nothing_valid():
    with pytest.raises(ValueError):
        parse_selection_spec("99", 3)


def test_selftest_filter_items_substring_case_insensitive():
    items = ["smoke_a", "Same_Device", "other"]
    assert filter_items(items, "same") == ["Same_Device"]
    assert filter_items(items, "SMOKE") == ["smoke_a"]


def test_selftest_save_and_load_last_selection_round_trip(tmp_path: Path):
    path = tmp_path / ".last-selection"
    nodeids = [
        "hil/tests/a.py::test_one",
        "hil/tests/b.py::test_two",
    ]
    save_last_selection(nodeids, path)
    assert path.is_file()
    assert load_last_selection(path) == nodeids


def test_selftest_load_last_selection_missing_or_blank(tmp_path: Path):
    missing = tmp_path / "missing"
    assert load_last_selection(missing) == []
    blank = tmp_path / "blank"
    blank.write_text("\n# comment\n\n", encoding="utf-8")
    assert load_last_selection(blank) == []


def test_selftest_build_pytest_argv_forwards_extra_args():
    nodeids = ["hil/tests/t.py::test_a", "hil/tests/t.py::test_b"]
    extra = ["--target=asic", "--port=/dev/ttyACM0", "--seed=42", "-v"]
    argv = build_pytest_argv(nodeids, extra, python="/venv/bin/python")
    assert argv[:3] == ["/venv/bin/python", "-m", "pytest"]
    assert argv[3:5] == nodeids
    assert argv[5:] == extra


def test_selftest_build_pytest_argv_without_extras():
    argv = build_pytest_argv(["hil/tests/t.py::test_a"], python="python")
    assert argv == ["python", "-m", "pytest", "hil/tests/t.py::test_a"]


def test_selftest_interactive_expand_then_run_selection(tmp_path: Path):
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_hw_smoke_a",
            "hil/tests/test_smoke.py::test_hw_smoke_b",
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
            "hil/tests/test_smoke.py::test_hw_smoke_a",
            "hil/tests/test_smoke.py::test_hw_smoke_b",
        ]
    ]
    assert load_last_selection(last) == runs[0]


def test_selftest_interactive_all_and_last_recall(tmp_path: Path):
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


def test_selftest_interactive_quit_without_running(tmp_path: Path):
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


def test_selftest_interactive_category_range_runs_multiple(tmp_path: Path):
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_hw_smoke_a",
            "hil/tests/test_bus.py::test_hw_bus_req",
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
    # hw_smoke and self_test are selected (indices 1 and 3); hw_bus (index 2,
    # between them in CATEGORIES order) is not.
    assert "hil/tests/test_smoke.py::test_hw_smoke_a" in runs[0]
    assert "hil/tests/test_link.py::test_unit_a" in runs[0]
    assert "hil/tests/test_bus.py::test_hw_bus_req" not in runs[0]


def test_selftest_interactive_filter_then_select(tmp_path: Path):
    groups = categorize_from_names(
        [
            "hil/tests/test_smoke.py::test_hw_smoke_a",
            "hil/tests/test_bus.py::test_hw_bus_req",
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
    assert runs == [["hil/tests/test_smoke.py::test_hw_smoke_a"]]
