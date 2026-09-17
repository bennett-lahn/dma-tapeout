"""Interactive terminal runner for demoboard HIL pytest suites.

Collects real node IDs from ``hil/tests/``, groups them by pytest marker
(preferred) or a tight name fallback, and offers a stdlib-only menu before
invoking pytest with any forwarded CLI options.

`--target` is mandatory and is forwarded to pytest, so nobody lands on fake
loopback hardware by accident.

The menu always separates two kinds of category. **Hardware** categories
(``hw_*``) hold tests that take the shared ``session`` fixture and so drive
whatever ``--target`` is (real DUT on fpga/asic, fake on loopback); these are
the product under test. **Self-test** holds every host-only test of the
harness itself (parsing, formatting, the FPGA tool, and any loopback-only
test that builds its own session instead of taking the fixture) - it never
touches ``--target`` and passing it proves nothing about the RTL. See
``HARDWARE_CATEGORIES`` / ``SELF_TEST_CATEGORY`` below.

Usage::

    python -m hil --target=loopback
    python -m hil --target=fpga
    python -m hil --target=asic --seed=42 -v
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from collections import OrderedDict
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# Hardware/DUT categories: every member takes the `session` fixture (real
# I/O against whatever `--target` is), is auto-tagged `hw` by
# `hil/conftest.py`, and its function name carries a `test_hw_` prefix. These
# are the product under test.
HARDWARE_CATEGORIES: tuple[str, ...] = (
    "hw_smoke",
    "hw_same_device",
    "hw_cross_device",
    "hw_chain",
    "hw_length",
    "hw_quit",
    "hw_address",
    "hw_overlap",
    "hw_start",
    "hw_bus",
    "hw_reset",
    "hw_random",
    "hw_checks",
)

# Everything that is not hardware/DUT: host-only tests of the harness itself
# (parsing, formatting, categorization, the FPGA tool, and self-tests that
# build their own loopback session locally instead of taking the `session`
# fixture). Never touches `--target`. Kept as one bucket, deliberately not
# broken out by file/feature, so it cannot be confused with the hardware
# categories above.
SELF_TEST_CATEGORY = "self_test"

# Ordered category keys shown in the menu. Empty groups are hidden. Hardware
# categories always precede the self-test bucket so the menu (`hil/run.py`'s
# `format_category_menu`) can print one banner over each half.
CATEGORIES: tuple[str, ...] = HARDWARE_CATEGORIES + (SELF_TEST_CATEGORY,)

# The marker `hil/conftest.py` auto-applies to any test taking the `session`
# fixture. A node without this marker is never placed in a hardware category
# here, regardless of any feature marker (`same_device`, `chain`, ...) it
# might also carry: `hil/tests/test_session.py`'s loopback self-tests
# deliberately carry no feature markers for this exact reason, but this
# guard also protects against a future test doing so by mistake.
HARDWARE_MARKER = "hw"

# Feature markers that mean a specific hardware category, checked only on
# nodes that already carry `HARDWARE_MARKER`.
_MARKER_ALIASES: dict[str, str] = {
    "smoke": "hw_smoke",
    "same_device": "hw_same_device",
    "cross_device": "hw_cross_device",
    "chain": "hw_chain",
    "next_device": "hw_chain",
    "length": "hw_length",
    "quit": "hw_quit",
    "address": "hw_address",
    "overlap": "hw_overlap",
    "start": "hw_start",
    "restart": "hw_start",
    "bus": "hw_bus",
    "reset": "hw_reset",
    "random": "hw_random",
    "checks": "hw_checks",
}

# Name fallback when markers were not collected at all (e.g. the subprocess
# collection fallback path). A node is only considered hardware if its leaf
# test name carries the `test_hw_` prefix; everything else is `self_test`
# regardless of what words appear in the name. Patterns below then pick the
# specific hardware category, and any `test_hw_*` name matching none of them
# lands in `hw_checks` (the catch-all for session/checker sanity).
_HW_NAME_PREFIX = "test_hw_"
_HW_CATEGORY_NAME_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hw_smoke", ("test_hw_smoke", "tc-smoke", "tc_smoke")),
    (
        "hw_same_device",
        ("same_device", "same-device", "same_0", "same_1", "tc_same", "tc-same"),
    ),
    (
        "hw_cross_device",
        (
            "cross_device",
            "cross-device",
            "cross_01",
            "cross_10",
            "tc_cross",
            "tc-cross",
        ),
    ),
    ("hw_random", ("test_hw_random", "random_chain")),
    (
        "hw_chain",
        (
            "test_hw_chain",
            "tc_chain",
            "tc-chain",
            "next_device",
            "next-device",
        ),
    ),
    ("hw_length", ("length_corners", "len_corner", "len-corner", "tc_len", "tc-len")),
    (
        "hw_quit",
        (
            "test_hw_quit",
            "test_hw_empty",
            "tc_quit",
            "tc-quit",
            "tc_empty",
            "tc-empty",
        ),
    ),
    ("hw_address", ("address_corners", "addr_wide", "addr-wide", "tc_addr", "tc-addr")),
    ("hw_overlap", ("test_hw_overlap", "tc_overlap", "tc-overlap")),
    ("hw_start", ("test_hw_restart", "tc_restart", "tc-restart", "restart")),
    ("hw_bus", ("test_hw_bus", "tc_bus", "tc-bus")),
    ("hw_reset", ("reset_recovery", "tc_reset", "tc-reset")),
    (
        "hw_checks",
        ("descriptors_intact", "host_status", "session_fixture_bring_up", "session_shutdown"),
    ),
)

_HIL_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _HIL_DIR.parent
_TESTS_DIR = _HIL_DIR / "tests"
LAST_SELECTION_PATH = _HIL_DIR / ".last-selection"

# pytest --collect-only -q emits one nodeid per line; summary lines lack "::".
_NODEID_RE = re.compile(r".+::.+")


def parse_collect_output(text: str) -> list[str]:
    """Parse ``pytest --collect-only -q`` stdout into ordered unique node IDs."""
    seen: set[str] = set()
    nodeids: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("="):
            continue
        # Quiet mode may prefix status characters; strip a leading status token.
        if line[0] in ".FsExX" and " " in line and "::" not in line.split(" ", 1)[0]:
            line = line.split(" ", 1)[1].strip()
        if not _NODEID_RE.match(line):
            continue
        if line not in seen:
            seen.add(line)
            nodeids.append(line)
    return nodeids


class _CollectionPlugin:
    """In-process pytest plugin that records node IDs and marker names."""

    def __init__(self) -> None:
        self.nodeids: list[str] = []
        self.markers: dict[str, tuple[str, ...]] = {}

    def pytest_collection_finish(self, session) -> None:
        seen: set[str] = set()
        for item in session.items:
            nodeid = item.nodeid
            if nodeid in seen:
                continue
            seen.add(nodeid)
            self.nodeids.append(nodeid)
            self.markers[nodeid] = tuple(m.name for m in item.iter_markers())


def collect_catalog(
    tests_path: Path | str | None = None,
    *,
    python: str | None = None,
    cwd: Path | str | None = None,
) -> tuple[list[str], dict[str, tuple[str, ...]]]:
    """Collect ``hil/tests/`` node IDs plus each node's pytest markers."""
    path = Path(tests_path) if tests_path is not None else _TESTS_DIR
    work = Path(cwd) if cwd is not None else _REPO_ROOT
    try:
        collect_arg = str(path.resolve().relative_to(work.resolve()))
    except ValueError:
        collect_arg = str(path)
    plugin = _CollectionPlugin()
    stdout = io.StringIO()
    stderr = io.StringIO()
    previous = os.getcwd()
    code = 5
    try:
        os.chdir(str(work))
        import pytest

        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = pytest.main(
                [collect_arg, "--collect-only", "-q", "-p", "no:cacheprovider"],
                plugins=[plugin],
            )
    finally:
        os.chdir(previous)
    if plugin.nodeids:
        return plugin.nodeids, plugin.markers
    # Fall back to subprocess stdout if the in-process plugin saw nothing.
    nodeids = collect_tests(path, python=python, cwd=work)
    if not nodeids and code not in (0, 5):
        combined = stdout.getvalue() + "\n" + stderr.getvalue()
        raise RuntimeError(
            "pytest collection failed (exit %s):\n%s"
            % (code, combined.strip() or "(no output)")
        )
    return nodeids, {}


