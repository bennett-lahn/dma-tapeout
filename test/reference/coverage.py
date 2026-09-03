"""Pure-Python functional-coverage sampler (``08-stimulus-and-coverage.md``).

Samples from golden chain / scoreboard events. Hits count only when the
window's checkers and the dual-axis scoreboard both pass. No cocotb.

Catalog IDs sampled here (defined once; later comments use the bare ID):

* ``COV-LEN`` - transfer length class (0, 1, N-1, N, N+1, 2N-1, 2N, 2N+1, 255, middle)
* ``COV-CHUNK`` - chunk position and size (only / first full / middle full / final full / final partial)
* ``COV-DEVICE`` - source x destination device (0x0, 0x1, 1x0, 1x1)
* ``COV-NEXTDEV`` - current fetch device x next fetch device (all four transitions)
* ``COV-CHAINLEN`` - executable TCD count (0, 1, 2, 3+)
* ``COV-END`` - descriptor outcome (quit, zero-length, one-chunk, multi-chunk)
* ``COV-ADDR`` - address class per SRC, DEST, and NEXT pointer
* ``COV-DATA`` - payload pattern (zero, ones, walking or alternating, incrementing, random)
* ``COV-DEPTH`` - compile-time ``DMA_BUF_DEPTH`` (full harness range 1..``DMA_BUF_DEPTH_MAX``, including tapeout 5)
* ``COV-DEPTH-LEN`` - depth x ``COV-LEN`` class
* ``COV-DEPTH-DEVICE`` - depth x source/destination tuple

Hierarchy / host points (``COV-CTRL-STATE``, ``COV-QPI-PHASE``, ``COV-BUS-*``,
``COV-RESET-*``, ``COV-START-*``) are recorded through
:meth:`CoverageSampler.record_observation`; the L1 adapter in
``common/coverage_l1.py`` translates DUT encodings.

Wave 2 public API: construct :class:`CoverageSampler`,
:meth:`CoverageSampler.record_chain` / :meth:`CoverageSampler.record_compare`
from a :class:`~reference.chain.ChainResult`,
:meth:`CoverageSampler.record_observation` (or the L1 adapter) during the
window, :meth:`CoverageSampler.commit_window` with both oracle flags, then
:meth:`CoverageSampler.write_fragment` into ``RUN_DIR``. Regenerate closure
with :func:`regenerate_closure` from retained fragments only.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field, replace

from reference.chain import ADDR_MAX, ChainResult
from reference.constants import DMA_BUF_DEPTH_MAX, PAGE_SIZE
from reference.scoreboard import RunContext

# Verification-side copy of ``qspi_pkg::DMA_BUF_DEPTH_MAX`` lives in
# ``reference.constants`` (not parsed from RTL).
DEPTH_BINS = tuple(range(1, DMA_BUF_DEPTH_MAX + 1))

FRAGMENT_SCHEMA = "dma-tapeout.coverage.fragment.v1"
CLOSURE_SCHEMA = "dma-tapeout.coverage.closure.v1"
FRAGMENT_FILENAME = "coverage.json"
CLOSURE_FILENAME = "coverage_closure.json"

# Recorded-exclusion citations (08-stimulus-and-coverage.md; D22 STALL).
EXCLUSION_CITATION = (
    "docs/llm/verification/08-stimulus-and-coverage.md "
    "(Coverage closure and exclusions; D22: BUS_REQ in IDLE enters STALL; "
    "length-class collapse at DMA_BUF_DEPTH 1/2)"
)
# Reviewer stamp for recorded exclusions (regenerated merge 2026-08-25).
EXCLUSION_REVIEWER = "tb-closure-2026-08-25"
EXCLUSION_DATE = "2026-08-25"

COV_LEN = "COV-LEN"
COV_CHUNK = "COV-CHUNK"
COV_DEVICE = "COV-DEVICE"
COV_NEXTDEV = "COV-NEXTDEV"
COV_CHAINLEN = "COV-CHAINLEN"
COV_END = "COV-END"
COV_ADDR = "COV-ADDR"
COV_DATA = "COV-DATA"
COV_CTRL_STATE = "COV-CTRL-STATE"
COV_QPI_PHASE = "COV-QPI-PHASE"
COV_BUS_STATE = "COV-BUS-STATE"
COV_BUS_PHASE = "COV-BUS-PHASE"
COV_BUS_RESUME = "COV-BUS-RESUME"
COV_START_PHASE = "COV-START-PHASE"
COV_START_RESULT = "COV-START-RESULT"
COV_RESET_STATE = "COV-RESET-STATE"
COV_RESET_PHASE = "COV-RESET-PHASE"
COV_DEPTH = "COV-DEPTH"
COV_DEPTH_LEN = "COV-DEPTH-LEN"
COV_DEPTH_DEVICE = "COV-DEPTH-DEVICE"

CHAIN_POINTS = (
    COV_LEN,
    COV_CHUNK,
    COV_DEVICE,
    COV_NEXTDEV,
    COV_CHAINLEN,
    COV_END,
    COV_ADDR,
    COV_DATA,
    COV_DEPTH,
    COV_DEPTH_LEN,
    COV_DEPTH_DEVICE,
)
L1_POINTS = (
    COV_CTRL_STATE,
    COV_QPI_PHASE,
    COV_BUS_STATE,
    COV_BUS_PHASE,
    COV_BUS_RESUME,
    COV_START_PHASE,
    COV_START_RESULT,
    COV_RESET_STATE,
    COV_RESET_PHASE,
)
ALL_POINTS = CHAIN_POINTS + L1_POINTS

# Length-class names in canonical priority (first owner of a numeric value wins).
LEN_CORNER_NAMES = ("0", "1", "255", "N-1", "N", "N+1", "2N-1", "2N", "2N+1")
LEN_MIDDLE = "middle"
LEN_BINS = LEN_CORNER_NAMES + (LEN_MIDDLE,)

CHUNK_ONLY = "only_chunk"
CHUNK_FIRST_FULL = "first_full"
CHUNK_MIDDLE_FULL = "middle_full"
CHUNK_FINAL_FULL = "final_full"
CHUNK_FINAL_PARTIAL = "final_partial"
CHUNK_BINS = (
    CHUNK_ONLY,
    CHUNK_FIRST_FULL,
    CHUNK_MIDDLE_FULL,
    CHUNK_FINAL_FULL,
    CHUNK_FINAL_PARTIAL,
)

DEVICE_TUPLES = ("0x0", "0x1", "1x0", "1x1")
CHAINLEN_BINS = ("0", "1", "2", "3+")
END_QUIT = "quit"
END_ZERO = "zero_length"
END_ONE = "one_chunk"
END_MULTI = "multi_chunk"
END_BINS = (END_QUIT, END_ZERO, END_ONE, END_MULTI)

ADDR_ZERO = "zero"
ADDR_BELOW_64K = "below_64k"
ADDR_AT_OR_ABOVE_64K = "at_or_above_64k"
ADDR_PAGE_EDGE = "page_edge"
ADDR_HIGHEST = "highest"
ADDR_CLASSES = (
    ADDR_ZERO,
    ADDR_BELOW_64K,
    ADDR_AT_OR_ABOVE_64K,
    ADDR_PAGE_EDGE,
    ADDR_HIGHEST,
)
ADDR_ROLES = ("src", "dest", "next")
ADDR_BINS = tuple(f"{role}:{cls}" for role in ADDR_ROLES for cls in ADDR_CLASSES)

DATA_ZERO = "zero"
DATA_ONES = "ones"
DATA_WALK_ALT = "walking_or_alternating"
DATA_INCREMENT = "incrementing"
DATA_RANDOM = "random"
DATA_BINS = (DATA_ZERO, DATA_ONES, DATA_WALK_ALT, DATA_INCREMENT, DATA_RANDOM)

# Handshake value tables (verification-side copies; L1 adapter checks they match).
CTRL_STATE_BINS = (
    "SYS_CTRL_IDLE",
    "NEW_FETCH",
    "FETCH",
    "NEW_OP",
    "READ",
    "WRITE",
    "UPDATE",
    "STALL",
)
QPI_PHASE_BINS = (
    "QSPI_IDLE",
    "CS_ON",
    "SEND_CMD_1",
    "SEND_CMD_2",
    "SEND_ADDR",
    "WAIT",
    "READ_DATA",
    "WRITE_DATA",
    "SCLK_OFF",
    "CS_OFF",
)
BUS_STATE_BINS = (
    "IDLE",
    "NEW_FETCH",
    "FETCH",
    "NEW_OP",
    "READ",
    "WRITE",
    "UPDATE",
)
BUS_PHASE_BINS = (
    "CS_ON",
    "command",
    "address",
    "wait",
    "read_data",
    "write_data",
    "SCLK_OFF",
    "CS_OFF",
)
BUS_RESUME_BINS = ("IDLE", "NEW_FETCH", "NEW_OP", "UPDATE")
START_PHASE_BINS = (
    "early",
    "near_edge_before",
    "on_edge",
    "near_edge_after",
    "late",
)
START_RESULT_BINS = (
    "idle_accepted",
    "idle_uncaptured",
    "active_ignored",
    "req_gnt_ignored",
    "held_high_single",
)
RESET_PHASE_BINS = (
    "idle_pad",
    "command",
    "address",
    "wait",
    "read_data",
    "write_data",
    "termination",
)

BOUNDARY_64K = 0x010000
PAGE_EDGE_NEIGHBORHOOD = 16
HIGHEST_WINDOW = 1024

# Aliases Wave 2 / the L1 adapter may pass; values are canonical bin names.
_START_PHASE_ALIASES = {
    "early": "early",
    "near-edge before": "near_edge_before",
    "near_edge_before": "near_edge_before",
    "on-edge": "on_edge",
    "on_edge": "on_edge",
    "near-edge after": "near_edge_after",
    "near_edge_after": "near_edge_after",
    "late": "late",
}
_START_RESULT_ALIASES = {
    "idle accepted": "idle_accepted",
    "idle_accepted": "idle_accepted",
    "idle uncaptured short pulse": "idle_uncaptured",
    "idle_uncaptured": "idle_uncaptured",
    "active ignored": "active_ignored",
    "active_ignored": "active_ignored",
    "request/grant ignored": "req_gnt_ignored",
    "req_gnt_ignored": "req_gnt_ignored",
    "held-high single capture": "held_high_single",
    "held_high_single": "held_high_single",
}
_BUS_STATE_ALIASES = {
    "SYS_CTRL_IDLE": "IDLE",
    "IDLE": "IDLE",
    "NEW_FETCH": "NEW_FETCH",
    "FETCH": "FETCH",
    "NEW_OP": "NEW_OP",
    "READ": "READ",
    "WRITE": "WRITE",
    "UPDATE": "UPDATE",
    "STALL": "STALL",
}
_RESET_PHASE_ALIASES = {
    "idle/pad": "idle_pad",
    "idle_pad": "idle_pad",
    "command": "command",
    "address": "address",
    "wait": "wait",
    "read data": "read_data",
    "read_data": "read_data",
    "write data": "write_data",
    "write_data": "write_data",
    "termination": "termination",
}

_POINT_BINS = {
    COV_LEN: set(LEN_BINS),
    COV_CHUNK: set(CHUNK_BINS),
    COV_DEVICE: set(DEVICE_TUPLES),
    COV_NEXTDEV: set(DEVICE_TUPLES),
    COV_CHAINLEN: set(CHAINLEN_BINS),
    COV_END: set(END_BINS),
    COV_ADDR: set(ADDR_BINS),
    COV_DATA: set(DATA_BINS),
    COV_CTRL_STATE: set(CTRL_STATE_BINS),
    COV_QPI_PHASE: set(QPI_PHASE_BINS),
    COV_BUS_STATE: set(BUS_STATE_BINS),
    COV_BUS_PHASE: set(BUS_PHASE_BINS),
    COV_BUS_RESUME: set(BUS_RESUME_BINS),
    COV_START_PHASE: set(START_PHASE_BINS),
    COV_START_RESULT: set(START_RESULT_BINS),
    COV_RESET_STATE: set(CTRL_STATE_BINS),
    COV_RESET_PHASE: set(RESET_PHASE_BINS),
    COV_DEPTH: {str(depth) for depth in DEPTH_BINS},
}


class CoverageError(ValueError):
    """Illegal coverage sample, unknown ``COV-*`` ID, or missing ``RUN_DIR``."""


def device_tuple(src_device: int, dest_device: int) -> str:
    """Return the ``COV-DEVICE`` / ``COV-NEXTDEV`` bin ``{src}x{dest}``."""
    return f"{int(src_device)}x{int(dest_device)}"


def length_corner_value(name: str, depth: int) -> int:
    """Numeric transfer length for a named ``COV-LEN`` corner at *depth*."""
    values = {
        "0": 0,
        "1": 1,
        "255": 255,
        "N-1": depth - 1,
        "N": depth,
        "N+1": depth + 1,
        "2N-1": 2 * depth - 1,
        "2N": 2 * depth,
        "2N+1": 2 * depth + 1,
    }
    try:
        return values[name]
    except KeyError as error:
        raise CoverageError(f"unknown COV-LEN corner {name!r}") from error


def classify_length(length: int, depth: int) -> str:
    """Return the canonical ``COV-LEN`` bin for *length* at *depth*.

    When two corner names share a numeric value (parameter collapse), the
    earlier name in :data:`LEN_CORNER_NAMES` owns the hit; the later name is
    an exclusion, not a second hit.
    """
    if isinstance(length, bool) or not isinstance(length, int):
        raise CoverageError(f"transfer length must be an int, got {length!r}")
    if not 0 <= length <= 255:
        raise CoverageError(f"transfer length {length} is outside 0..255")
    matches = []
    for name in LEN_CORNER_NAMES:
        value = length_corner_value(name, depth)
        if 0 <= value <= 255 and value == length:
            matches.append(name)
    return matches[0] if matches else LEN_MIDDLE


def applicable_len_bins(depth: int) -> "tuple[str, ...]":
    """Distinct ``COV-LEN`` bins that exist at *depth*, including ``middle``."""
    seen: "set[int]" = set()
    names = []
    for name in LEN_CORNER_NAMES:
        value = length_corner_value(name, depth)
        if not 0 <= value <= 255 or value in seen:
            continue
        seen.add(value)
        names.append(name)
    names.append(LEN_MIDDLE)
    return tuple(names)


def collapsed_len_bins(depth: int) -> "tuple[tuple[str, int, str], ...]":
    """Return ``(name, value, owner)`` for ``COV-LEN`` corners that collapse at *depth*."""
    seen: "dict[int, str]" = {}
    collapsed = []
    for name in LEN_CORNER_NAMES:
        value = length_corner_value(name, depth)
        if not 0 <= value <= 255:
            collapsed.append((name, value, "out_of_range"))
            continue
        owner = seen.get(value)
        if owner is not None:
            collapsed.append((name, value, owner))
            continue
        seen[value] = name
    return tuple(collapsed)


def classify_chunks(transfer_len: int, depth: int) -> "tuple[str, ...]":
    """Return ``COV-CHUNK`` bins for the sequential ``k=min(N, remaining)`` walk."""
    if transfer_len <= 0:
        return ()
    bins = []
    remaining = transfer_len
    first = True
    while remaining > 0:
        chunk = min(depth, remaining)
        remaining -= chunk
        last = remaining == 0
        if first and last:
            bins.append(CHUNK_ONLY)
        elif first:
            bins.append(CHUNK_FIRST_FULL)
        elif last and chunk == depth:
            bins.append(CHUNK_FINAL_FULL)
        elif last:
            bins.append(CHUNK_FINAL_PARTIAL)
        else:
            bins.append(CHUNK_MIDDLE_FULL)
        first = False
    return tuple(bins)


def end_bin(tcd, depth: int) -> str:
    """Return the ``COV-END`` bin using the run's ``DMA_BUF_DEPTH``."""
    if tcd.quit:
        return END_QUIT
    if tcd.transfer_len == 0:
        return END_ZERO
    chunks = (tcd.transfer_len + depth - 1) // depth if tcd.transfer_len else 0
    if chunks <= 1:
        return END_ONE
    return END_MULTI


