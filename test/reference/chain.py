"""Pure-Python oracle for TCD chains.

Given an initial PSRAM memory layout, ``interpret_chain`` walks the TCD chain
from the fixed head (PSRAM0 ``0x000000``) and returns what a correct DMA must
produce: an ordered QPI transaction log and the final memory images. It models
architecture only (fetch, chunked copy, quit, next-device links), no timing.

Main types:

* :class:`MemoryImage` - two device memories (PSRAM0/1). Storage is a sparse
  nested dict ``{device: {address: byte}}`` plus an optional fill byte for
  undefined reads. Public API is ``read`` / ``write`` / ``clone``.
* :class:`Transaction` - one CE#-framed QPI transfer in scoreboard form:
  ``kind`` (``FETCH_READ`` / ``DATA_READ`` / ``DATA_WRITE``), ``opcode``,
  ``device``, ``address``, ``length``, and ``data``. Optional timestamps and
  ``meta`` are diagnostics only and ignored by semantic equality.
* :class:`ChainResult` - full interpretation of one chain: the ordered
  ``transactions`` log, ``initial_memory`` / ``final_memory``, fetched
  descriptors and the ``(device, address)`` path taken, plus write/read
  address sets used by the scoreboard.

Normative algorithm: ``docs/llm/verification/05-reference-model.md``.
Used by directed cases such as ``TC-SAME-*``, ``TC-CROSS-*``, ``TC-CHAIN``,
``TC-QUIT``, ``TC-EMPTY``, and ``TC-OVERLAP``.
"""

from dataclasses import dataclass, field, replace

from reference.tcd import (
    PTR_MAX,
    TCD_BYTES,
    ReferenceModelError,
    Tcd,
    TcdError,
    decode_tcd,
    format_bytes,
    format_tcd,
    validate_tcd,
)

# Transaction kinds produced by the oracle.
FETCH_READ = "FETCH_READ"
DATA_READ = "DATA_READ"
DATA_WRITE = "DATA_WRITE"

# Neutral kinds a pin monitor may store before ordered classification.
OBSERVED_READ = "READ"
OBSERVED_WRITE = "WRITE"

OPCODE_READ = 0xEB
OPCODE_WRITE = 0x02

KIND_OPCODE = {
    FETCH_READ: OPCODE_READ,
    DATA_READ: OPCODE_READ,
    DATA_WRITE: OPCODE_WRITE,
    OBSERVED_READ: OPCODE_READ,
    OBSERVED_WRITE: OPCODE_WRITE,
}
READ_KINDS = (FETCH_READ, DATA_READ, OBSERVED_READ)
WRITE_KINDS = (DATA_WRITE, OBSERVED_WRITE)

# V1 fixed head: always PSRAM0, address 0x000000 (03-architecture.md).
HEAD_DEVICE = 0
HEAD_ADDRESS = 0x000000

ADDR_MAX = PTR_MAX
DEVICES = (0, 1)

DEFAULT_FETCH_BUDGET = 64
DEFAULT_TXN_BUDGET = 4096

# V1 tapeout configuration; the oracle accepts the 1/2/4/8 sweep depths too.
DEFAULT_DMA_BUF_DEPTH = 1


class MemoryRangeError(ReferenceModelError):
    """An access left ``0x000000..0x7FFFFF`` or named an unknown device."""


class MemoryUndefinedError(ReferenceModelError):
    """A read touched a byte the initializer never defined and no fill is set."""


class ReferenceLimitError(ReferenceModelError):
    """A fetch or transaction budget was exhausted (never ``QUIT`` or ``DONE``)."""


def _check_index(name: str, value) -> int:
    if isinstance(value, bool):
        raise MemoryRangeError(f"{name} must be an int, got bool {value!r}")
    if not isinstance(value, int):
        raise MemoryRangeError(f"{name} must be an int, got {type(value).__name__}")
    return value


