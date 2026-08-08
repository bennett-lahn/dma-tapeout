"""Dual-axis scoreboard: ordered transactions and final memory images.

Supports normal quit completion, reset-interrupted epochs (``TC-RESET-ACTIVE``),
and repeated-run equality (``TC-RESET-REPEAT``). Pure Python only.

Both axes are required (``05-reference-model.md``). A test does not pass because
only the destination range matches:

* **Axis 1** compares the ordered transaction log record by record and stops
  semantic comparison at the first mismatch while retaining context. Records are
  never sorted by address or collapsed.
* **Axis 2** compares final memory over the union of expected-defined,
  observed-defined, written, read, and explicitly guarded addresses, and
  classifies each differing byte as ``missing_write``, ``wrong_data``, or
  ``unexpected_write``.

Public compare API (frozen for M2):

* :meth:`Scoreboard.compare_transactions`
* :meth:`Scoreboard.compare_memory`
* :meth:`Scoreboard.compare` (both axes, in that order)
* :meth:`Scoreboard.compare_reset_prefix`
* :meth:`Scoreboard.compare_reset_memory`
* :func:`compare_epoch_logs` / :meth:`Scoreboard.compare_epochs`
"""

from dataclasses import dataclass

from reference.chain import (
    DATA_READ,
    DATA_WRITE,
    FETCH_READ,
    OBSERVED_READ,
    OBSERVED_WRITE,
    ChainResult,
    MemoryImage,
    MemoryUndefinedError,
    Transaction,
    as_transactions,
    commit_prefix,
)
from reference.tcd import ReferenceModelError, format_bytes

AXIS_TRANSACTIONS = "transactions"
AXIS_MEMORY = "memory"
AXIS_REFERENCE = "reference"

CLASS_MISSING_WRITE = "missing_write"
CLASS_WRONG_DATA = "wrong_data"
CLASS_UNEXPECTED_WRITE = "unexpected_write"

MISSING = "<missing>"
UNDEFINED = "<undefined>"

CONTEXT_RECORDS = 3
MAX_REPORTED_BYTES = 16
HEX_WINDOW = 8


class ScoreboardError(AssertionError):
    """One scoreboard axis failed; the message carries the frozen report header."""

    def __init__(self, axis: str, message: str) -> None:
        super().__init__(message)
        self.axis = axis


