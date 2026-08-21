"""Legal descriptor-chain generator (``08-stimulus-and-coverage.md``).

One class owns chain construction so a single field's rule can be inspected,
overridden, or held fixed while another dimension is swept:

* :meth:`ChainGenerator.chain_length`, :meth:`ChainGenerator.device_tuple`,
  :meth:`ChainGenerator.next_device`, :meth:`ChainGenerator.transfer_length`,
  :meth:`ChainGenerator.address_class`, :meth:`ChainGenerator.payload_pattern`,
  and :meth:`ChainGenerator.layout_class` are each separately callable,
* :meth:`ChainGenerator.build_directed` lays out a caller-specified corner, and
* :meth:`ChainGenerator.build_chain` draws every dimension from the bias table.

The bias table is data the class consumes (:data:`DEFAULT_BIAS`), not constants
inlined in generation logic, so a directed test can narrow or override one
dimension without forking the generator. Child ``random.Random`` streams come
from the single ``SEED`` contract in :func:`common.seeds.child_random`; module
global random state is never read.

Every returned chain is encoded through :mod:`reference.tcd` and interpreted
through :mod:`reference.chain`, so a generated chain is guaranteed to pass
``validate_tcd`` and to interpret without a ``reference_limit`` error. Pure
Python only; no cocotb.

Every descriptor slot in the chain (head, links, and the terminating quit
descriptor) is write-forbidden: no destination placement or
``LAYOUT_EQUAL``/``LAYOUT_OVERLAP_*`` layout may land on it. This is enforced
at placement time, unconditionally.

M5 owns full constrained-random volume and the coverage catalog. This module is
sized for M2 directed corners plus deterministic small random chains.
"""

from dataclasses import dataclass

from common.constants import FILL
from common.seeds import child_random
from reference.chain import (
    ADDR_MAX,
    DEFAULT_DMA_BUF_DEPTH,
    HEAD_ADDRESS,
    HEAD_DEVICE,
    ChainResult,
    MemoryImage,
    interpret_chain,
)
from reference.scoreboard import Region, guard_region
from reference.constants import PAGE_SIZE
from reference.tcd import (
    TCD_BYTES,
    TRANSFER_LEN_MAX,
    ReferenceModelError,
    Tcd,
    encode_tcd,
    format_bytes,
)

# Stable child-stream names (Determinism section of 08-stimulus-and-coverage.md).
STREAM_CHAIN = "chain"
STREAM_DEVICES = "devices"
STREAM_LENGTHS = "lengths"
STREAM_ADDRESSES = "addresses"
STREAM_PAYLOAD = "payload"
STREAM_LAYOUT = "layout"
STREAMS = (
    STREAM_CHAIN,
    STREAM_DEVICES,
    STREAM_LENGTHS,
    STREAM_ADDRESSES,
    STREAM_PAYLOAD,
    STREAM_LAYOUT,
)

REGION_DESCRIPTOR = "descriptor"
REGION_SOURCE = "source"
REGION_DESTINATION = "destination"
REGION_GUARD = "guard"

ADDR_ZERO = "zero"
ADDR_LOW = "low"
ADDR_BOUNDARY_64K = "boundary_64k"
ADDR_PAGE_EDGE = "page_edge"
ADDR_HIGH = "high"

PATTERN_ZERO = "zero"
PATTERN_ONES = "ones"
PATTERN_WALKING = "walking"
PATTERN_INCREMENT = "increment"
PATTERN_ALTERNATING = "alternating"
PATTERN_RANDOM = "random"

LAYOUT_DISJOINT = "disjoint"
LAYOUT_EQUAL = "equal"
LAYOUT_OVERLAP_FORWARD = "overlap_forward"
LAYOUT_OVERLAP_BACKWARD = "overlap_backward"

DEFAULT_REGION_START = 0x000100
DEFAULT_REGION_GAP = 0x10
DEFAULT_GUARD_BYTES = 2
DEFAULT_FILL = FILL
DEST_SENTINEL = 0x5A
GUARD_VALUE = 0xC3

