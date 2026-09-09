"""Interactive terminal runner for demoboard HIL pytest suites.

Collects real node IDs from ``hil/tests/``, groups them by marker or name into
feature categories, and offers a stdlib-only menu (ranges, ``/filter``, last
selection recall) before invoking pytest with any forwarded CLI options.

Usage::

    python -m hil
    python -m hil --target=asic --port=/dev/ttyACM0 --seed=42 -v
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# Ordered category keys shown in the menu. Grouping prefers pytest markers of
# the same name, then substring matches on the node ID / test function name.
CATEGORIES: tuple[str, ...] = (
    "smoke",
    "same_device",
    "cross_device",
    "chain",
    "length",
    "quit",
    "address",
    "overlap",
    "start",
    "bus",
    "reset",
    "random",
    "unit",
)

# Name-based fallbacks when markers are absent (first match wins, else unit).
_CATEGORY_NAME_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("smoke", ("smoke",)),
    ("same_device", ("same_device", "same-device", "tc_same", "tc-same")),
    ("cross_device", ("cross_device", "cross-device", "tc_cross", "tc-cross")),
    ("chain", ("chain", "tc_chain", "tc-chain", "next_device", "next-device")),
    ("length", ("length", "len_corner", "len-corner", "tc_len", "tc-len")),
    ("quit", ("quit", "tc_quit", "tc-quit", "tc_empty", "tc-empty")),
    ("address", ("address", "addr_wide", "addr-wide", "tc_addr", "tc-addr")),
    ("overlap", ("overlap", "tc_overlap", "tc-overlap")),
    ("start", ("start", "restart", "tc_restart", "tc-restart")),
    ("bus", ("bus", "bus_req", "bus-req", "bus_gnt", "bus-gnt")),
    ("reset", ("reset",)),
    ("random", ("random", "fuzz")),
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


def _node_name(nodeid: str) -> str:
    """Return the leaf test name portion of a node ID (after the last ``::``)."""
    return nodeid.rsplit("::", 1)[-1].lower()


def _node_haystack(nodeid: str) -> str:
    return nodeid.lower().replace("\\", "/")


def categorize_from_names(nodeids: Sequence[str]) -> OrderedDict[str, list[str]]:
    """Group node IDs by category using name/path heuristics (no markers)."""
    groups: OrderedDict[str, list[str]] = OrderedDict((c, []) for c in CATEGORIES)
    for nodeid in nodeids:
        hay = _node_haystack(nodeid)
        name = _node_name(nodeid)
        placed = False
        for category, patterns in _CATEGORY_NAME_PATTERNS:
            if any(p in hay or p in name for p in patterns):
                groups[category].append(nodeid)
                placed = True
                break
        if not placed:
            groups["unit"].append(nodeid)
    return groups


def categorize_tests(
    nodeids: Sequence[str],
    markers_by_node: Mapping[str, Iterable[str]] | None = None,
) -> OrderedDict[str, list[str]]:
    """Group tests by marker when provided, otherwise by name heuristics.

    A node may appear in multiple marker categories. Unmatched nodes land in
    ``unit``. Name-based grouping assigns each node to at most one category.
    """
    if not markers_by_node:
        return categorize_from_names(nodeids)

    known = set(CATEGORIES)
    groups: OrderedDict[str, list[str]] = OrderedDict((c, []) for c in CATEGORIES)
    placed: set[str] = set()
    for nodeid in nodeids:
        marks = {m.lower() for m in markers_by_node.get(nodeid, ())}
        matched = False
        for category in CATEGORIES:
            if category == "unit":
                continue
            if category in marks:
                groups[category].append(nodeid)
                matched = True
                placed.add(nodeid)
        # Unknown markers are ignored; fall through to name heuristics / unit.
        if not matched:
            # Prefer name heuristics over dumping everything into unit.
            name_groups = categorize_from_names([nodeid])
            for category, members in name_groups.items():
                if members and category in known:
                    groups[category].extend(members)
                    placed.add(nodeid)
                    break
    for nodeid in nodeids:
        if nodeid not in placed:
            groups["unit"].append(nodeid)
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
    """Render the top-level category list with counts."""
    rows = categories_with_counts(groups)
    lines = [
        "HIL test runner",
        "===============",
        "Collected %d tests in %d categories"
        % (sum(c for _, c in rows), len(rows)),
        "",
    ]
    width = max((len(name) for name, _ in rows), default=4)
    for i, (name, count) in enumerate(rows, start=1):
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
    lines = [
        "Category: %s (%d tests)" % (category, len(nodeids)),
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


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: collect, menu, then pytest with forwarded options."""
    extra = list(sys.argv[1:] if argv is None else argv)
    try:
        nodeids = collect_tests()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not nodeids:
        print("No tests collected under hil/tests/.", file=sys.stderr)
        return 5
    groups = categorize_tests(nodeids)
    return interactive_loop(groups, all_nodeids=nodeids, extra_args=extra)


if __name__ == "__main__":
    raise SystemExit(main())