class MemoryImage:
    """Two independent byte-addressed device images keyed by device ``0``/``1``.

    Public operations are :meth:`read`, :meth:`write`, and :meth:`clone`, per
    ``05-reference-model.md``. Uninitialized bytes are a configuration decision:
    with ``fill=None`` (default) reading an undefined byte is a reference-model
    error, and an explicit ``fill`` byte is recorded in :attr:`fill` for the run
    manifest.
    """

    def __init__(self, *, fill: "int | None" = None, devices=DEVICES) -> None:
        if fill is not None:
            fill = _check_index("fill", fill)
            if not 0 <= fill <= 0xFF:
                raise MemoryRangeError(f"fill=0x{fill:X} is not a byte value")
        self.fill = fill
        self.devices = tuple(devices)
        self._bytes: "dict[int, dict[int, int]]" = {
            device: {} for device in self.devices
        }

    # -- span checks -------------------------------------------------------

    def _map(self, device: int) -> "dict[int, int]":
        _check_index("device", device)
        try:
            return self._bytes[device]
        except KeyError:
            raise MemoryRangeError(
                f"device {device} is not part of this image (have {list(self.devices)})"
            ) from None

    def _check_span(self, device: int, address: int, length: int) -> None:
        """Reject any access that does not fit wholly in ``0x000000..0x7FFFFF``."""
        self._map(device)
        _check_index("address", address)
        _check_index("length", length)
        if length < 0:
            raise MemoryRangeError(f"length={length} is negative")
        if address < 0:
            raise MemoryRangeError(f"address={address} is negative")
        last = address + max(length - 1, 0)
        if last > ADDR_MAX:
            raise MemoryRangeError(
                f"dev={device} access 0x{address:06X}+{length} ends at 0x{last:X}, "
                f"past 0x{ADDR_MAX:06X} (widened arithmetic, no wrap)"
            )

    # -- byte access -------------------------------------------------------

    def is_defined(self, device: int, address: int) -> bool:
        """True when *address* was explicitly initialized or written."""
        return address in self._map(device)

    def byte(self, device: int, address: int) -> int:
        """Return one byte, honoring the configured fill policy."""
        self._check_span(device, address, 1)
        values = self._map(device)
        if address in values:
            return values[address]
        if self.fill is None:
            raise MemoryUndefinedError(
                f"dev={device} addr=0x{address:06X} is undefined; initialize it or "
                "construct MemoryImage(fill=<byte>) and record the fill"
            )
        return self.fill

    def poke(self, device: int, address: int, value: int) -> None:
        """Store one byte without any transaction bookkeeping."""
        self._check_span(device, address, 1)
        _check_index("value", value)
        if not 0 <= value <= 0xFF:
            raise MemoryRangeError(f"value=0x{value:X} is not a byte")
        self._map(device)[address] = value

    def read(self, device: int, address: int, length: int) -> bytes:
        """Read *length* bytes; the whole span must fit in the address space."""
        self._check_span(device, address, length)
        return bytes(self.byte(device, address + offset) for offset in range(length))

    def write(self, device: int, address: int, data) -> None:
        """Write *data* at *address*; the whole span must fit in the address space."""
        payload = bytes(data)
        self._check_span(device, address, len(payload))
        values = self._map(device)
        for offset, value in enumerate(payload):
            values[address + offset] = value

    def fill_range(self, device: int, address: int, length: int, value: int) -> None:
        """Define *length* bytes of *value* (sentinels and guard regions)."""
        self.write(device, address, bytes([value & 0xFF]) * length)

    # -- snapshots ---------------------------------------------------------

    def clone(self) -> "MemoryImage":
        """Return an independent image with the same fill policy and contents."""
        other = MemoryImage(fill=self.fill, devices=self.devices)
        for device in self.devices:
            other._bytes[device] = dict(self._bytes[device])
        return other

    def snapshot(self) -> "dict[int, dict[int, int]]":
        """Return ``{device: {address: byte}}`` for defined bytes only."""
        return {device: dict(values) for device, values in self._bytes.items()}

    def defined_addresses(self, device: int) -> "set[int]":
        """Return the set of defined addresses on *device*."""
        return set(self._map(device))

    def describe(self) -> str:
        counts = ", ".join(
            f"dev{device}={len(values)}B" for device, values in self._bytes.items()
        )
        fill = "undefined-is-error" if self.fill is None else f"fill=0x{self.fill:02X}"
        return f"MemoryImage({fill}, {counts})"

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return self.describe()


def memory_from_snapshot(snapshot, *, fill: "int | None" = None) -> MemoryImage:
    """Build a :class:`MemoryImage` from ``{device: {address: byte}}``."""
    image = MemoryImage(fill=fill, devices=tuple(snapshot))
    for device, values in snapshot.items():
        for address, value in values.items():
            image.poke(device, address, value)
    return image


