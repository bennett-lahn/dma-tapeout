"""Verbose per-case reporting for HIL runs (`--detail`).

A green HIL run normally prints nothing, so it is no evidence of what actually
moved on the wire. `--detail` turns every directed and random case into a block
that shows the installed stimulus, the oracle's descriptor fetches and QPI
transaction log, an expected-versus-actual hex dump of the destination bytes,
the guard window, and the final host port state - on pass as well as on fail.

Nothing here asserts. `hil.checks` still owns pass / fail; this module only
renders what those checks compared. Every entry point is a no-op when the
reporter is disabled, so the default run pays no formatting cost.

Host-only module: `firmware/` must never import it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TEST = _REPO / "test"
for _path in (str(_REPO), str(_TEST)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from hil.checks import collapse_extents, format_address  # noqa: E402
from test.reference.chain import interpret_chain  # noqa: E402
from test.reference.constants import TCD_BYTES  # noqa: E402
from test.reference.tcd import decode_tcd, format_tcd  # noqa: E402

# Destination bytes rendered per case in the expected-versus-actual hex dump.
# 0 keeps the per-extent summary lines and drops the dump.
DEFAULT_DETAIL_BYTES = 256

# Bytes per hex-dump row. 16 keeps a row inside a normal terminal width.
BYTES_PER_LINE = 16

# Cap on descriptor and transaction rows so one 255-byte case cannot bury the
# rest of the run. The count line always reports the true total.
MAX_DETAIL_ROWS = 24

# Hex-dump cell for an address the oracle or the read-back has no byte for.
ABSENT_CELL = "--"


def _cell(value):
    return ABSENT_CELL if value is None else "%02X" % (int(value) & 0xFF)


class DetailReporter:
    """Renders one verbose block per HIL case when `--detail` is set.

    Args:
        enabled: False makes every method a no-op returning `()`.
        writer: callable taking the finished multi-line block. Defaults to
            `print`; the pytest fixture routes it through the terminal reporter
            so output survives capture.
        max_bytes: destination bytes to hex-dump per case (0 = summary only).
        target: `--target` name, shown in the header.
        seed: `--seed` value, shown in the header.
    """

    def __init__(
        self,
        *,
        enabled=False,
        writer=None,
        max_bytes=DEFAULT_DETAIL_BYTES,
        target=None,
        seed=None,
    ):
        self.enabled = bool(enabled)
        self.max_bytes = max(int(max_bytes), 0)
        self.target = target
        self.seed = seed
        self._writer = writer if writer is not None else print

    def report_case(
        self,
        case_id,
        *,
        name=None,
        markers=(),
        memory=None,
        result=None,
        expected=None,
        dumped=None,
        spans=None,
        status=None,
        guard=None,
        notes=(),
    ):
        """Render one case block and hand it to the writer.

        Every argument past `case_id` is optional; a section is skipped when
        its data is absent. `result` is an `interpret_chain` `ChainResult`; when
        omitted but `memory` is given, the oracle is re-run so the block can
        show descriptors and the transaction log.

        Returns:
            tuple[str, ...]: the emitted lines, or `()` when disabled.
        """
        if not self.enabled:
            return ()
        if result is None and memory is not None:
            result = interpret_chain(memory)
        if memory is None and result is not None:
            memory = getattr(result, "initial_memory", None)
        if expected is None and result is not None:
            expected = dict(result.expected_writes)

        lines = [self._header(case_id, name)]
        lines.extend(self._meta_lines(markers))
        lines.extend(self._stimulus_lines(spans))
        lines.extend(self._oracle_lines(result))
        lines.extend(self._compare_lines(expected, dumped))
        lines.extend(self._dump_lines(expected, dumped))
        lines.extend(self._guard_lines(guard))
        lines.extend(self._descriptor_lines(memory, result))
        lines.extend(self._transaction_lines(result))
        lines.extend(self._status_lines(status))
        lines.extend("  note: %s" % note for note in notes)
        self._writer("\n".join(lines))
        return tuple(lines)

    # --- sections --------------------------------------------------------

    def _header(self, case_id, name):
        title = str(case_id)
        if name:
            title += " (%s)" % name
        return "--- HIL detail: %s ---" % title

    def _meta_lines(self, markers):
        meta = []
        if self.target:
            meta.append("target=%s" % self.target)
        if self.seed is not None:
            meta.append("seed=%s" % self.seed)
        if markers:
            meta.append("markers=%s" % ",".join(str(m) for m in markers))
        return ["  " + "  ".join(meta)] if meta else []

    def _stimulus_lines(self, spans):
        if not spans:
            return []
        total = sum(len(bytes(data)) for _device, _address, data in spans)
        devices = sorted({int(device) for device, _address, _data in spans})
        return [
            "  stimulus: %d span(s), %d byte(s) installed on %s"
            % (len(spans), total, ", ".join("dev%d" % d for d in devices))
        ]

    def _oracle_lines(self, result):
        if result is None:
            return []
        return [
            "  oracle: %d transaction(s), %d descriptor fetch(es), "
            "%d expected write byte(s), completed=%s"
            % (
                len(getattr(result, "transactions", ()) or ()),
                len(getattr(result, "descriptors", ()) or ()),
                len(getattr(result, "expected_writes", {}) or {}),
                getattr(result, "completed", "?"),
            )
        ]

    def _compare_lines(self, expected, dumped):
        expected = dict(expected or {})
        observed = dict(dumped or {})
        if not expected and not observed:
            return ["  dest compare: no destination bytes (chain moved nothing)"]
        keys = set(expected) | set(observed)
        same = sum(1 for key in keys if expected.get(key) == observed.get(key))
        extents = collapse_extents(keys)
        lines = [
            "  dest compare: %d/%d byte(s) match across %d extent(s)"
            % (same, len(keys), len(extents))
        ]
        for device, address, length in extents:
            bad = [
                addr
                for addr in range(address, address + length)
                if expected.get((device, addr)) != observed.get((device, addr))
            ]
            verdict = (
                "match"
                if not bad
                else "%d byte(s) differ (first %s)"
                % (len(bad), format_address(bad[0]))
            )
            lines.append(
                "    dev%d %s +%d  %s"
                % (device, format_address(address), length, verdict)
            )
        return lines

    def _dump_lines(self, expected, dumped):
        expected = dict(expected or {})
        observed = dict(dumped or {})
        keys = set(expected) | set(observed)
        if not keys or self.max_bytes <= 0:
            return []

        lines = ["  memory compare (expected vs actual):"]
        budget = self.max_bytes
        shown = 0
        total = len(keys)
        for device, address, length in collapse_extents(keys):
            if budget <= 0:
                break
            take = min(length, budget)
            lines.append(
                "    dev%d %s..%s"
                % (
                    device,
                    format_address(address),
                    format_address(address + take - 1),
                )
            )
            for offset in range(0, take, BYTES_PER_LINE):
                row = range(
                    address + offset,
                    min(address + offset + BYTES_PER_LINE, address + take),
                )
                want = [expected.get((device, addr)) for addr in row]
                have = [observed.get((device, addr)) for addr in row]
                prefix = "      %s" % format_address(address + offset)
                pad = " " * len(prefix)
                lines.append(
                    "%s  expect  %s" % (prefix, " ".join(_cell(v) for v in want))
                )
                lines.append(
                    "%s  actual  %s" % (pad, " ".join(_cell(v) for v in have))
                )
                if want != have:
                    marks = ["^^" if w != h else "  " for w, h in zip(want, have)]
                    lines.append(("%s  diff    %s" % (pad, " ".join(marks))).rstrip())
            budget -= take
            shown += take
        if shown < total:
            lines.append(
                "    ... %d more byte(s) not shown (raise --detail-bytes)"
                % (total - shown)
            )
        return lines

    def _guard_lines(self, guard):
        if guard is None:
            return []
        return [
            "  guards: %d byte(s) checked outside the destination extents"
            % len(guard)
        ]

    def _descriptor_lines(self, memory, result):
        if memory is None or result is None:
            return []
        slots = []
        for entry in getattr(result, "path", ()) or ():
            key = (int(entry[0]), int(entry[1]))
            if key not in slots:
                slots.append(key)
        if not slots:
            return []
        lines = ["  descriptors (fetch order, %d slot(s)):" % len(slots)]
        for device, address in slots[:MAX_DETAIL_ROWS]:
            raw = memory.read(device, address, TCD_BYTES)
            lines.append(
                "    dev%d %s  %s"
                % (device, format_address(address), format_tcd(decode_tcd(raw)))
            )
        if len(slots) > MAX_DETAIL_ROWS:
            lines.append("    ... %d more slot(s)" % (len(slots) - MAX_DETAIL_ROWS))
        return lines

    def _transaction_lines(self, result):
        transactions = tuple(getattr(result, "transactions", ()) or ())
        if not transactions:
            return []
        lines = ["  oracle transactions (%d):" % len(transactions)]
        for txn in transactions[:MAX_DETAIL_ROWS]:
            lines.append("    %s" % txn.canonical())
        if len(transactions) > MAX_DETAIL_ROWS:
            lines.append(
                "    ... %d more transaction(s)"
                % (len(transactions) - MAX_DETAIL_ROWS)
            )
        return lines

    def _status_lines(self, status):
        if not status:
            return []
        oe = status.get("oe")
        return [
            "  host: done=%s bus_gnt=%s bus_req=%s rst_n_low=%s oe=%s"
            % (
                status.get("done"),
                status.get("bus_gnt"),
                status.get("bus_req"),
                status.get("rst_n_low"),
                "0x%02X" % oe if isinstance(oe, int) else oe,
            )
        ]


def null_reporter():
    """A disabled reporter, for callers that always hold one."""
    return DetailReporter(enabled=False)


__all__ = [
    "BYTES_PER_LINE",
    "DEFAULT_DETAIL_BYTES",
    "MAX_DETAIL_ROWS",
    "DetailReporter",
    "null_reporter",
]