def collect_tests(
    tests_path: Path | str | None = None,
    *,
    python: str | None = None,
    cwd: Path | str | None = None,
) -> list[str]:
    """Run ``pytest --collect-only -q`` and return discovered node IDs."""
    path = Path(tests_path) if tests_path is not None else _TESTS_DIR
    exe = python or sys.executable
    work = Path(cwd) if cwd is not None else _REPO_ROOT
    proc = subprocess.run(
        [exe, "-m", "pytest", "--collect-only", "-q", str(path)],
        cwd=str(work),
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    nodeids = parse_collect_output(proc.stdout or "")
    if not nodeids and proc.returncode not in (0, 5):
        # 5 = no tests collected; other codes are real collection failures.
        raise RuntimeError(
            "pytest collection failed (exit %s):\n%s"
            % (proc.returncode, combined.strip() or "(no output)")
        )
    return nodeids


def _hw_feature_categories(marks: Iterable[str]) -> set[str]:
    """Return hardware menu categories implied by a node's feature markers.

    Only meaningful once the caller has already confirmed `HARDWARE_MARKER`
    is present; a feature marker alone (e.g. `same_device`) never implies a
    hardware category by itself, so it cannot pull a self-test in.
    """
    found: set[str] = set()
    for raw in marks:
        category = _MARKER_ALIASES.get(raw.lower())
        if category in HARDWARE_CATEGORIES:
            found.add(category)
    return found


def _node_name(nodeid: str) -> str:
    """Return the leaf test name portion of a node ID (after the last ``::``)."""
    return nodeid.rsplit("::", 1)[-1].lower()


def _node_haystack(nodeid: str) -> str:
    return nodeid.lower().replace("\\", "/")


def _is_hw_name(nodeid: str) -> bool:
    """True when a node's leaf test name carries the `test_hw_` prefix.

    Name-only fallback for the marker-less collection path; the marker path
    (`categorize_tests` with `markers_by_node`) uses `HARDWARE_MARKER`
    instead and is authoritative whenever it is available.
    """
    return _node_name(nodeid).startswith(_HW_NAME_PREFIX)


def categorize_from_names(nodeids: Sequence[str]) -> OrderedDict[str, list[str]]:
    """Group node IDs by category using name/path heuristics (no markers).

    A node only ever lands in a hardware category when its name carries the
    `test_hw_` prefix (`_is_hw_name`); every other node is `self_test`, no
    matter what feature words its name happens to contain. This is what
    keeps a loopback self-test named e.g. `test_selftest_..._reset_recovery`
    out of `hw_reset`.
    """
    groups: OrderedDict[str, list[str]] = OrderedDict((c, []) for c in CATEGORIES)
    for nodeid in nodeids:
        if not _is_hw_name(nodeid):
            groups[SELF_TEST_CATEGORY].append(nodeid)
            continue
        hay = _node_haystack(nodeid)
        name = _node_name(nodeid)
        placed = False
        for category, patterns in _HW_CATEGORY_NAME_PATTERNS:
            if any(p in hay or p in name for p in patterns):
                groups[category].append(nodeid)
                placed = True
                break
        if not placed:
            groups["hw_checks"].append(nodeid)
    return groups


def categorize_tests(
    nodeids: Sequence[str],
    markers_by_node: Mapping[str, Iterable[str]] | None = None,
) -> OrderedDict[str, list[str]]:
    """Group tests by marker when provided, otherwise by name heuristics.

    A node needs `HARDWARE_MARKER` to land in any hardware category; it may
    then appear in several (one per matching feature marker), defaulting to
    `hw_checks` if none matched. Every other node - no `HARDWARE_MARKER`, no
    markers recorded, or (for the marker-less path) no `test_hw_` name
    prefix - lands in `self_test`, regardless of any feature marker it might
    also carry.
    """
    if not markers_by_node:
        return categorize_from_names(nodeids)

    groups: OrderedDict[str, list[str]] = OrderedDict((c, []) for c in CATEGORIES)
    for nodeid in nodeids:
        marks = set(markers_by_node.get(nodeid, ()))
        if not marks:
            # Nothing recorded for this node: fall back to its name alone.
            for category, members in categorize_from_names([nodeid]).items():
                if members:
                    groups[category].extend(members)
                    break
            continue
        if HARDWARE_MARKER not in marks:
            groups[SELF_TEST_CATEGORY].append(nodeid)
            continue
        categories = _hw_feature_categories(marks) or {"hw_checks"}
        for category in categories:
            groups[category].append(nodeid)
    return groups


def categories_with_counts(
    groups: Mapping[str, Sequence[str]],
) -> list[tuple[str, int]]:
    """Return ``(category, count)`` pairs, skipping empty categories."""
    return [(name, len(members)) for name, members in groups.items() if members]


def parse_selection_spec(
    spec: str,
    count: int,
) -> set[int] | tuple[str, str] | str:
    """Parse a menu line into indices (1-based) or a command token.

    Returns
    -------
    set[int]
        Selected 1-based indices within ``1..count``.
    'all' | 'quit' | 'last' | 'back'
        Named commands.
    ('filter', keyword)
        Substring filter request from ``/keyword``.
    """
    text = spec.strip()
    if not text:
        return "last"
    low = text.lower()
    if low in ("q", "quit"):
        return "quit"
    if low in ("a", "all"):
        return "all"
    if low == "last":
        return "last"
    if low in ("b", "back", ".."):
        return "back"
    if text.startswith("/"):
        return ("filter", text[1:])

    indices: set[int] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start = int(left.strip())
            end = int(right.strip())
            if start > end:
                start, end = end, start
            for i in range(start, end + 1):
                if 1 <= i <= count:
                    indices.add(i)
        else:
            i = int(token)
            if 1 <= i <= count:
                indices.add(i)
    if not indices:
        raise ValueError("no valid indices in selection %r (count=%s)" % (spec, count))
    return indices


def filter_items(items: Sequence[str], keyword: str) -> list[str]:
    """Keep items whose text contains ``keyword`` (case-insensitive)."""
    key = keyword.lower()
    return [item for item in items if key in item.lower()]


def save_last_selection(
    nodeids: Sequence[str],
    path: Path | str | None = None,
) -> Path:
    """Write selected node IDs (one per line) for later ``last`` / Enter recall."""
    dest = Path(path) if path is not None else LAST_SELECTION_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(nodeids) + ("\n" if nodeids else ""), encoding="utf-8")
    return dest