def classify_address(address: int) -> "tuple[str, ...]":
    """Return every ``COV-ADDR`` class *address* belongs to (overlapping bins)."""
    if isinstance(address, bool) or not isinstance(address, int):
        raise CoverageError(f"address must be an int, got {address!r}")
    if address < 0:
        raise CoverageError(f"address 0x{address:X} is outside 0x000000..0x{ADDR_MAX:06X}")
    # D35: ptr[23] is don't-care; COV-ADDR bins classify A[22:0] only.
    address = address & ADDR_MAX
    hits = []
    if address == 0:
        hits.append(ADDR_ZERO)
    if 0 < address < BOUNDARY_64K:
        hits.append(ADDR_BELOW_64K)
    if address >= BOUNDARY_64K:
        hits.append(ADDR_AT_OR_ABOVE_64K)
    offset = address % PAGE_SIZE
    if offset <= PAGE_EDGE_NEIGHBORHOOD or (PAGE_SIZE - offset) <= PAGE_EDGE_NEIGHBORHOOD:
        hits.append(ADDR_PAGE_EDGE)
    if address >= ADDR_MAX - HIGHEST_WINDOW + 1:
        hits.append(ADDR_HIGHEST)
    return tuple(hits)


def classify_payload(data: bytes) -> "str | None":
    """Return the ``COV-DATA`` bin for *data*, or ``None`` when there is no payload."""
    payload = bytes(data)
    if not payload:
        return None
    if all(byte == 0x00 for byte in payload):
        return DATA_ZERO
    if all(byte == 0xFF for byte in payload):
        return DATA_ONES
    if all(payload[index] == (1 << (index % 8)) for index in range(len(payload))):
        return DATA_WALK_ALT
    if (
        len(payload) >= 2
        and payload[0] != payload[1]
        and all(payload[index] == payload[index % 2] for index in range(len(payload)))
    ):
        return DATA_WALK_ALT
    if len(payload) >= 2 and all(
        payload[index] == (payload[0] + index) & 0xFF for index in range(len(payload))
    ):
        return DATA_INCREMENT
    if len(payload) >= 2 and all(
        payload[index] == (index + 1) & 0xFF for index in range(len(payload))
    ):
        return DATA_INCREMENT
    return DATA_RANDOM