@dataclass(frozen=True)
class RunContext:
    """Run dimensions printed in every scoreboard failure header."""

    level: str = "L1"
    sim: str = ""
    seed: "int | None" = None
    depth: int = 1
    timing: str = "ideal"
    test: str = ""
    repro: str = ""

    def header(self, axis: str, epoch, expected_count: int, observed_count: int) -> str:
        lines = [
            f"SCOREBOARD FAIL axis={axis}",
            f"level={self.level} sim={self.sim or '?'} seed="
            f"{'?' if self.seed is None else self.seed} depth={self.depth} "
            f"timing={self.timing}",
            f"epoch={epoch} expected_transactions={expected_count} "
            f"observed_transactions={observed_count}",
        ]
        if self.test:
            lines.append(f"test={self.test}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Region:
    """One labeled memory region for memory-axis diagnostics."""

    device: int
    address: int
    length: int
    kind: str = "region"

    def contains(self, device: int, address: int) -> bool:
        return device == self.device and self.address <= address < self.address + self.length

    def addresses(self) -> "set[int]":
        return set(range(self.address, self.address + self.length))


@dataclass
class ByteMismatch:
    """One differing memory byte with its classification."""

    device: int
    address: int
    expected: "int | None"
    observed: "int | None"
    classification: str
    region: str = ""

    def canonical(self) -> str:
        expected = UNDEFINED if self.expected is None else f"{self.expected:02X}"
        observed = UNDEFINED if self.observed is None else f"{self.observed:02X}"
        return (
            f"dev={self.device} addr=0x{self.address:06X} "
            f"expected={expected} observed={observed} class={self.classification}"
            + (f" region={self.region}" if self.region else "")
        )


def guard_region(device: int, address: int, length: int = 1) -> Region:
    """Return a guard :class:`Region` for the memory axis guard set."""
    return Region(device=device, address=address, length=length, kind="guard")


class _ObservedMemory:
    """Adapter over whatever a test hands the memory axis as observed storage.

    Accepted forms:

    * :class:`reference.chain.MemoryImage` (``read(device, address, length)``)
    * mapping ``{device: object}`` where the object exposes ``read(address,
      length)`` or ``byte(address)`` (the PSRAM model's ``PsramDevice``)
    * mapping ``{device: {address: byte}}``
    """

    def __init__(self, observed) -> None:
        self._image = None
        self._devices: "dict[int, object]" = {}
        self._maps: "dict[int, dict[int, int]]" = {}

        if isinstance(observed, MemoryImage):
            self._image = observed
            return
        if not isinstance(observed, dict):
            raise ReferenceModelError(
                f"observed memory {type(observed).__name__} is not a MemoryImage or "
                "a {device: model-or-mapping} mapping"
            )
        for device, value in observed.items():
            if isinstance(value, dict):
                self._maps[device] = dict(value)
            elif hasattr(value, "read") or hasattr(value, "byte"):
                self._devices[device] = value
            else:
                raise ReferenceModelError(
                    f"observed memory for device {device} is "
                    f"{type(value).__name__}; expected a mapping or a model with "
                    "read()/byte()"
                )

    def devices(self) -> "tuple[int, ...]":
        if self._image is not None:
            return tuple(self._image.devices)
        return tuple(sorted(set(self._devices) | set(self._maps)))

    def byte(self, device: int, address: int) -> "int | None":
        """Return the observed byte, or ``None`` when the source has no value."""
        if self._image is not None:
            try:
                return self._image.byte(device, address)
            except MemoryUndefinedError:
                return None
        if device in self._maps:
            return self._maps[device].get(address)
        model = self._devices.get(device)
        if model is None:
            return None
        if hasattr(model, "byte"):
            return int(model.byte(address))
        return int(model.read(address, 1)[0])

    def defined_addresses(self, device: int) -> "set[int]":
        """Return addresses the observed source can enumerate (may be empty)."""
        if self._image is not None:
            return self._image.defined_addresses(device)
        if device in self._maps:
            return set(self._maps[device])
        model = self._devices.get(device)
        snapshot = getattr(model, "snapshot", None)
        if snapshot is None:
            return set()
        return set(snapshot())


class Scoreboard:
    """Compare observed monitor records and memory against the golden oracle."""

    def __init__(
        self,
        expected_transactions,
        expected_memory: MemoryImage,
        *,
        initial_memory: "MemoryImage | None" = None,
        expected_writes: "dict | None" = None,
        read_addresses=(),
        guards=(),
        regions=(),
        context: "RunContext | None" = None,
        epoch: int = 0,
        log=None,
    ) -> None:
        self.expected_transactions = as_transactions(expected_transactions)
        self.expected_memory = expected_memory
        self.initial_memory = initial_memory
        self.expected_writes = dict(expected_writes or {})
        self.read_addresses = set(read_addresses)
        self.regions = tuple(regions)
        self.guards = tuple(
            entry if isinstance(entry, Region) else guard_region(*entry)
            for entry in guards
        )
        self.context = context or RunContext()
        self.epoch = epoch
        self.log = log
        self.mismatches: "list[ByteMismatch]" = []

    # -- construction ------------------------------------------------------

    @classmethod
    def from_result(
        cls,
        result: ChainResult,
        *,
        guards=(),
        regions=(),
        context: "RunContext | None" = None,
        epoch: int = 0,
        log=None,
    ) -> "Scoreboard":
        """Build a scoreboard from a :func:`reference.chain.interpret_chain` result."""
        resolved = context or RunContext(depth=result.dma_buf_depth)
        return cls(
            result.transactions,
            result.final_memory,
            initial_memory=result.initial_memory,
            expected_writes=result.expected_writes,
            read_addresses=result.read_addresses,
            guards=guards,
            regions=regions,
            context=resolved,
            epoch=epoch,
            log=log,
        )

    # -- reporting ---------------------------------------------------------

    def _fail(self, axis: str, body: str, observed_count: int) -> None:
        header = self.context.header(
            axis, self.epoch, len(self.expected_transactions), observed_count
        )
        repro = f"\n{self.context.repro}" if self.context.repro else ""
        message = f"{header}\n{body}{repro}"
        if self.log is not None:
            self.log.error("%s", message)
        raise ScoreboardError(axis, message)

    def _region_label(self, device: int, address: int) -> str:
        for region in self.regions + self.guards:
            if region.contains(device, address):
                return region.kind
        if (device, address) in self.expected_writes:
            return "destination"
        if (device, address) in self.read_addresses:
            return "source"
        return ""

    def _descriptor_context(self, index: int) -> str:
        """Return the active TCD fetch line for a transaction index, if known."""
        active = None
        for expected in self.expected_transactions[: index + 1]:
            if expected.kind == FETCH_READ:
                active = expected
        if active is None:
            return ""
        return f"active_fetch={active.device}:0x{active.address:06X} bytes=[{format_bytes(active.data)}]"

    # -- axis 1: ordered transactions --------------------------------------

    def classify_observed(self, observed: Transaction, index: int) -> Transaction:
        """Resolve a neutral ``READ``/``WRITE`` record into an oracle kind.

        Every ``0x02`` is ``DATA_WRITE``. A read is ``FETCH_READ`` only when it
        matches the expected fetch position at *index*; otherwise it is
        ``DATA_READ``. Opcode, device, address, length, and data are never
        inferred from the expected record.
        """
        if observed.kind == OBSERVED_WRITE:
            return observed.as_kind(DATA_WRITE)
        if observed.kind != OBSERVED_READ:
            return observed
        if index < len(self.expected_transactions):
            expected = self.expected_transactions[index]
            if (
                expected.kind == FETCH_READ
                and expected.device == observed.device
                and expected.address == observed.address
                and expected.length == observed.length
            ):
                return observed.as_kind(FETCH_READ)
        return observed.as_kind(DATA_READ)

    def _compare_log(self, observed, expected, axis: str, label: str) -> None:
        observed = as_transactions(observed)
        resolved = tuple(
            self.classify_observed(record, index)
            for index, record in enumerate(observed)
        )

        for index in range(min(len(expected), len(resolved))):
            want = expected[index]
            got = resolved[index]
            diffs = want.differences(got)
            if not diffs:
                continue
            self._fail(
                axis,
                self._transaction_report(index, want, got, diffs, expected, resolved, label),
                len(resolved),
            )

        if len(resolved) != len(expected):
            index = min(len(expected), len(resolved))
            want = expected[index] if index < len(expected) else None
            got = resolved[index] if index < len(resolved) else None
            body = [
                f"{label}: log length differs: expected {len(expected)} record(s), "
                f"observed {len(resolved)}",
                f"first unmatched index={index}",
                f"expected {want.canonical() if want is not None else MISSING}",
                f"observed {got.canonical() if got is not None else MISSING}",
                "",
                "expected context:",
                _window(expected, index),
                "observed context:",
                _window(resolved, index),
            ]
            self._fail(axis, "\n".join(body), len(resolved))

    def _transaction_report(self, index, want, got, diffs, expected, resolved, label) -> str:
        lines = [
            f"{label}: first mismatching index={index}",
            f"expected {want.canonical()}",
            f"observed {got.canonical()}",
            "field differences:",
        ]
        for name, mine, theirs in diffs:
            lines.append(f"  {name}: expected={_show(name, mine)} observed={_show(name, theirs)}")
        descriptor = self._descriptor_context(index)
        if descriptor:
            lines.append(descriptor)
        lines += [
            "",
            "expected context:",
            _window(expected, index),
            "observed context:",
            _window(resolved, index),
        ]
        return "\n".join(lines)

    def compare_transactions(self, observed) -> None:
        """Axis 1: ordered transaction log equality.

        Raises:
            ScoreboardError: on the first differing record, or on a length
                difference (extra, missing, truncated, or duplicated record).
        """
        self._compare_log(
            observed, self.expected_transactions, AXIS_TRANSACTIONS, "transactions"
        )

    # -- axis 2: final memory ---------------------------------------------

    def _compare_memory_image(
        self,
        observed_memory,
        expected_memory: MemoryImage,
        *,
        skip=(),
        label: str = "final memory",
        observed_count: int = 0,
    ) -> "list[ByteMismatch]":
        observed = _ObservedMemory(observed_memory)
        skip = set(skip)
        mismatches: "list[ByteMismatch]" = []

        for device in sorted(set(expected_memory.devices) | set(observed.devices())):
            addresses = set()
            if device in expected_memory.devices:
                addresses |= expected_memory.defined_addresses(device)
            addresses |= observed.defined_addresses(device)
            addresses |= {
                address for (dev, address) in self.expected_writes if dev == device
            }
            addresses |= {
                address for (dev, address) in self.read_addresses if dev == device
            }
            for region in self.guards:
                if region.device == device:
                    addresses |= region.addresses()

            for address in sorted(addresses):
                if (device, address) in skip:
                    continue
                want = _expected_byte(expected_memory, device, address)
                got = observed.byte(device, address)
                if want == got:
                    continue
                mismatches.append(
                    ByteMismatch(
                        device=device,
                        address=address,
                        expected=want,
                        observed=got,
                        classification=self._classify_byte(device, address, want, got),
                        region=self._region_label(device, address),
                    )
                )

        self.mismatches = mismatches
        if mismatches:
            self._fail(
                AXIS_MEMORY,
                self._memory_report(mismatches, observed, expected_memory, label),
                observed_count,
            )
        return mismatches

    def _classify_byte(self, device, address, expected, observed) -> str:
        if (device, address) in self.expected_writes:
            initial = (
                None
                if self.initial_memory is None
                else _expected_byte(self.initial_memory, device, address)
            )
            if initial is not None and observed == initial:
                return CLASS_MISSING_WRITE
            if observed is None:
                return CLASS_MISSING_WRITE
            return CLASS_WRONG_DATA
        return CLASS_UNEXPECTED_WRITE

    def _memory_report(self, mismatches, observed, expected_memory, label) -> str:
        first = mismatches[0]
        lines = [
            f"{label}: {len(mismatches)} differing byte(s); first:",
            first.canonical(),
        ]
        descriptor = self._descriptor_context(len(self.expected_transactions) - 1)
        if descriptor:
            lines.append(descriptor)
        lines.append(
            "expected window: "
            + _hex_window(
                lambda address: _expected_byte(expected_memory, first.device, address),
                first.address,
            )
        )
        lines.append(
            "observed window: "
            + _hex_window(
                lambda address: observed.byte(first.device, address), first.address
            )
        )
        groups = _group(mismatches)
        lines.append("grouped differences:")
        for group in groups[:MAX_REPORTED_BYTES]:
            lines.append(f"  {group}")
        if len(groups) > MAX_REPORTED_BYTES:
            lines.append(
                f"  ... {len(groups) - MAX_REPORTED_BYTES} more group(s); "
                "full expected/observed traces belong in the run directory"
            )
        return "\n".join(lines)

    def compare_memory(self, observed_memory) -> None:
        """Axis 2: final PSRAM image equality on normal quit completion.

        Compares the union of expected-defined, observed-defined, written, read,
        and explicitly guarded addresses on both devices.

        Raises:
            ScoreboardError: on any differing byte, classified
                ``missing_write``, ``wrong_data``, or ``unexpected_write``.
        """
        self._compare_memory_image(
            observed_memory,
            self.expected_memory,
            observed_count=len(self.expected_transactions),
        )

    def compare(self, observed, observed_memory=None) -> None:
        """Run axis 1 then axis 2 for a normally completed epoch."""
        self.compare_transactions(observed)
        if observed_memory is not None:
            self.compare_memory(observed_memory)

    # -- reset-interrupted epochs ------------------------------------------

    def expected_prefix(self, count: int) -> "tuple[Transaction, ...]":
        """Return the expected log prefix of *count* records."""
        if count > len(self.expected_transactions):
            raise ReferenceModelError(
                f"expected log has {len(self.expected_transactions)} records, "
                f"cannot take a prefix of {count}"
            )
        return self.expected_transactions[:count]

    def compare_reset_prefix(self, observed, reset_index: "int | None" = None) -> None:
        """Compare transactions completed before a sampled reset edge.

        *observed* holds only normally completed records (a CE# interval aborted
        by reset stays a separate diagnostic event). *reset_index*, when given,
        is the number of records completed before the first rising ``clk`` edge
        sampled with ``rst_n=0``; the observed log is truncated to it.

        Raises:
            ReferenceModelError: fewer completed records than *reset_index*, or
                more completed records than the full expected chain.
            ScoreboardError: a completed record differs from the expected prefix.
        """
        records = as_transactions(observed)
        if reset_index is not None:
            if reset_index < 0:
                raise ReferenceModelError(f"reset_index={reset_index} is negative")
            if len(records) < reset_index:
                raise ReferenceModelError(
                    f"reset_index={reset_index} exceeds the {len(records)} completed "
                    "record(s) supplied; the monitor and the sampled reset edge disagree"
                )
            records = records[:reset_index]
        if len(records) > len(self.expected_transactions):
            self._fail(
                AXIS_TRANSACTIONS,
                f"reset prefix: {len(records)} completed record(s) exceed the "
                f"{len(self.expected_transactions)} record expected chain",
                len(records),
            )
        self._compare_log(
            records,
            self.expected_prefix(len(records)),
            AXIS_TRANSACTIONS,
            "reset prefix",
        )

    def compare_reset_memory(
        self, observed_memory, prefix_length: int, *, aborted_addresses=()
    ) -> "list[tuple[int, int]]":
        """Compare committed memory derived from the expected completed prefix.

        Addresses in *aborted_addresses* are reported separately and skipped:
        they are only comparable when the PSRAM model defines byte-commit
        behavior for that exact abort point.

        Returns:
            The list of ``(device, address)`` pairs skipped as aborted.

        Raises:
            ScoreboardError: a committed byte outside the aborted set differs.
        """
        if self.initial_memory is None:
            raise ReferenceModelError(
                "reset-prefix memory needs the epoch's initial memory clone; "
                "build the scoreboard with Scoreboard.from_result(...)"
            )
        skip = [tuple(entry) for entry in aborted_addresses]
        expected = commit_prefix(
            self.initial_memory, self.expected_transactions, prefix_length
        )
        self._compare_memory_image(
            observed_memory,
            expected,
            skip=skip,
            label=f"reset-prefix memory (prefix={prefix_length})",
            observed_count=prefix_length,
        )
        return skip

    # -- repeated-run epochs ------------------------------------------------

    @staticmethod
    def compare_epochs(first_observed, second_observed, *, context=None) -> None:
        """``TC-RESET-REPEAT`` hook: two observed logs must be field-identical."""
        compare_epoch_logs(first_observed, second_observed, context=context)


def compare_epoch_logs(first_observed, second_observed, *, context=None) -> None:
    """Require two epochs' observed transaction logs to be equal field-for-field.

    This is stronger than each epoch's own oracle comparison: it targets working
    state, counters, or pointers leaking across a reset boundary in a way that
    still matches the golden interpreter by coincidence.

    Raises:
        ScoreboardError: the logs differ in length or in any equality field.
    """
    resolved = context or RunContext()
    first = as_transactions(first_observed)
    second = as_transactions(second_observed)

    def fail(body: str) -> None:
        header = resolved.header(AXIS_TRANSACTIONS, "1-vs-2", len(first), len(second))
        repro = f"\n{resolved.repro}" if resolved.repro else ""
        raise ScoreboardError(AXIS_TRANSACTIONS, f"{header}\n{body}{repro}")

    for index in range(min(len(first), len(second))):
        diffs = first[index].differences(second[index])
        if not diffs:
            continue
        lines = [
            f"two-epoch equality: first mismatching index={index}",
            f"epoch1 {first[index].canonical()}",
            f"epoch2 {second[index].canonical()}",
            "field differences:",
        ]
        for name, mine, theirs in diffs:
            lines.append(f"  {name}: epoch1={_show(name, mine)} epoch2={_show(name, theirs)}")
        lines += [
            "",
            "epoch1 context:",
            _window(first, index),
            "epoch2 context:",
            _window(second, index),
        ]
        fail("\n".join(lines))

    if len(first) != len(second):
        index = min(len(first), len(second))
        fail(
            f"two-epoch equality: epoch1 has {len(first)} record(s), epoch2 has "
            f"{len(second)}; first unmatched index={index}\n"
            f"epoch1 {first[index].canonical() if index < len(first) else MISSING}\n"
            f"epoch2 {second[index].canonical() if index < len(second) else MISSING}"
        )


# -- diagnostics helpers ---------------------------------------------------


def _expected_byte(memory: MemoryImage, device: int, address: int) -> "int | None":
    if device not in memory.devices:
        return None
    try:
        return memory.byte(device, address)
    except MemoryUndefinedError:
        return None


def _show(name: str, value) -> str:
    if value is None:
        return MISSING
    if name == "data":
        return format_bytes(value).replace(" ", "") or "<empty>"
    if name == "opcode":
        return f"{value:02X}"
    if name == "address":
        return f"0x{value:06X}"
    return str(value)


def _window(records, index: int, window: int = CONTEXT_RECORDS) -> str:
    low = max(index - window, 0)
    high = min(index + window + 1, len(records))
    if low == high:
        return f"  {MISSING}"
    return "\n".join(f"  {records[position].canonical()}" for position in range(low, high))


def _hex_window(getter, address: int, window: int = HEX_WINDOW) -> str:
    low = max(address - window, 0)
    parts = []
    for current in range(low, address + window + 1):
        value = getter(current)
        text = UNDEFINED if value is None else f"{value:02X}"
        parts.append(f"[{text}]" if current == address else text)
    return f"0x{low:06X}: " + " ".join(parts)


def _group(mismatches) -> "list[str]":
    """Group contiguous same-classification mismatches into report lines."""
    groups: "list[str]" = []
    start: "ByteMismatch | None" = None
    previous: "ByteMismatch | None" = None
    for mismatch in mismatches:
        contiguous = (
            previous is not None
            and mismatch.device == previous.device
            and mismatch.address == previous.address + 1
            and mismatch.classification == previous.classification
        )
        if not contiguous:
            if start is not None:
                groups.append(_group_text(start, previous))
            start = mismatch
        previous = mismatch
    if start is not None:
        groups.append(_group_text(start, previous))
    return groups


def _group_text(start: ByteMismatch, end: ByteMismatch) -> str:
    if start.address == end.address:
        return start.canonical()
    expected = UNDEFINED if start.expected is None else f"{start.expected:02X}"
    observed = UNDEFINED if start.observed is None else f"{start.observed:02X}"
    region = f" region={start.region}" if start.region else ""
    return (
        f"dev={start.device} addr=0x{start.address:06X}..0x{end.address:06X} "
        f"class={start.classification}{region} "
        f"first_expected={expected} first_observed={observed}"
    )


__all__ = [
    "AXIS_MEMORY",
    "AXIS_REFERENCE",
    "AXIS_TRANSACTIONS",
    "CLASS_MISSING_WRITE",
    "CLASS_UNEXPECTED_WRITE",
    "CLASS_WRONG_DATA",
    "ByteMismatch",
    "Region",
    "RunContext",
    "Scoreboard",
    "ScoreboardError",
    "compare_epoch_logs",
    "guard_region",
]