def load_last_selection(path: Path | str | None = None) -> list[str]:
    """Load previously saved node IDs, or ``[]`` if missing/empty."""
    src = Path(path) if path is not None else LAST_SELECTION_PATH
    if not src.is_file():
        return []
    out: list[str] = []
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def build_pytest_argv(
    nodeids: Sequence[str],
    extra_args: Sequence[str] | None = None,
    *,
    python: str | None = None,
) -> list[str]:
    """Build ``python -m pytest <nodeids...> <extra...>`` for subprocess exec."""
    cmd = [python or sys.executable, "-m", "pytest"]
    cmd.extend(nodeids)
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def run_pytest(
    nodeids: Sequence[str],
    extra_args: Sequence[str] | None = None,
    *,
    python: str | None = None,
    cwd: Path | str | None = None,
) -> int:
    """Execute pytest with selected node IDs, streaming stdout/stderr live."""
    if not nodeids:
        print("No tests selected.", file=sys.stderr)
        return 5
    cmd = build_pytest_argv(nodeids, extra_args, python=python)
    work = Path(cwd) if cwd is not None else _REPO_ROOT
    print("Running:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(work), check=False)
    return int(proc.returncode)


def format_category_menu(groups: Mapping[str, Sequence[str]]) -> str:
    """Render the top-level category list with counts.

    Hardware (``hw_*``) and self-test categories are always printed under
    separate banners, in that order, even though both live in one flat,
    continuously-numbered list for selection purposes.
    """
    rows = categories_with_counts(groups)
    hardware_nodeids = {
        nodeid
        for name, nodeids in groups.items()
        if name != SELF_TEST_CATEGORY
        for nodeid in nodeids
    }
    selftest_nodeids = set(groups.get(SELF_TEST_CATEGORY, ()))
    total = len(hardware_nodeids | selftest_nodeids)
    lines = [
        "HIL test runner",
        "===============",
        "Collected %d unique tests in %d categories (%d hardware / %d self-test)"
        % (total, len(rows), len(hardware_nodeids), len(selftest_nodeids)),
        "",
    ]
    width = max((len(name) for name, _ in rows), default=4)
    printed_hw_banner = False
    printed_self_test_banner = False
    for i, (name, count) in enumerate(rows, start=1):
        if name == SELF_TEST_CATEGORY:
            if not printed_self_test_banner:
                if printed_hw_banner:
                    lines.append("")
                lines.append("SELF-TEST (host-only harness checks; no --target)")
                printed_self_test_banner = True
        elif not printed_hw_banner:
            lines.append("HARDWARE (drives --target; the product under test)")
            printed_hw_banner = True
        lines.append("  %2d. %-*s  (%d)" % (i, width, name, count))
    lines.extend(
        [
            "",
            "Commands:",
            "  a / all       run all collected tests",
            "  N             expand category N",
            "  N,M,A-B       run categories by number / range",
            "  /keyword      filter categories or tests by substring",
            "  last / Enter  rerun previous selection",
            "  q / quit      exit without running",
            "",
        ]
    )
    return "\n".join(lines)


def format_test_menu(category: str, nodeids: Sequence[str]) -> str:
    """Render an expanded category's individual node IDs."""
    kind = (
        "self-test; host-only, no --target"
        if category == SELF_TEST_CATEGORY
        else "hardware; drives --target"
    )
    lines = [
        "Category: %s (%d tests, %s)" % (category, len(nodeids), kind),
        "",
    ]
    for i, nodeid in enumerate(nodeids, start=1):
        lines.append("  %2d. %s" % (i, nodeid))
    lines.extend(
        [
            "",
            "Commands:",
            "  a / all       run all tests in this category",
            "  N,M,A-B       run selected tests",
            "  /keyword      filter by substring",
            "  b / back / .. return to categories",
            "  q / quit      exit without running",
            "",
        ]
    )
    return "\n".join(lines)


def _indices_to_items(indices: set[int], items: Sequence[str]) -> list[str]:
    return [items[i - 1] for i in sorted(indices)]


def interactive_loop(
    groups: Mapping[str, Sequence[str]],
    *,
    all_nodeids: Sequence[str],
    extra_args: Sequence[str] | None = None,
    last_path: Path | str | None = None,
    input_fn=input,
    print_fn=print,
    run_fn=run_pytest,
) -> int:
    """Drive the category / test menu until the user runs tests or quits.

    Returns the pytest exit code, or ``0`` when quitting without a run.
    """
    path = Path(last_path) if last_path is not None else LAST_SELECTION_PATH
    category_rows = categories_with_counts(groups)
    category_names = [name for name, _ in category_rows]

    view: str = "categories"  # or "tests"
    view_category: str | None = None
    view_items: list[str] = list(category_names)
    filter_note = ""

    def show_menu() -> None:
        nonlocal filter_note
        if view == "categories":
            # Rebuild counts for the currently visible category names.
            visible_groups = OrderedDict(
                (name, groups[name]) for name in view_items if name in groups
            )
            print_fn(format_category_menu(visible_groups))
        else:
            assert view_category is not None
            print_fn(format_test_menu(view_category, view_items))
        if filter_note:
            print_fn(filter_note)
            filter_note = ""

    while True:
        show_menu()
        try:
            raw = input_fn("Selection: ")
        except EOFError:
            print_fn("")
            return 0

        try:
            parsed = parse_selection_spec(raw, len(view_items))
        except ValueError as exc:
            print_fn("Invalid selection: %s" % exc)
            continue

        if parsed == "quit":
            return 0

        if parsed == "last":
            previous = load_last_selection(path)
            if not previous:
                if not raw.strip():
                    print_fn("No previous selection to rerun.")
                else:
                    print_fn("No previous selection saved.")
                continue
            save_last_selection(previous, path)
            return run_fn(previous, extra_args)

        if parsed == "back":
            if view == "tests":
                view = "categories"
                view_category = None
                view_items = list(category_names)
            else:
                print_fn("Already at category list.")
            continue

        if isinstance(parsed, tuple) and parsed[0] == "filter":
            keyword = parsed[1]
            if view == "categories":
                filtered = [
                    name
                    for name in category_names
                    if keyword.lower() in name.lower()
                    or any(keyword.lower() in n.lower() for n in groups.get(name, ()))
                ]
            else:
                filtered = filter_items(view_items, keyword)
            if not filtered:
                filter_note = "No matches for %r." % keyword
                continue
            view_items = filtered
            filter_note = "Filtered to %d item(s) matching %r." % (
                len(filtered),
                keyword,
            )
            continue

        if parsed == "all":
            if view == "categories":
                selected = list(all_nodeids)
            else:
                selected = list(view_items)
            save_last_selection(selected, path)
            return run_fn(selected, extra_args)

        assert isinstance(parsed, set)
        if view == "categories":
            # Single index alone expands; multi-select / ranges run categories.
            if len(parsed) == 1 and "," not in raw and "-" not in raw:
                idx = next(iter(parsed))
                name = view_items[idx - 1]
                view = "tests"
                view_category = name
                view_items = list(groups[name])
                continue
            selected: list[str] = []
            seen: set[str] = set()
            for name in _indices_to_items(parsed, view_items):
                for nodeid in groups[name]:
                    if nodeid not in seen:
                        seen.add(nodeid)
                        selected.append(nodeid)
            save_last_selection(selected, path)
            return run_fn(selected, extra_args)

        selected = _indices_to_items(parsed, view_items)
        save_last_selection(selected, path)
        return run_fn(selected, extra_args)


def has_target_arg(args: Sequence[str]) -> bool:
    """True when `--target` was passed (`--target=x` or `--target x`)."""
    return any(arg == "--target" or arg.startswith("--target=") for arg in args)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: collect, menu, then pytest with forwarded options."""
    extra = list(sys.argv[1:] if argv is None else argv)
    if not has_target_arg(extra):
        print(
            "python -m hil needs a target: --target=loopback | fpga | asic.\n"
            "  loopback is in-process fake hardware and must be asked for by "
            "name.\n"
            "  --target=fpga compiles and uploads a fresh bitstream on its own.",
            file=sys.stderr,
        )
        return 2
    try:
        nodeids, markers = collect_catalog()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not nodeids:
        print("No tests collected under hil/tests/.", file=sys.stderr)
        return 5
    groups = categorize_tests(nodeids, markers)
    return interactive_loop(groups, all_nodeids=nodeids, extra_args=extra)


if __name__ == "__main__":
    raise SystemExit(main())