def chainlen_bin(count: int) -> str:
    """Return the ``COV-CHAINLEN`` bin for an executable-TCD count."""
    if count <= 0:
        return "0"
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    return "3+"


def normalize_observation(cov_id: str, bin_name: str) -> str:
    """Map aliases to the canonical bin for *cov_id* and reject unknowns."""
    if cov_id not in _POINT_BINS and cov_id not in (COV_DEPTH_LEN, COV_DEPTH_DEVICE):
        raise CoverageError(f"unknown coverage id {cov_id!r}")
    name = str(bin_name)
    if cov_id == COV_START_PHASE:
        name = _START_PHASE_ALIASES.get(name, name)
    elif cov_id == COV_START_RESULT:
        name = _START_RESULT_ALIASES.get(name, name)
    elif cov_id == COV_BUS_STATE:
        name = _BUS_STATE_ALIASES.get(name, name)
    elif cov_id == COV_RESET_PHASE:
        name = _RESET_PHASE_ALIASES.get(name, name)
    elif cov_id == COV_BUS_PHASE:
        name = {
            "read data": "read_data",
            "write data": "write_data",
        }.get(name, name)
    if cov_id == COV_DEPTH:
        name = str(int(name)) if str(name).isdigit() else name
    if cov_id in _POINT_BINS and name not in _POINT_BINS[cov_id]:
        if cov_id == COV_BUS_STATE and name == "STALL":
            return "STALL"
        raise CoverageError(f"{cov_id} has no bin {bin_name!r} (canonical {name!r})")
    if cov_id == COV_DEPTH_LEN:
        _check_depth_len_bin(name)
    if cov_id == COV_DEPTH_DEVICE:
        _check_depth_device_bin(name)
    return name