# Bias table consumed as data; pass ``bias={...}`` to narrow one dimension.
DEFAULT_BIAS = {
    "chain_length": {
        "min": 1,
        "max": 8,
        "favored": {"min": 3, "second": 2, "max": 2},
        "uniform_weight": 3,
    },
    "transfer_len": {
        "corner_weight": 60,
        "uniform_weight": 40,
        "corners": (0, 1, "N-1", "N", "N+1", "2N-1", "2N", "2N+1", TRANSFER_LEN_MAX),
        "low": 0,
        "high": TRANSFER_LEN_MAX,
    },
    "device_tuple": {(0, 0): 1, (0, 1): 1, (1, 0): 1, (1, 1): 1},
    "next_device": {"change": 60, "keep": 40},
    "address_class": {
        ADDR_ZERO: 1,
        ADDR_LOW: 4,
        ADDR_BOUNDARY_64K: 2,
        ADDR_PAGE_EDGE: 2,
        ADDR_HIGH: 1,
    },
    "payload_pattern": {
        PATTERN_ZERO: 1,
        PATTERN_ONES: 1,
        PATTERN_WALKING: 1,
        PATTERN_INCREMENT: 2,
        PATTERN_ALTERNATING: 1,
        PATTERN_RANDOM: 2,
    },
    "layout": {
        LAYOUT_DISJOINT: 6,
        LAYOUT_EQUAL: 1,
        LAYOUT_OVERLAP_FORWARD: 1,
        LAYOUT_OVERLAP_BACKWARD: 1,
    },
}


class GeneratorError(ReferenceModelError):
    """The requested chain cannot be laid out legally."""


@dataclass(frozen=True)
class TcdSpec:
    """One executable descriptor a caller wants in a directed chain.

    Every field is optional. ``None`` means "let the generator choose from its
    streams and bias table", so a directed corner pins only the dimension it
    cares about:

    * ``next_device`` ``None`` keeps the chain on the device this descriptor
      lives on; otherwise it selects the device of the following descriptor.
    * ``src_addr`` / ``dest_addr`` pin exact pointers (``TC-ADDR-WIDE``,
      ``TC-OVERLAP``); unset addresses come from ``src_class`` / ``dest_class``
      and the layout allocator.
    * ``next_tcd_addr`` pins where the *following* descriptor (or the
      terminating quit descriptor) is placed.
    * ``data`` pins exact source bytes; otherwise ``pattern`` generates them.
    """

    transfer_len: int = 1
    src_device: int = 0
    dest_device: int = 0
    next_device: "int | None" = None
    src_addr: "int | None" = None
    dest_addr: "int | None" = None
    next_tcd_addr: "int | None" = None
    data: "bytes | None" = None
    pattern: "str | None" = None
    src_class: "str | None" = None
    dest_class: "str | None" = None
    layout: "str | None" = None
    reserved: int = 0