@dataclass(frozen=True)
class Transaction:
    """One CE#-framed QPI transaction in normalized form.

    Equality fields are ``index``, ``kind``, ``opcode``, ``device``,
    ``address``, ``length``, and ``data``. Diagnostic metadata
    (``start_time_ns``, ``end_time_ns``, ``meta``) is excluded from semantic
    equality.
    """

    index: int
    kind: str
    opcode: int
    device: int
    address: int
    length: int
    data: bytes
    start_time_ns: "float | None" = field(default=None, compare=False)
    end_time_ns: "float | None" = field(default=None, compare=False)
    meta: "dict | None" = field(default=None, compare=False)

    EQUALITY_FIELDS = ("index", "kind", "opcode", "device", "address", "length", "data")

    @property
    def is_read(self) -> bool:
        return self.kind in READ_KINDS

    @property
    def is_write(self) -> bool:
        return self.kind in WRITE_KINDS

    def equality(self) -> dict:
        """Return only the fields that participate in semantic equality."""
        return {name: getattr(self, name) for name in self.EQUALITY_FIELDS}

    def differences(self, other: "Transaction") -> "list[tuple[str, object, object]]":
        """Return ``(field, mine, theirs)`` for every differing equality field."""
        diffs = []
        for name in self.EQUALITY_FIELDS:
            mine = getattr(self, name)
            theirs = getattr(other, name)
            if mine != theirs:
                diffs.append((name, mine, theirs))
        return diffs

    def canonical(self) -> str:
        """Canonical single-line form from ``05-reference-model.md``."""
        return (
            f"#{self.index:03d} {self.kind} op={self.opcode:02X} "
            f"dev={self.device} addr=0x{self.address:06X} len={self.length} "
            f"data={format_bytes(self.data).replace(' ', '')}"
        )

    def as_kind(self, kind: str) -> "Transaction":
        """Return a copy classified as *kind* (neutral READ/WRITE resolution)."""
        return replace(self, kind=kind)

    def with_index(self, index: int) -> "Transaction":
        return replace(self, index=index)

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.canonical()


def transaction(
    index: int,
    kind: str,
    device: int,
    address: int,
    data,
    *,
    opcode: "int | None" = None,
    length: "int | None" = None,
    **metadata,
) -> Transaction:
    """Build one :class:`Transaction`, defaulting opcode and length from *kind*.

    ``length`` may be given explicitly so a monitor can record a decoded byte
    count that disagrees with the payload it captured.
    """
    payload = bytes(data)
    if opcode is None:
        try:
            opcode = KIND_OPCODE[kind]
        except KeyError:
            raise ReferenceModelError(f"unknown transaction kind {kind!r}") from None
    return Transaction(
        index=index,
        kind=kind,
        opcode=opcode,
        device=device,
        address=address,
        length=len(payload) if length is None else length,
        data=payload,
        **metadata,
    )


def as_transactions(records) -> "tuple[Transaction, ...]":
    """Normalize an iterable of :class:`Transaction` or mappings.

    Mappings accept the equality field names plus optional ``start_time_ns`` /
    ``end_time_ns`` / ``meta``. ``index`` defaults to position when absent, so a
    monitor may emit ordered records without numbering them itself.
    """
    normalized = []
    for position, record in enumerate(records):
        if isinstance(record, Transaction):
            normalized.append(record)
            continue
        if not isinstance(record, dict):
            raise ReferenceModelError(
                f"transaction record {position} is {type(record).__name__}, "
                "expected Transaction or dict"
            )
        fields = dict(record)
        kind = fields.pop("kind")
        normalized.append(
            transaction(
                fields.pop("index", position),
                kind,
                fields.pop("device"),
                fields.pop("address"),
                fields.pop("data"),
                opcode=fields.pop("opcode", None),
                length=fields.pop("length", None),
                **fields,
            )
        )
    return tuple(normalized)


@dataclass(frozen=True)
class FetchedTcd:
    """One descriptor the interpreter fetched, with its wire bytes."""

    txn_index: int
    device: int
    address: int
    raw: bytes
    tcd: Tcd

    def canonical(self) -> str:
        return (
            f"tcd_fetch={self.device}:0x{self.address:06X} "
            f"bytes=[{format_bytes(self.raw)}] {format_tcd(self.tcd)}"
        )