def _check_depth_len_bin(name: str) -> None:
    if ":" not in name:
        raise CoverageError(f"{COV_DEPTH_LEN} bin {name!r} must be depth:len_class")
    depth_text, length_name = name.split(":", 1)
    if depth_text not in _POINT_BINS[COV_DEPTH] or length_name not in LEN_BINS:
        raise CoverageError(f"{COV_DEPTH_LEN} has no bin {name!r}")


def _check_depth_device_bin(name: str) -> None:
    if ":" not in name:
        raise CoverageError(f"{COV_DEPTH_DEVICE} bin {name!r} must be depth:srcxdest")
    depth_text, tuple_name = name.split(":", 1)
    if depth_text not in _POINT_BINS[COV_DEPTH] or tuple_name not in DEVICE_TUPLES:
        raise CoverageError(f"{COV_DEPTH_DEVICE} has no bin {name!r}")


def structural_exclusions(
    depth: int,
    *,
    level: str = "L1",
    sim: str = "*",
) -> "tuple[dict, ...]":
    """Return recorded (not silent) exclusions for *depth*.

    Always includes ``COV-BUS-STATE`` / ``STALL`` (fresh BUS_REQ cannot land
    in STALL because reaching STALL already requires the synchronized request).
    Includes ``COV-LEN`` / ``N-1`` when ``N-1`` collapses onto another corner
    (the catalog example is ``N-1=0`` at depth 1), plus any other length-class
    collapse at this depth, and the matching ``COV-DEPTH-LEN`` crosses.
    """
    records = [
        _exclusion(
            COV_BUS_STATE,
            "STALL",
            reason=(
                "STALL is not a fresh BUS_REQ assertion state; reaching STALL "
                "already requires the synchronized request"
            ),
            unreachability=(
                "sys_control_state_t STALL is entered only after synchronized "
                "BUS_REQ is already high, so a new assertion cannot be sampled "
                "as a STALL crossing"
            ),
            level=level,
            sim=sim,
            depth="*",
            expiration=(
                "If the architecture allows entering STALL without an "
                "already-asserted synchronized BUS_REQ"
            ),
            reviewer=EXCLUSION_REVIEWER,
            date=EXCLUSION_DATE,
        )
    ]
    for name, value, owner in collapsed_len_bins(depth):
        if owner == "out_of_range":
            reason = (
                f"{name}={value} is outside TRANSFER_LEN 0..255 at "
                f"DMA_BUF_DEPTH={depth}"
            )
            note = f"{name} is not a legal transfer length at this depth"
        else:
            reason = (
                f"{name}={value} duplicates the {owner} length-class bin at "
                f"depth {depth}"
            )
            note = (
                f"At DMA_BUF_DEPTH={depth}, {name} evaluates to {value}, "
                f"which is already owned by {owner}"
            )
        records.append(
            _exclusion(
                COV_LEN,
                name,
                reason=reason,
                unreachability=note,
                level=level,
                sim=sim,
                depth=depth,
                expiration=(
                    "If DMA_BUF_DEPTH for this run changes so the corner is "
                    "distinct and in 0..255"
                ),
                reviewer=EXCLUSION_REVIEWER,
                date=EXCLUSION_DATE,
            )
        )
        records.append(
            _exclusion(
                COV_DEPTH_LEN,
                f"{depth}:{name}",
                reason=reason,
                unreachability=note,
                level=level,
                sim=sim,
                depth=depth,
                expiration=(
                    "If DMA_BUF_DEPTH for this run changes so the corner is "
                    "distinct and in 0..255"
                ),
                reviewer=EXCLUSION_REVIEWER,
                date=EXCLUSION_DATE,
            )
        )
    return tuple(records)