@dataclass(frozen=True)
class GeneratedChain:
    """A legal chain plus the exact byte layout chosen for it."""

    tcds: "tuple[Tcd, ...]"
    descriptor_locations: "tuple[tuple[int, int], ...]"
    memory: MemoryImage
    regions: "tuple[Region, ...]"
    guards: "tuple[Region, ...]"
    seed: int
    dma_buf_depth: int = DEFAULT_DMA_BUF_DEPTH
    notes: "tuple[str, ...]" = ()

    @property
    def head(self) -> "tuple[int, int]":
        return (HEAD_DEVICE, HEAD_ADDRESS)

    @property
    def executable(self) -> "tuple[Tcd, ...]":
        """Descriptors excluding the terminating quit descriptor."""
        return tuple(tcd for tcd in self.tcds if not tcd.quit)

    def interpret(self, dma_buf_depth: "int | None" = None, **kwargs) -> ChainResult:
        """Interpret this chain from an independent clone of its memory."""
        depth = self.dma_buf_depth if dma_buf_depth is None else dma_buf_depth
        return interpret_chain(self.memory, depth, **kwargs)

    def regions_of(self, kind: str) -> "tuple[Region, ...]":
        return tuple(region for region in self.regions if region.kind == kind)

    def manifest(self) -> dict:
        """Return a JSON-able stimulus manifest for the run directory."""
        return {
            "seed": self.seed,
            "dma_buf_depth": self.dma_buf_depth,
            "head": {"device": HEAD_DEVICE, "address": HEAD_ADDRESS},
            "descriptors": [
                {
                    "device": device,
                    "address": address,
                    "bytes": format_bytes(encode_tcd(tcd)),
                    "quit": bool(tcd.quit),
                    "src": {"device": tcd.src_device, "address": tcd.src_ptr},
                    "dest": {"device": tcd.dest_device, "address": tcd.dest_ptr},
                    "transfer_len": tcd.transfer_len,
                    "next": {"device": tcd.next_device, "address": tcd.next_tcd},
                }
                for tcd, (device, address) in zip(self.tcds, self.descriptor_locations)
            ],
            "regions": [
                {
                    "kind": region.kind,
                    "device": region.device,
                    "address": region.address,
                    "length": region.length,
                }
                for region in self.regions + self.guards
            ],
            "notes": list(self.notes),
        }

    def describe(self) -> str:
        parts = [
            f"{device}:0x{address:06X}"
            for device, address in self.descriptor_locations
        ]
        return (
            f"GeneratedChain(seed={self.seed}, depth={self.dma_buf_depth}, "
            f"tcds={len(self.tcds)}, path={' -> '.join(parts)})"
        )


class _Layout:
    """Bump allocator with occupancy tracking for one chain's byte layout."""

    def __init__(self, *, start: int = DEFAULT_REGION_START, gap: int = DEFAULT_REGION_GAP) -> None:
        self._start = start
        self._cursor = {0: start, 1: start}
        self._used: "dict[int, set[int]]" = {0: set(), 1: set()}
        self._descriptor_used: "dict[int, set[int]]" = {0: set(), 1: set()}
        self._gap = gap

    def free(self, device: int, address: int, length: int) -> bool:
        if address < 0 or address + max(length - 1, 0) > ADDR_MAX:
            return False
        used = self._used[device]
        return all(address + offset not in used for offset in range(length))

    def occupy(self, device: int, address: int, length: int) -> None:
        """Mark a region used without moving the allocator cursor.

        A pinned or address-class placement near the top of memory must not push
        the cursor out of the address space; only :meth:`allocate` advances it.
        """
        self._used[device].update(range(address, address + length))

    def reserve_descriptor(self, device: int, address: int, length: int) -> None:
        """Occupy a descriptor slot and remember it as write-forbidden for the
        whole build, independent of when it is placed relative to other TCDs.
        """
        self.occupy(device, address, length)
        self._descriptor_used[device].update(range(address, address + length))

    def overlaps_descriptor(self, device: int, address: int, length: int) -> bool:
        """True when ``[address, address+length)`` intersects a reserved descriptor slot."""
        used = self._descriptor_used[device]
        return any(address + offset in used for offset in range(max(length, 1)))

    def allocate(self, device: int, length: int) -> int:
        """Return the next free address on *device* able to hold *length* bytes."""
        span = max(length, 1)
        address = self._cursor[device]
        while not self.free(device, address, span):
            address += 1
            if address + span - 1 > ADDR_MAX:
                raise GeneratorError(
                    f"no free {span}-byte region left on device {device}"
                )
        self.occupy(device, address, span)
        self._cursor[device] = address + span + self._gap
        return address


def _weighted(stream, weights: dict):
    """Return one key from *weights* (``{value: weight}``) using *stream*."""
    entries = [(key, weight) for key, weight in weights.items() if weight > 0]
    if not entries:
        raise GeneratorError("bias table entry has no positive weights")
    total = sum(weight for _, weight in entries)
    draw = stream.random() * total
    upto = 0.0
    for key, weight in entries:
        upto += weight
        if draw < upto:
            return key
    return entries[-1][0]