@dataclass
class ChainResult:
    """Golden interpretation of one chain: log, memory, and chain path."""

    transactions: "tuple[Transaction, ...]"
    final_memory: MemoryImage
    initial_memory: MemoryImage
    descriptors: "tuple[FetchedTcd, ...]"
    path: "tuple[tuple[int, int], ...]"
    dma_buf_depth: int
    expected_writes: "dict[tuple[int, int], int]"
    read_addresses: "set[tuple[int, int]]"
    completed: bool = True

    @property
    def fetch_count(self) -> int:
        return len(self.descriptors)

    def data_transactions(self) -> "tuple[Transaction, ...]":
        return tuple(txn for txn in self.transactions if txn.kind != FETCH_READ)

    def descriptor_for(self, txn_index: int) -> "FetchedTcd | None":
        """Return the descriptor active at *txn_index* (diagnostics)."""
        active = None
        for descriptor in self.descriptors:
            if descriptor.txn_index <= txn_index:
                active = descriptor
            else:
                break
        return active

    def path_text(self) -> str:
        return " -> ".join(f"{device}:0x{address:06X}" for device, address in self.path)

    def log_text(self) -> str:
        return "\n".join(txn.canonical() for txn in self.transactions)

    def __len__(self) -> int:
        return len(self.transactions)

    def __iter__(self):
        return iter(self.transactions)


def _check_depth(dma_buf_depth) -> int:
    if isinstance(dma_buf_depth, bool) or not isinstance(dma_buf_depth, int):
        raise ReferenceModelError(
            f"dma_buf_depth must be an int, got {dma_buf_depth!r}"
        )
    if dma_buf_depth < 1:
        raise ReferenceModelError(f"dma_buf_depth={dma_buf_depth} must be at least 1")
    return dma_buf_depth


def _path_text(path) -> str:
    return " -> ".join(f"{device}:0x{address:06X}" for device, address in path)