def _exclusion(
    cov_id: str,
    bin_name: str,
    *,
    reason: str,
    unreachability: str,
    level: str,
    sim: str,
    depth,
    expiration: str,
    reviewer: str = EXCLUSION_REVIEWER,
    date: str = EXCLUSION_DATE,
    architecture_citation: str = EXCLUSION_CITATION,
) -> dict:
    return {
        "id": cov_id,
        "bin": bin_name,
        "reason": reason,
        "architecture_citation": architecture_citation,
        "level": level,
        "sim": sim,
        "depth": depth,
        "unreachability": unreachability,
        "reviewer": reviewer,
        "date": date,
        "expiration": expiration,
    }


def required_bins(depths=DEPTH_BINS) -> "dict[str, tuple[str, ...]]":
    """Return required bins / crosses for closure across *depths*."""
    depths = tuple(sorted({int(depth) for depth in depths}))
    len_names: "set[str]" = set()
    depth_len = []
    depth_device = []
    for depth in depths:
        for name in applicable_len_bins(depth):
            len_names.add(name)
            depth_len.append(f"{depth}:{name}")
        for tuple_name in DEVICE_TUPLES:
            depth_device.append(f"{depth}:{tuple_name}")
    return {
        COV_LEN: tuple(name for name in LEN_BINS if name in len_names),
        COV_CHUNK: CHUNK_BINS,
        COV_DEVICE: DEVICE_TUPLES,
        COV_NEXTDEV: DEVICE_TUPLES,
        COV_CHAINLEN: CHAINLEN_BINS,
        COV_END: END_BINS,
        COV_ADDR: ADDR_BINS,
        COV_DATA: DATA_BINS,
        COV_CTRL_STATE: CTRL_STATE_BINS,
        COV_QPI_PHASE: QPI_PHASE_BINS,
        COV_BUS_STATE: BUS_STATE_BINS,
        COV_BUS_PHASE: BUS_PHASE_BINS,
        COV_BUS_RESUME: BUS_RESUME_BINS,
        COV_START_PHASE: START_PHASE_BINS,
        COV_START_RESULT: START_RESULT_BINS,
        COV_RESET_STATE: CTRL_STATE_BINS,
        COV_RESET_PHASE: RESET_PHASE_BINS,
        COV_DEPTH: tuple(str(depth) for depth in DEPTH_BINS),
        COV_DEPTH_LEN: tuple(depth_len),
        COV_DEPTH_DEVICE: tuple(depth_device),
    }


def fragment_path(config: dict, *, filename: str = FRAGMENT_FILENAME) -> str:
    """Return ``RUN_DIR`` / *filename* using :func:`common.artifacts.run_dir`."""
    from common.artifacts import run_dir as resolve_run_dir

    return os.path.join(resolve_run_dir(config), filename)


@dataclass
class WindowRecord:
    """One commit_window outcome (counted when checkers pass and scoreboard is ok or na)."""

    index: int
    checkers_ok: bool
    scoreboard_ok: bool
    scoreboard_na: bool = False

    @property
    def counted(self) -> bool:
        if not self.checkers_ok:
            return False
        if self.scoreboard_na:
            return True
        return self.scoreboard_ok

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "checkers_ok": self.checkers_ok,
            "scoreboard_ok": self.scoreboard_ok,
            "scoreboard_na": self.scoreboard_na,
            "counted": self.counted,
        }