class ChainGenerator:
    """Build firmware-legal descriptor chains for directed and small random use."""

    def __init__(
        self,
        seed: int = 0,
        *,
        bias: "dict | None" = None,
        dma_buf_depth: int = DEFAULT_DMA_BUF_DEPTH,
        fill: int = DEFAULT_FILL,
        dest_sentinel: int = DEST_SENTINEL,
        guard_value: int = GUARD_VALUE,
        guard_bytes: int = DEFAULT_GUARD_BYTES,
        region_start: int = DEFAULT_REGION_START,
        region_gap: int = DEFAULT_REGION_GAP,
    ) -> None:
        self.seed = int(seed)
        self.bias = _merge_bias(bias)
        self.dma_buf_depth = dma_buf_depth
        self.fill = fill & 0xFF
        self.dest_sentinel = dest_sentinel & 0xFF
        self.guard_value = guard_value & 0xFF
        self.guard_bytes = guard_bytes
        self.region_start = region_start
        self.region_gap = region_gap
        self._streams = {name: child_random(self.seed, name) for name in STREAMS}

    # -- streams -----------------------------------------------------------

    def stream(self, name: str):
        """Return the child ``random.Random`` for *name* (stable per seed)."""
        try:
            return self._streams[name]
        except KeyError:
            raise GeneratorError(
                f"unknown stream {name!r}; declared streams are {list(STREAMS)}"
            ) from None

    def reset_streams(self) -> None:
        """Rebuild every child stream so a repeated build is bit-identical."""
        self._streams = {name: child_random(self.seed, name) for name in STREAMS}

    # -- one method per generated dimension --------------------------------

    def chain_length(self) -> int:
        """Number of executable TCDs, favoring 1, 2, and the configured maximum."""
        table = self.bias["chain_length"]
        low, high = table["min"], table["max"]
        favored = table["favored"]
        weights: "dict[object, int]" = {}
        for value, weight in (
            (low, favored.get("min", 0)),
            (min(low + 1, high), favored.get("second", 0)),
            (high, favored.get("max", 0)),
            ("uniform", table.get("uniform_weight", 0)),
        ):
            # Accumulate, so a narrowed bias table that collapses low and high
            # onto one value keeps a positive weight instead of overwriting it.
            weights[value] = weights.get(value, 0) + weight
        choice = _weighted(self.stream(STREAM_CHAIN), weights)
        if choice == "uniform":
            return self.stream(STREAM_CHAIN).randint(low, high)
        return int(choice)

    def device_tuple(self) -> "tuple[int, int]":
        """Return ``(src_device, dest_device)`` with equal weight per direction."""
        return tuple(_weighted(self.stream(STREAM_DEVICES), self.bias["device_tuple"]))

    def next_device(self, current_device: int) -> int:
        """Return the next fetch device, biased to change device."""
        choice = _weighted(self.stream(STREAM_DEVICES), self.bias["next_device"])
        return (1 - current_device) if choice == "change" else current_device

    def length_class(self) -> "int | str":
        """Return one raw transfer-length class from the bias table."""
        table = self.bias["transfer_len"]
        weights = {
            "corner": table["corner_weight"],
            "uniform": table["uniform_weight"],
        }
        if _weighted(self.stream(STREAM_LENGTHS), weights) == "uniform":
            return "uniform"
        corners = tuple(table["corners"])
        return corners[self.stream(STREAM_LENGTHS).randrange(len(corners))]

    def transfer_length(self, dma_buf_depth: "int | None" = None) -> int:
        """Resolve :meth:`length_class` into a legal ``0..255`` transfer length."""
        table = self.bias["transfer_len"]
        depth = self.dma_buf_depth if dma_buf_depth is None else dma_buf_depth
        low, high = table["low"], table["high"]
        selected = self.length_class()
        if selected == "uniform":
            return self.stream(STREAM_LENGTHS).randint(low, high)
        resolved = {
            "N-1": depth - 1,
            "N": depth,
            "N+1": depth + 1,
            "2N-1": 2 * depth - 1,
            "2N": 2 * depth,
            "2N+1": 2 * depth + 1,
        }.get(selected, selected)
        return max(low, min(high, int(resolved)))

    def address_class(self) -> str:
        """Return one address class name for a pointer role."""
        return _weighted(self.stream(STREAM_ADDRESSES), self.bias["address_class"])

    def payload_pattern(self) -> str:
        """Return one payload pattern name."""
        return _weighted(self.stream(STREAM_PAYLOAD), self.bias["payload_pattern"])

    def layout_class(self) -> str:
        """Return one source/destination layout class name."""
        return _weighted(self.stream(STREAM_LAYOUT), self.bias["layout"])

    # -- dimension helpers -------------------------------------------------

    def payload(self, pattern: str, length: int) -> bytes:
        """Materialize *length* bytes of *pattern*."""
        if length <= 0:
            return b""
        if pattern == PATTERN_ZERO:
            return bytes(length)
        if pattern == PATTERN_ONES:
            return b"\xFF" * length
        if pattern == PATTERN_INCREMENT:
            return bytes((index + 1) & 0xFF for index in range(length))
        if pattern == PATTERN_WALKING:
            return bytes(1 << (index % 8) for index in range(length))
        if pattern == PATTERN_ALTERNATING:
            return bytes(0xA5 if index % 2 == 0 else 0x5A for index in range(length))
        if pattern == PATTERN_RANDOM:
            stream = self.stream(STREAM_PAYLOAD)
            return bytes(stream.randrange(256) for _ in range(length))
        raise GeneratorError(f"unknown payload pattern {pattern!r}")

    def address_for(self, address_class: str, length: int, device: int, layout: _Layout) -> int:
        """Return a legal start address of *address_class*, or fall back to the allocator.

        Class candidates that collide with an already-placed region or leave the
        address space fall back to the bump allocator so a generated chain never
        becomes accidentally illegal.
        """
        span = max(length, 1)
        stream = self.stream(STREAM_ADDRESSES)
        if address_class == ADDR_ZERO:
            candidates = [0x000000]
        elif address_class == ADDR_LOW:
            candidates = [stream.randrange(0x000100, 0x010000 - span)]
        elif address_class == ADDR_BOUNDARY_64K:
            candidates = [0x010000 - span, 0x010000, 0x00FFFF - span + 1]
        elif address_class == ADDR_PAGE_EDGE:
            page = stream.randrange(1, 64)
            base = page * PAGE_SIZE
            candidates = [base - span, base, base - 1]
        elif address_class == ADDR_HIGH:
            candidates = [ADDR_MAX - span + 1]
        else:
            raise GeneratorError(f"unknown address class {address_class!r}")

        for candidate in candidates:
            if candidate >= 0 and layout.free(device, candidate, span):
                layout.occupy(device, candidate, span)
                return candidate
        return layout.allocate(device, span)

    # -- chain construction ------------------------------------------------

    def build_directed(
        self,
        specs,
        *,
        quit_spec: "TcdSpec | None" = None,
        dma_buf_depth: "int | None" = None,
        verify: bool = True,
        notes=(),
    ) -> GeneratedChain:
        """Lay out one caller-specified chain and terminate it with a quit TCD.

        *specs* is an ordered iterable of :class:`TcdSpec` (or mappings). The
        head descriptor always lands on PSRAM0 ``0x000000``; each following
        descriptor lands on the device its predecessor's ``next_device``
        selected. Unset addresses, payloads, and devices come from the generator
        streams, so a directed build is still deterministic for one seed.

        Pass ``specs=()`` for the ``TC-EMPTY`` case (quit descriptor at the head).

        Every descriptor slot (head, links, quit) is write-forbidden: a pinned
        ``dest_addr`` that would land there raises, and an equal/overlap layout
        that would land there is redirected disjoint (``_place_transfer``).

        Raises:
            GeneratorError: the requested layout cannot be placed legally.
        """
        specs = tuple(_as_spec(spec) for spec in specs)
        depth = self.dma_buf_depth if dma_buf_depth is None else dma_buf_depth
        layout = _Layout(start=self.region_start, gap=self.region_gap)
        memory = MemoryImage(fill=self.fill)

        locations = self._descriptor_locations(specs, layout)
        descriptor_regions: "tuple[Region, ...]" = tuple(
            Region(device=device, address=address, length=TCD_BYTES, kind=REGION_DESCRIPTOR)
            for device, address in locations
        )
        regions: "list[Region]" = list(descriptor_regions)
        guard_spans: "list[tuple[int, int, int]]" = []

        tcds: "list[Tcd]" = []
        for position, spec in enumerate(specs):
            src_device = spec.src_device
            dest_device = spec.dest_device
            length = spec.transfer_len
            src_address, dest_address = self._place_transfer(spec, layout)

            if length:
                payload = (
                    self.payload(spec.pattern or self.payload_pattern(), length)
                    if spec.data is None
                    else bytes(spec.data)
                )
                if len(payload) != length:
                    raise GeneratorError(
                        f"spec {position}: data is {len(payload)} bytes but "
                        f"transfer_len is {length}"
                    )
                # Destination sentinels first, so an overlapping or equal layout
                # keeps the source payload the caller asked for.
                memory.fill_range(dest_device, dest_address, length, self.dest_sentinel)
                memory.write(src_device, src_address, payload)
                regions.append(
                    Region(src_device, src_address, length, REGION_SOURCE)
                )
                regions.append(
                    Region(dest_device, dest_address, length, REGION_DESTINATION)
                )
                guard_spans.extend(
                    self._guard_spans(dest_device, dest_address, length)
                )

            next_device = locations[position + 1][0]
            next_address = locations[position + 1][1]
            tcds.append(
                Tcd(
                    src_ptr=src_address,
                    dest_ptr=dest_address,
                    transfer_len=length,
                    next_tcd=next_address,
                    quit=False,
                    src_device=src_device,
                    dest_device=dest_device,
                    next_device=next_device,
                    reserved=spec.reserved,
                )
            )

        tcds.append(_quit_tcd(quit_spec))

        for tcd, (device, address) in zip(tcds, locations):
            memory.write(device, address, encode_tcd(tcd))

        # Guards land last and only on bytes nothing else defined, so a sentinel
        # can never overwrite a payload, descriptor, or destination byte.
        guards = self._write_guards(memory, guard_spans)

        chain = GeneratedChain(
            tcds=tuple(tcds),
            descriptor_locations=locations,
            memory=memory,
            regions=tuple(regions),
            guards=tuple(guards),
            seed=self.seed,
            dma_buf_depth=depth,
            notes=tuple(notes),
        )
        if verify:
            chain.interpret()  # raises on illegal TCD, bad range, or budget limit
        return chain

    def build_chain(self, *, dma_buf_depth: "int | None" = None, verify: bool = True) -> GeneratedChain:
        """Draw every dimension from the bias table and return a legal chain."""
        depth = self.dma_buf_depth if dma_buf_depth is None else dma_buf_depth
        count = self.chain_length()
        specs = []
        device = HEAD_DEVICE
        for _ in range(count):
            src_device, dest_device = self.device_tuple()
            device = self.next_device(device)
            specs.append(
                TcdSpec(
                    transfer_len=self.transfer_length(depth),
                    src_device=src_device,
                    dest_device=dest_device,
                    next_device=device,
                    src_class=self.address_class(),
                    dest_class=self.address_class(),
                    pattern=self.payload_pattern(),
                    layout=self.layout_class(),
                )
            )
        return self.build_directed(
            specs, dma_buf_depth=depth, verify=verify, notes=(f"random seed={self.seed}",)
        )

    # -- layout internals --------------------------------------------------

    def _descriptor_locations(self, specs, layout: _Layout):
        """Place every descriptor, head first, following ``next_device``.

        The returned tuple has one entry per spec plus one for the terminating
        quit descriptor, so ``specs=()`` puts the quit descriptor at the fixed
        head (``TC-EMPTY``).
        """
        locations = [(HEAD_DEVICE, HEAD_ADDRESS)]
        layout.reserve_descriptor(HEAD_DEVICE, HEAD_ADDRESS, TCD_BYTES)
        device = HEAD_DEVICE
        for spec in specs:
            device = device if spec.next_device is None else spec.next_device
            address = spec.next_tcd_addr
            if address is None:
                address = layout.allocate(device, TCD_BYTES)
            elif not layout.free(device, address, TCD_BYTES):
                raise GeneratorError(
                    f"descriptor slot {device}:0x{address:06X} overlaps an "
                    "already-placed region"
                )
            layout.reserve_descriptor(device, address, TCD_BYTES)
            locations.append((device, address))
        return tuple(locations)

    def _place_transfer(self, spec, layout: _Layout) -> "tuple[int, int]":
        """Return ``(src_address, dest_address)`` honoring the spec's layout class.

        Every destination lands off every descriptor slot in the chain: a
        pinned ``dest_addr`` that hits a descriptor slot raises
        :class:`GeneratorError`, and an ``LAYOUT_EQUAL`` / ``LAYOUT_OVERLAP_*``
        request that would land there falls back to disjoint allocation
        instead of clobbering an unfetched TCD.
        """
        length = spec.transfer_len
        span = max(length, 1)

        if spec.src_addr is not None:
            src = spec.src_addr
            layout.occupy(spec.src_device, src, span)
        else:
            src = self.address_for(
                spec.src_class or self.address_class(), span, spec.src_device, layout
            )

        if spec.dest_addr is not None:
            dest = spec.dest_addr
            if layout.overlaps_descriptor(spec.dest_device, dest, span):
                raise GeneratorError(
                    f"pinned dest_addr {spec.dest_device}:0x{dest:06X}+{span} "
                    "overlaps a descriptor slot; firmware-legal generation must "
                    "not clobber a future TCD"
                )
            layout.occupy(spec.dest_device, dest, span)
            return src, dest

        layout_class = spec.layout or LAYOUT_DISJOINT
        same_device = spec.src_device == spec.dest_device
        if (
            same_device
            and layout_class == LAYOUT_EQUAL
            and not layout.overlaps_descriptor(spec.dest_device, src, span)
        ):
            return src, src
        if same_device and layout_class in (LAYOUT_OVERLAP_FORWARD, LAYOUT_OVERLAP_BACKWARD) and length > 1:
            shift = max(1, length // 2)
            dest = src + shift if layout_class == LAYOUT_OVERLAP_FORWARD else src - shift
            if (
                dest >= 0
                and dest + span - 1 <= ADDR_MAX
                and not layout.overlaps_descriptor(spec.dest_device, dest, span)
            ):
                layout.occupy(spec.dest_device, dest, span)
                return src, dest
        dest = self.address_for(
            spec.dest_class or self.address_class(), span, spec.dest_device, layout
        )
        return src, dest

    def _guard_spans(self, device: int, address: int, length: int):
        """Return candidate sentinel spans either side of a destination range."""
        count = self.guard_bytes
        if count <= 0:
            return []
        spans = []
        low = address - count
        if low >= 0:
            spans.append((device, low, count))
        high = address + length
        if high + count - 1 <= ADDR_MAX:
            spans.append((device, high, count))
        return spans

    def _write_guards(self, memory: MemoryImage, spans) -> "tuple[Region, ...]":
        """Define each candidate guard span that no other region already owns."""
        guards = []
        for device, address, count in spans:
            if any(memory.is_defined(device, address + offset) for offset in range(count)):
                continue
            memory.fill_range(device, address, count, self.guard_value)
            guards.append(guard_region(device, address, count))
        return tuple(guards)


def _merge_bias(bias: "dict | None") -> dict:
    """Return :data:`DEFAULT_BIAS` with *bias* overriding one level of keys."""
    merged = {key: _copy_entry(value) for key, value in DEFAULT_BIAS.items()}
    for key, value in (bias or {}).items():
        if key not in merged:
            raise GeneratorError(
                f"unknown bias dimension {key!r}; expected one of {sorted(merged)}"
            )
        if isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = _copy_entry(value)
    return merged


def _copy_entry(value):
    return dict(value) if isinstance(value, dict) else value


def _as_spec(spec) -> "TcdSpec":
    if isinstance(spec, TcdSpec):
        return spec
    if isinstance(spec, dict):
        return TcdSpec(**spec)
    raise GeneratorError(
        f"chain spec must be a TcdSpec or dict, got {type(spec).__name__}"
    )


def _quit_tcd(quit_spec: "TcdSpec | None") -> Tcd:
    """Return the terminating quit descriptor (fields may be nonzero but unexecuted)."""
    if quit_spec is None:
        return Tcd(quit=True)
    return Tcd(
        src_ptr=quit_spec.src_addr or 0,
        dest_ptr=quit_spec.dest_addr or 0,
        transfer_len=quit_spec.transfer_len,
        next_tcd=0,
        quit=True,
        src_device=quit_spec.src_device,
        dest_device=quit_spec.dest_device,
        next_device=0 if quit_spec.next_device is None else quit_spec.next_device,
        reserved=quit_spec.reserved,
    )


def len_addr_corner_specs(depth: int) -> "tuple[TcdSpec, ...]":
    """Specs that hit ``2N-1`` / ``2N`` / ``2N+1``, ``src=0``, and ``next`` at highest.

    First executable TCD sources PSRAM1 address 0 (head occupies PSRAM0
    ``0x000000``) and links the following descriptor to the highest legal
    11-byte slot so ``COV-ADDR`` ``src:zero`` and ``next:highest`` both fire.
    Lengths are the distinct in-range ``2N-*`` classes at *depth*.
    """
    highest = ADDR_MAX - TCD_BYTES + 1
    n = int(depth)
    lengths = []
    seen: "set[int]" = set()
    for raw in (2 * n - 1, 2 * n, 2 * n + 1):
        if 0 <= raw <= TRANSFER_LEN_MAX and raw not in seen:
            seen.add(raw)
            lengths.append(raw)
    if not lengths:
        raise GeneratorError(f"no distinct 2N length classes at depth {depth}")
    specs = []
    for index, length in enumerate(lengths):
        if index == 0:
            specs.append(
                TcdSpec(
                    transfer_len=length,
                    src_device=1,
                    dest_device=0,
                    next_device=0,
                    src_addr=0,
                    dest_addr=0x000200,
                    next_tcd_addr=highest,
                    pattern=PATTERN_INCREMENT,
                )
            )
            continue
        specs.append(
            TcdSpec(
                transfer_len=length,
                src_device=0,
                dest_device=1,
                pattern=PATTERN_INCREMENT,
            )
        )
    return tuple(specs)


def build_directed_chain(specs, *, seed: int = 0, **kwargs) -> GeneratedChain:
    """Convenience wrapper: one generator, one directed chain."""
    build_kwargs = {
        name: kwargs.pop(name)
        for name in ("quit_spec", "dma_buf_depth", "verify", "notes")
        if name in kwargs
    }
    return ChainGenerator(seed, **kwargs).build_directed(specs, **build_kwargs)


__all__ = [
    "ADDR_BOUNDARY_64K",
    "ADDR_HIGH",
    "ADDR_LOW",
    "ADDR_PAGE_EDGE",
    "ADDR_ZERO",
    "DEFAULT_BIAS",
    "DEST_SENTINEL",
    "GUARD_VALUE",
    "LAYOUT_DISJOINT",
    "LAYOUT_EQUAL",
    "LAYOUT_OVERLAP_BACKWARD",
    "LAYOUT_OVERLAP_FORWARD",
    "PATTERN_ALTERNATING",
    "PATTERN_INCREMENT",
    "PATTERN_ONES",
    "PATTERN_RANDOM",
    "PATTERN_WALKING",
    "PATTERN_ZERO",
    "REGION_DESCRIPTOR",
    "REGION_DESTINATION",
    "REGION_GUARD",
    "REGION_SOURCE",
    "STREAMS",
    "ChainGenerator",
    "GeneratedChain",
    "GeneratorError",
    "TcdSpec",
    "build_directed_chain",
    "len_addr_corner_specs",
]