def interpret_chain(
    initial_memory: MemoryImage,
    dma_buf_depth: int = DEFAULT_DMA_BUF_DEPTH,
    fetch_budget: int = DEFAULT_FETCH_BUDGET,
    txn_budget: int = DEFAULT_TXN_BUDGET,
) -> ChainResult:
    """Interpret the chain rooted at the V1 fixed head and return a :class:`ChainResult`.

    Starts at PSRAM0 ``0x000000`` unconditionally, emits one 11-byte
    ``FETCH_READ`` per descriptor, then alternating ``DATA_READ`` /
    ``DATA_WRITE`` chunks of ``k=min(dma_buf_depth, remaining)``. ``QUIT``
    outranks ``TRANSFER_LEN``; ``TRANSFER_LEN=0`` follows ``NEXT_TCD`` on
    ``NEXT_DEVICE`` without any data transaction. Each chunk reads all ``k``
    bytes before its own write mutates memory, so overlapping ranges follow
    sequential chunk behavior rather than whole-transfer ``memmove``.

    *initial_memory* is never mutated: the interpreter clones it.

    Raises:
        ReferenceLimitError: a fetch or transaction budget was exhausted.
        TcdError: a fetched descriptor is not legal V1 stimulus.
        MemoryRangeError: an access left ``0x000000..0x7FFFFF``.
        MemoryUndefinedError: a read touched an undefined byte with no fill.
    """
    _check_depth(dma_buf_depth)
    _check_index("fetch_budget", fetch_budget)
    _check_index("txn_budget", txn_budget)

    memory = initial_memory.clone()
    initial = initial_memory.clone()

    transactions: "list[Transaction]" = []
    descriptors: "list[FetchedTcd]" = []
    path: "list[tuple[int, int]]" = []
    expected_writes: "dict[tuple[int, int], int]" = {}
    read_addresses: "set[tuple[int, int]]" = set()

    fetch_device, fetch_address = HEAD_DEVICE, HEAD_ADDRESS

    def emit(kind: str, device: int, address: int, payload: bytes) -> None:
        if len(transactions) >= txn_budget:
            raise ReferenceLimitError(
                f"transaction budget {txn_budget} exhausted before "
                f"{kind} dev={device} addr=0x{address:06X}; path={_path_text(path)}"
            )
        transactions.append(
            transaction(len(transactions), kind, device, address, payload)
        )

    while True:
        if len(descriptors) >= fetch_budget:
            raise ReferenceLimitError(
                f"descriptor fetch budget {fetch_budget} exhausted at next fetch "
                f"{fetch_device}:0x{fetch_address:06X}; path={_path_text(path)}. "
                "Budget exhaustion is not QUIT, DONE, or a DUT pass."
            )

        path.append((fetch_device, fetch_address))
        raw = memory.read(fetch_device, fetch_address, TCD_BYTES)
        emit(FETCH_READ, fetch_device, fetch_address, raw)
        read_addresses.update(
            (fetch_device, fetch_address + offset) for offset in range(TCD_BYTES)
        )

        try:
            tcd = validate_tcd(decode_tcd(raw))
        except TcdError as error:
            raise TcdError(
                f"{error} at fetch {fetch_device}:0x{fetch_address:06X} "
                f"bytes=[{format_bytes(raw)}]; path={_path_text(path)}"
            ) from error

        descriptors.append(
            FetchedTcd(
                txn_index=len(transactions) - 1,
                device=fetch_device,
                address=fetch_address,
                raw=raw,
                tcd=tcd,
            )
        )

        if tcd.quit:
            break

        src_address = tcd.src_ptr
        dest_address = tcd.dest_ptr
        remaining = tcd.transfer_len

        while remaining > 0:
            chunk = min(dma_buf_depth, remaining)
            payload = memory.read(tcd.src_device, src_address, chunk)
            emit(DATA_READ, tcd.src_device, src_address, payload)
            read_addresses.update(
                (tcd.src_device, src_address + offset) for offset in range(chunk)
            )
            emit(DATA_WRITE, tcd.dest_device, dest_address, payload)
            memory.write(tcd.dest_device, dest_address, payload)
            for offset, value in enumerate(payload):
                expected_writes[(tcd.dest_device, dest_address + offset)] = value
            remaining -= chunk
            if remaining > 0:
                src_address += chunk
                dest_address += chunk

        fetch_device, fetch_address = tcd.next_device, tcd.next_tcd

    return ChainResult(
        transactions=tuple(transactions),
        final_memory=memory,
        initial_memory=initial,
        descriptors=tuple(descriptors),
        path=tuple(path),
        dma_buf_depth=dma_buf_depth,
        expected_writes=expected_writes,
        read_addresses=read_addresses,
        completed=True,
    )


def commit_prefix(
    initial_memory: MemoryImage, transactions, count: "int | None" = None
) -> MemoryImage:
    """Replay the first *count* transactions' writes onto a clone of *initial_memory*.

    Used for reset-interrupted epochs: expected committed memory is derived from
    the expected completed prefix, never from the full chain.
    """
    memory = initial_memory.clone()
    for txn in as_transactions(transactions)[:count]:
        if txn.kind in WRITE_KINDS:
            memory.write(txn.device, txn.address, txn.data)
    return memory


def format_log(transactions, *, first: "int | None" = None, window: int = 3) -> str:
    """Render a log, optionally windowed around index *first* for a mismatch."""
    records = as_transactions(transactions)
    if first is None:
        return "\n".join(txn.canonical() for txn in records)
    low = max(first - window, 0)
    high = min(first + window + 1, len(records))
    return "\n".join(txn.canonical() for txn in records[low:high])


__all__ = [
    "ADDR_MAX",
    "DATA_READ",
    "DATA_WRITE",
    "DEFAULT_DMA_BUF_DEPTH",
    "DEFAULT_FETCH_BUDGET",
    "DEFAULT_TXN_BUDGET",
    "DEVICES",
    "FETCH_READ",
    "HEAD_ADDRESS",
    "HEAD_DEVICE",
    "OBSERVED_READ",
    "OBSERVED_WRITE",
    "OPCODE_READ",
    "OPCODE_WRITE",
    "READ_KINDS",
    "WRITE_KINDS",
    "ChainResult",
    "FetchedTcd",
    "MemoryImage",
    "MemoryRangeError",
    "MemoryUndefinedError",
    "ReferenceLimitError",
    "Transaction",
    "as_transactions",
    "commit_prefix",
    "format_log",
    "interpret_chain",
    "memory_from_snapshot",
    "transaction",
]