class CoverageSampler:
    """Accumulate ``COV-*`` hits for one run and emit a ``RUN_DIR`` fragment.

    Observations and golden-chain events land in a pending window. They become
    hits only in :meth:`commit_window` when *checkers_ok* and *scoreboard_ok*
    are both true. A failing window is retained as a rejected window, not as
    silent coverage.
    """

    def __init__(
        self,
        dma_buf_depth: int = 1,
        *,
        context: "RunContext | None" = None,
        run_dir: "str | None" = None,
        config: "dict | None" = None,
    ) -> None:
        depth = int(dma_buf_depth)
        if depth not in DEPTH_BINS:
            raise CoverageError(
                f"dma_buf_depth={dma_buf_depth} is outside the harness range "
                f"1..{DMA_BUF_DEPTH_MAX}"
            )
        self.dma_buf_depth = depth
        self.config = dict(config or {})
        self.context = context or RunContext(
            level=str(self.config.get("dut_level", self.config.get("level", "L1"))),
            sim=str(self.config.get("sim", "")),
            seed=self.config.get("seed"),
            depth=depth,
            timing=str(self.config.get("timing_profile", "ideal")),
            test=str(self.config.get("test", "")),
        )
        self.run_dir = run_dir or self.config.get("run_dir") or None
        self._pending: "dict[str, dict[str, int]]" = defaultdict(lambda: defaultdict(int))
        self._hits: "dict[str, dict[str, int]]" = defaultdict(lambda: defaultdict(int))
        self._windows: "list[WindowRecord]" = []
        self._absorbed_paths: "set[str]" = set()
        self._sticky_failed = False
        self.exclusions = list(
            structural_exclusions(
                depth,
                level=self.context.level,
                sim=self.context.sim or "*",
            )
        )

    @classmethod
    def from_config(cls, config: dict) -> "CoverageSampler":
        """Build a sampler from a :func:`common.config.parse_run_config` mapping."""
        depth = int(config.get("dma_buf_depth", 1))
        dest = config.get("run_dir") or None
        if not dest:
            from common.artifacts import run_dir as resolve_run_dir

            dest = resolve_run_dir(config)
        context = RunContext(
            level=str(config.get("dut_level", "L1")),
            sim=str(config.get("sim", "")),
            seed=config.get("seed"),
            depth=depth,
            timing=str(config.get("timing_profile", "ideal")),
            test=str(config.get("test", "")),
        )
        return cls(depth, context=context, run_dir=dest, config=config)

    @property
    def hits(self) -> "dict[str, dict[str, int]]":
        """Committed hits only (failing windows are omitted)."""
        return {cov_id: dict(bins) for cov_id, bins in self._hits.items() if bins}

    def pending_hits(self) -> "dict[str, dict[str, int]]":
        return {cov_id: dict(bins) for cov_id, bins in self._pending.items() if bins}

    def _add(self, cov_id: str, bin_name: str) -> None:
        name = normalize_observation(cov_id, bin_name)
        if cov_id == COV_BUS_STATE and name == "STALL":
            return
        self._pending[cov_id][name] += 1

    def record_observation(self, cov_id: str, bin_name: str) -> None:
        """Queue one hierarchy / host bin into the current (uncommitted) window."""
        self._add(cov_id, bin_name)

    def record_chain(self, result: ChainResult, *, generated=None) -> None:
        """Classify golden-chain events into the current window.

        *generated* is accepted so Wave 2 can pass a
        :class:`~reference.generator.GeneratedChain`; classification uses
        *result* (the oracle). Depth must match this sampler's compile-time
        ``DMA_BUF_DEPTH``.
        """
        if not isinstance(result, ChainResult):
            raise CoverageError(
                f"record_chain expected ChainResult, got {type(result).__name__}"
            )
        if result.dma_buf_depth != self.dma_buf_depth:
            raise CoverageError(
                f"ChainResult.dma_buf_depth={result.dma_buf_depth} does not "
                f"match sampler depth {self.dma_buf_depth}"
            )
        if generated is not None and getattr(generated, "dma_buf_depth", self.dma_buf_depth) not in (
            None,
            self.dma_buf_depth,
        ):
            raise CoverageError(
                f"generated.dma_buf_depth={generated.dma_buf_depth} does not "
                f"match sampler depth {self.dma_buf_depth}"
            )
        depth = self.dma_buf_depth
        self._add(COV_DEPTH, str(depth))

        executable = tuple(item for item in result.descriptors if not item.tcd.quit)
        self._add(COV_CHAINLEN, chainlen_bin(len(executable)))

        for item in result.descriptors:
            self._add(COV_END, end_bin(item.tcd, depth))
            if item.tcd.quit:
                continue
            length_name = classify_length(item.tcd.transfer_len, depth)
            self._add(COV_LEN, length_name)
            self._add(COV_DEPTH_LEN, f"{depth}:{length_name}")
            tuple_name = device_tuple(item.tcd.src_device, item.tcd.dest_device)
            self._add(COV_DEVICE, tuple_name)
            self._add(COV_DEPTH_DEVICE, f"{depth}:{tuple_name}")
            for chunk_name in classify_chunks(item.tcd.transfer_len, depth):
                self._add(COV_CHUNK, chunk_name)
            for role, address in (
                ("src", item.tcd.src_ptr & ADDR_MAX),
                ("dest", item.tcd.dest_ptr & ADDR_MAX),
                ("next", item.tcd.next_tcd & ADDR_MAX),
            ):
                for cls in classify_address(address):
                    self._add(COV_ADDR, f"{role}:{cls}")
            if item.tcd.transfer_len > 0:
                payload = result.initial_memory.read(
                    item.tcd.src_device,
                    item.tcd.src_ptr & ADDR_MAX,
                    item.tcd.transfer_len,
                )
                pattern = classify_payload(payload)
                if pattern is not None:
                    self._add(COV_DATA, pattern)

        for index in range(len(result.path) - 1):
            current_device, _ = result.path[index]
            next_device, _ = result.path[index + 1]
            self._add(COV_NEXTDEV, device_tuple(current_device, next_device))

    def record_compare(self, result: ChainResult, *, generated=None, scoreboard=None) -> None:
        """Record golden events after a dual-axis scoreboard compare.

        *scoreboard* is accepted for the Wave 2 call site; it does not commit
        hits. Call :meth:`commit_window` with ``scoreboard_ok`` from that compare.
        """
        if scoreboard is not None and getattr(scoreboard, "context", None) is not None:
            if self.context.test == "" and scoreboard.context.test:
                self.context = scoreboard.context
        self.record_chain(result, generated=generated)

    def begin_window(self, *, test: "str | None" = None) -> None:
        """Scope pending hits to *test*; leftover pending becomes a sticky failed window."""
        if test is not None and test != self.context.test:
            if self.pending_hits():
                self.commit_window(checkers_ok=False, scoreboard_ok=False)
            self.context = replace(self.context, test=test)

    def commit_window(
        self,
        *,
        checkers_ok: bool,
        scoreboard_ok: bool = False,
        scoreboard_na: bool = False,
    ) -> bool:
        """Promote pending hits iff checkers pass and scoreboard is ok or na.

        Always clears pending. A failing window stays in ``windows`` (sticky)
        and does not count hits. ``scoreboard_na`` is for windows with no
        golden compare (injection/reset); do not pass ``scoreboard_ok=True``
        without a compare.
        """
        if scoreboard_ok and scoreboard_na:
            raise CoverageError("commit_window cannot set both scoreboard_ok and scoreboard_na")
        record = WindowRecord(
            index=len(self._windows),
            checkers_ok=bool(checkers_ok),
            scoreboard_ok=bool(scoreboard_ok),
            scoreboard_na=bool(scoreboard_na),
        )
        self._windows.append(record)
        if not record.counted:
            self._sticky_failed = True
        if record.counted:
            for cov_id, bins in self._pending.items():
                for name, count in bins.items():
                    self._hits[cov_id][name] += count
        self._pending = defaultdict(lambda: defaultdict(int))
        return record.counted

    def record_passing(
        self,
        result: ChainResult,
        *,
        generated=None,
        checkers_ok: bool = True,
        scoreboard_ok: bool = True,
    ) -> bool:
        """Record *result* and commit in one call (unit tests and simple Wave 2)."""
        self.record_chain(result, generated=generated)
        return self.commit_window(checkers_ok=checkers_ok, scoreboard_ok=scoreboard_ok)

    def fragment(self) -> dict:
        """Return the JSON-able per-run fragment (committed hits only)."""
        return {
            "schema": FRAGMENT_SCHEMA,
            "run": {
                "level": self.context.level,
                "sim": self.context.sim,
                "seed": self.context.seed,
                "depth": self.dma_buf_depth,
                "timing": self.context.timing,
                "test": self.context.test,
                "run_dir": self.run_dir or "",
            },
            "hits": self.hits,
            "exclusions": list(self.exclusions),
            "windows": [window.as_dict() for window in self._windows],
            "uncommitted_pending": bool(self.pending_hits()),
        }

    def write_fragment(
        self, run_dir: "str | None" = None, *, filename: str = FRAGMENT_FILENAME
    ) -> str:
        """Write the fragment JSON under *run_dir* (``RUN_DIR``) and return its path."""
        dest = run_dir or self.run_dir
        if not dest:
            if self.config:
                dest = os.path.dirname(fragment_path(self.config, filename=filename)) or None
            if not dest:
                raise CoverageError(
                    "write_fragment needs run_dir (RUN_DIR) or CoverageSampler(run_dir=...)"
                )
        os.makedirs(dest, exist_ok=True)
        path = os.path.join(dest, filename)
        payload = self.fragment()
        payload["run"]["run_dir"] = dest
        if os.path.isfile(path):
            existing = load_fragment(path)
            self._check_fragment_owner(existing)
            abs_path = os.path.abspath(path)
            if abs_path not in self._absorbed_paths:
                self.absorb_fragment(path)
                payload = self.fragment()
                payload["run"]["run_dir"] = dest
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        self.run_dir = dest
        self._absorbed_paths.add(os.path.abspath(path))
        return path

    def _check_fragment_owner(self, payload: dict) -> None:
        run = payload.get("run") or {}
        disk_depth = run.get("depth")
        if disk_depth is not None and int(disk_depth) != self.dma_buf_depth:
            raise CoverageError(
                f"coverage.json owned by depth {disk_depth}, this run is "
                f"{self.dma_buf_depth}"
            )
        disk_seed = run.get("seed")
        if (
            disk_seed not in (None, "")
            and self.context.seed not in (None, "")
            and str(disk_seed) != str(self.context.seed)
        ):
            raise CoverageError(
                f"coverage.json owned by seed {disk_seed}, this run is "
                f"{self.context.seed}"
            )
        disk_sim = run.get("sim")
        if (
            disk_sim not in (None, "")
            and self.context.sim not in (None, "")
            and str(disk_sim) != str(self.context.sim)
        ):
            raise CoverageError(
                f"coverage.json owned by sim {disk_sim}, this run is "
                f"{self.context.sim}"
            )

    def absorb_fragment(self, path: str) -> None:
        """Merge a previously written fragment so a later process can append hits.

        Directed, reset/bus, and hole-fill suites share one ``RUN_DIR`` per
        compile. A fresh process must load the retained fragment before adding
        windows; otherwise ``write_fragment`` would overwrite earlier hits.
        Does not touch the current pending window.
        """
        payload = load_fragment(path)
        run_depth = payload.get("run", {}).get("depth")
        if run_depth is not None and int(run_depth) != self.dma_buf_depth:
            raise CoverageError(
                f"absorb_fragment depth {run_depth} does not match sampler "
                f"depth {self.dma_buf_depth}"
            )
        for cov_id, bins in (payload.get("hits") or {}).items():
            for name, count in bins.items():
                if not isinstance(count, int) or count < 0:
                    raise CoverageError(
                        f"illegal hit count {count!r} for {cov_id}/{name}"
                    )
                self._hits[cov_id][name] += count
        for window in payload.get("windows") or []:
            self._windows.append(
                WindowRecord(
                    index=len(self._windows),
                    checkers_ok=bool(window.get("checkers_ok")),
                    scoreboard_ok=bool(window.get("scoreboard_ok")),
                    scoreboard_na=bool(window.get("scoreboard_na", False)),
                )
            )
        seen = {_exclusion_key(record) for record in self.exclusions}
        for record in payload.get("exclusions") or []:
            key = _exclusion_key(record)
            if key in seen:
                continue
            seen.add(key)
            self.exclusions.append(record)
        self._absorbed_paths.add(os.path.abspath(path))


@dataclass
class CoverageReport:
    """Closure report regenerated from retained fragments (never hand-edited)."""

    schema: str = CLOSURE_SCHEMA
    hits: dict = field(default_factory=dict)
    exclusions: list = field(default_factory=list)
    required: dict = field(default_factory=dict)
    missing: dict = field(default_factory=dict)
    fragments: list = field(default_factory=list)
    windows_counted: int = 0
    windows_rejected: int = 0
    depths: list = field(default_factory=list)
    closed: bool = False

    def as_dict(self) -> dict:
        return {
            "schema": self.schema,
            "closed": self.closed,
            "hits": self.hits,
            "required": self.required,
            "missing": self.missing,
            "exclusions": self.exclusions,
            "fragments": self.fragments,
            "windows_counted": self.windows_counted,
            "windows_rejected": self.windows_rejected,
            "depths": self.depths,
        }


def find_fragments(root: str, *, filename: str = FRAGMENT_FILENAME) -> "list[str]":
    """Return sorted paths of *filename* under *root* (file or directory)."""
    if os.path.isfile(root):
        return [root]
    found = []
    for dirpath, _, files in os.walk(root):
        if filename in files:
            found.append(os.path.join(dirpath, filename))
    return sorted(found)


def load_fragment(path: str) -> dict:
    """Load one fragment JSON and reject unknown or hand-broken schemas."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema") != FRAGMENT_SCHEMA:
        raise CoverageError(
            f"{path} schema {payload.get('schema')!r} is not {FRAGMENT_SCHEMA}"
        )
    if not isinstance(payload.get("hits"), dict):
        raise CoverageError(f"{path} is missing a hits object")
    return payload


def _merge_hits(accumulator: dict, hits: dict) -> None:
    for cov_id, bins in hits.items():
        bucket = accumulator.setdefault(cov_id, {})
        for name, count in bins.items():
            if not isinstance(count, int) or count < 0:
                raise CoverageError(f"illegal hit count {count!r} for {cov_id}/{name}")
            bucket[name] = bucket.get(name, 0) + count


def _exclusion_key(record: dict) -> tuple:
    """Identity for recorded exclusions: ``(id, bin, depth)``.

    Depth ``*`` is the only global wildcard. A depth-1 ``N-1`` exclusion
    must not hide a depth-5 ``N-1`` requirement.
    """
    return (record.get("id"), str(record.get("bin")), record.get("depth"))


def _bin_is_excluded(exclusions: "set[tuple]", cov_id: str, name: str, depths) -> bool:
    """Return True when *name* is excluded for every depth that still requires it."""
    key_star = (cov_id, str(name), "*")
    if key_star in exclusions:
        return True
    if cov_id == COV_DEPTH:
        try:
            depth = int(name)
        except (TypeError, ValueError):
            return False
        return (cov_id, str(name), depth) in exclusions
    if cov_id in (COV_DEPTH_LEN, COV_DEPTH_DEVICE):
        try:
            depth = int(str(name).split(":", 1)[0])
        except (TypeError, ValueError):
            return False
        return (cov_id, str(name), depth) in exclusions
    needing = []
    for depth in depths:
        if cov_id == COV_LEN:
            if name in applicable_len_bins(depth):
                needing.append(depth)
        else:
            needing.append(depth)
    if not needing:
        return True
    return all((cov_id, str(name), depth) in exclusions for depth in needing)


def aggregate_fragments(sources) -> CoverageReport:
    """Sum hits from retained fragments and compute missing required bins."""
    if isinstance(sources, (str, os.PathLike)):
        paths = find_fragments(str(sources))
    else:
        paths = []
        for source in sources:
            paths.extend(find_fragments(str(source)))
    paths = sorted(set(paths))
    if not paths:
        raise CoverageError("aggregate_fragments found no coverage fragments")

    hits: dict = {}
    exclusions = []
    seen_exclusions: "set[tuple]" = set()
    depths: "set[int]" = set()
    counted = 0
    rejected = 0
    for path in paths:
        payload = load_fragment(path)
        _merge_hits(hits, payload.get("hits") or {})
        run_depth = payload.get("run", {}).get("depth")
        if run_depth is not None:
            depths.add(int(run_depth))
        for record in payload.get("exclusions") or []:
            key = _exclusion_key(record)
            if key in seen_exclusions:
                continue
            seen_exclusions.add(key)
            exclusions.append(record)
        for window in payload.get("windows") or []:
            if window.get("counted"):
                counted += 1
            else:
                rejected += 1

    required = {
        cov_id: list(bins) for cov_id, bins in required_bins(depths or DEPTH_BINS).items()
    }
    # COV-DEPTH always requires the full harness range 1..MAX, including tapeout 5.
    required[COV_DEPTH] = [str(depth) for depth in DEPTH_BINS]
    required[COV_DEPTH_LEN] = list(required_bins(DEPTH_BINS)[COV_DEPTH_LEN])
    required[COV_DEPTH_DEVICE] = list(required_bins(DEPTH_BINS)[COV_DEPTH_DEVICE])

    # Refresh reviewer/date from current structural exclusions so closure
    # regeneration does not retain empty policy fields from older fragments.
    structural_policy: "dict[tuple, dict]" = {}
    for depth in sorted(depths) or list(DEPTH_BINS):
        for record in structural_exclusions(depth):
            structural_policy[_exclusion_key(record)] = record
    for record in exclusions:
        policy = structural_policy.get(_exclusion_key(record))
        if policy is None:
            continue
        record["reviewer"] = policy["reviewer"]
        record["date"] = policy["date"]

    excluded_bins = {_exclusion_key(record) for record in exclusions}
    missing = {}
    merged_depths = sorted(depths) or list(DEPTH_BINS)
    for cov_id, bins in required.items():
        absent = []
        for name in bins:
            if _bin_is_excluded(excluded_bins, cov_id, str(name), merged_depths):
                continue
            if hits.get(cov_id, {}).get(str(name), 0) <= 0:
                absent.append(name)
        if absent:
            missing[cov_id] = absent

    return CoverageReport(
        hits=hits,
        exclusions=exclusions,
        required=required,
        missing=missing,
        fragments=paths,
        windows_counted=counted,
        windows_rejected=rejected,
        depths=sorted(depths),
        closed=not missing,
    )


def write_closure_report(report: CoverageReport, path: str) -> str:
    """Write *report* as JSON. Counts come only from :func:`aggregate_fragments`."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report.as_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def regenerate_closure(sources, dest_path: str) -> CoverageReport:
    """Rebuild the closure report from retained fragments and write *dest_path*."""
    report = aggregate_fragments(sources)
    write_closure_report(report, dest_path)
    return report


__all__ = [
    "ALL_POINTS",
    "CHAIN_POINTS",
    "CLOSURE_FILENAME",
    "CLOSURE_SCHEMA",
    "CTRL_STATE_BINS",
    "COV_ADDR",
    "COV_BUS_PHASE",
    "COV_BUS_RESUME",
    "COV_BUS_STATE",
    "COV_CHAINLEN",
    "COV_CHUNK",
    "COV_CTRL_STATE",
    "COV_DATA",
    "COV_DEPTH",
    "COV_DEPTH_DEVICE",
    "COV_DEPTH_LEN",
    "COV_DEVICE",
    "COV_END",
    "COV_LEN",
    "COV_NEXTDEV",
    "COV_QPI_PHASE",
    "COV_RESET_PHASE",
    "COV_RESET_STATE",
    "COV_START_PHASE",
    "COV_START_RESULT",
    "DEPTH_BINS",
    "DMA_BUF_DEPTH_MAX",
    "EXCLUSION_CITATION",
    "EXCLUSION_DATE",
    "EXCLUSION_REVIEWER",
    "FRAGMENT_FILENAME",
    "FRAGMENT_SCHEMA",
    "L1_POINTS",
    "QPI_PHASE_BINS",
    "CoverageError",
    "CoverageReport",
    "CoverageSampler",
    "WindowRecord",
    "aggregate_fragments",
    "applicable_len_bins",
    "classify_address",
    "classify_chunks",
    "classify_length",
    "classify_payload",
    "collapsed_len_bins",
    "device_tuple",
    "find_fragments",
    "fragment_path",
    "load_fragment",
    "normalize_observation",
    "regenerate_closure",
    "required_bins",
    "structural_exclusions",
    "write_closure_report",
]
