"""Directed HIL case catalog: MemoryImage layouts plus golden expected writes.

Each `TC-*` (directed-test verification ID) builder installs 11-byte TCDs
(transfer control descriptors) and source bytes into a `MemoryImage`, then
runs `interpret_chain` at tapeout `DMA_BUF_DEPTH` N=5 so destination dumps can
be compared without a second oracle. Host code may import `test.reference`
directly; this package must never be pulled into MicroPython firmware.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TEST = _REPO / "test"
for _path in (str(_REPO), str(_TEST)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from test.reference.chain import (  # noqa: E402
    DEFAULT_DMA_BUF_DEPTH,
    HEAD_ADDRESS,
    HEAD_DEVICE,
    MemoryImage,
    interpret_chain,
)
from hil.checks import collapse_extents, contiguous_spans  # noqa: E402
from test.reference.constants import PAGE_SIZE, PTR_BIT23, PTR_MAX  # noqa: E402
from test.reference.tcd import Tcd, encode_tcd, validate_tcd  # noqa: E402

# Descriptor slots sit below the payload region so installs never collide.
_SLOT = 0x20
_QUIT_SLOT = 0x000020
_PAYLOAD_BASE = 0x000100


@dataclass(frozen=True)
class HilCase:
    """One directed HIL stimulus window with oracle-backed expectations."""

    id: str
    name: str
    markers: tuple[str, ...]
    initial_mem: MemoryImage
    spans: tuple[tuple[int, int, bytes], ...]
    expected_writes: dict[tuple[int, int], int]
    dump_extents: tuple[tuple[int, int, int], ...]


def _place(mem: MemoryImage, device: int, address: int, tcd: Tcd) -> None:
    """Encode and install one validated TCD."""
    mem.write(device, address, encode_tcd(validate_tcd(tcd)))


def _increment(length: int, start: int = 0x40) -> bytes:
    return bytes((start + offset) & 0xFF for offset in range(length))


def build_case(case_id: str, name: str, build_fn, markers=()) -> HilCase:
    """Build one case: install stimulus, interpret at N=5, derive spans/dumps."""
    mem = MemoryImage(fill=0x00)
    build_fn(mem)
    result = interpret_chain(mem, dma_buf_depth=DEFAULT_DMA_BUF_DEPTH)
    expected = dict(result.expected_writes)
    return HilCase(
        id=case_id,
        name=name,
        markers=tuple(markers),
        initial_mem=mem,
        spans=contiguous_spans(mem),
        expected_writes=expected,
        dump_extents=collapse_extents(expected),
    )


def _install_copy(
    mem: MemoryImage,
    *,
    src_device: int,
    dest_device: int,
    length: int,
    src_addr: int = _PAYLOAD_BASE,
    dest_addr: int | None = None,
    next_tcd: int = _QUIT_SLOT,
    next_device: int = HEAD_DEVICE,
    payload: bytes | None = None,
) -> None:
    """Head data TCD plus terminating QUIT."""
    if dest_addr is None:
        dest_addr = src_addr + 0x100
    data = _increment(length) if payload is None else payload
    if length:
        mem.write(src_device, src_addr & PTR_MAX, data)
    _place(
        mem,
        HEAD_DEVICE,
        HEAD_ADDRESS,
        Tcd(
            src_ptr=src_addr & PTR_MAX,
            dest_ptr=dest_addr & PTR_MAX,
            transfer_len=length,
            next_tcd=next_tcd & PTR_MAX,
            src_device=src_device,
            dest_device=dest_device,
            next_device=next_device,
        ),
    )
    _place(mem, next_device, next_tcd & PTR_MAX, Tcd(quit=True))


def case_smoke() -> HilCase:
    """TC-SMOKE: PSRAM0-to-PSRAM0 8-byte copy then QUIT."""

    def build(mem: MemoryImage) -> None:
        _install_copy(mem, src_device=0, dest_device=0, length=8)

    return build_case(
        "TC-SMOKE",
        "PSRAM0-to-PSRAM0 8-byte copy + QUIT",
        build,
        markers=("smoke", "same_device"),
    )


def case_same_0() -> HilCase:
    """TC-SAME-0: PSRAM0-to-PSRAM0 copy."""

    def build(mem: MemoryImage) -> None:
        _install_copy(
            mem,
            src_device=0,
            dest_device=0,
            length=8,
            src_addr=0x000180,
            dest_addr=0x000280,
        )

    return build_case(
        "TC-SAME-0",
        "PSRAM0-to-PSRAM0 copy",
        build,
        markers=("same_device",),
    )


def case_same_1() -> HilCase:
    """TC-SAME-1: PSRAM1-to-PSRAM1 copy after head fetch on PSRAM0."""

    def build(mem: MemoryImage) -> None:
        _install_copy(
            mem,
            src_device=1,
            dest_device=1,
            length=8,
            src_addr=0x000180,
            dest_addr=0x000280,
        )

    return build_case(
        "TC-SAME-1",
        "PSRAM1-to-PSRAM1 copy",
        build,
        markers=("same_device",),
    )


def case_cross_01() -> HilCase:
    """TC-CROSS-01: PSRAM0 source to PSRAM1 destination."""

    def build(mem: MemoryImage) -> None:
        _install_copy(
            mem,
            src_device=0,
            dest_device=1,
            length=8,
            src_addr=0x000180,
            dest_addr=0x000280,
        )

    return build_case(
        "TC-CROSS-01",
        "PSRAM0 to PSRAM1 copy",
        build,
        markers=("cross_device",),
    )


def case_cross_10() -> HilCase:
    """TC-CROSS-10: PSRAM1 source to PSRAM0 destination."""

    def build(mem: MemoryImage) -> None:
        _install_copy(
            mem,
            src_device=1,
            dest_device=0,
            length=8,
            src_addr=0x000180,
            dest_addr=0x000280,
        )

    return build_case(
        "TC-CROSS-10",
        "PSRAM1 to PSRAM0 copy",
        build,
        markers=("cross_device",),
    )


def case_chain() -> HilCase:
    """TC-CHAIN: at least three data TCDs chained then QUIT."""

    def build(mem: MemoryImage) -> None:
        slots = (0x000000, 0x000040, 0x000080, 0x0000C0)
        sources = (0x001000, 0x002000, 0x003000)
        dests = (0x011000, 0x012000, 0x013000)
        payloads = (b"\xA0\xA1\xA2", b"\xB0\xB1\xB2\xB3\xB4", b"\xC0\xC1")
        for index, payload in enumerate(payloads):
            src_dev = 0 if index != 2 else 1
            dest_dev = 0 if index == 0 else (1 if index == 1 else 0)
            mem.write(src_dev, sources[index], payload)
            _place(
                mem,
                HEAD_DEVICE,
                slots[index],
                Tcd(
                    src_ptr=sources[index],
                    dest_ptr=dests[index],
                    transfer_len=len(payload),
                    next_tcd=slots[index + 1],
                    src_device=src_dev,
                    dest_device=dest_dev,
                    next_device=HEAD_DEVICE,
                ),
            )
        _place(mem, HEAD_DEVICE, slots[3], Tcd(quit=True))

    return build_case(
        "TC-CHAIN",
        "three data TCDs + QUIT",
        build,
        markers=("chain",),
    )


def case_next_device() -> HilCase:
    """TC-NEXT-DEVICE: alternating descriptor fetch between device 0 and 1."""

    def build(mem: MemoryImage) -> None:
        # Fetch path: PSRAM0:0 -> PSRAM1:0 -> PSRAM0:0x40 -> PSRAM1:0x60 (QUIT).
        mem.write(0, 0x000200, b"\x11\x22")
        mem.write(1, 0x000200, b"\x33\x44")
        mem.write(0, 0x000300, b"\x55\x66")
        _place(
            mem,
            0,
            HEAD_ADDRESS,
            Tcd(
                src_ptr=0x000200,
                dest_ptr=0x000280,
                transfer_len=2,
                next_tcd=0x000000,
                next_device=1,
            ),
        )
        _place(
            mem,
            1,
            0x000000,
            Tcd(
                src_ptr=0x000200,
                dest_ptr=0x000280,
                transfer_len=2,
                next_tcd=0x000040,
                src_device=1,
                dest_device=1,
                next_device=0,
            ),
        )
        _place(
            mem,
            0,
            0x000040,
            Tcd(
                src_ptr=0x000300,
                dest_ptr=0x000380,
                transfer_len=2,
                next_tcd=0x000060,
                next_device=1,
            ),
        )
        _place(mem, 1, 0x000060, Tcd(quit=True))

    return build_case(
        "TC-NEXT-DEVICE",
        "alternating NEXT_DEVICE descriptor fetches",
        build,
        markers=("next_device", "chain"),
    )


def case_length_corners() -> HilCase:
    """TC-LEN-CORNERS: lengths 0, 1, 4, 5, 6, 9, 10, 11, 255 at N=5."""

    def build(mem: MemoryImage) -> None:
        lengths = (0, 1, 4, 5, 6, 9, 10, 11, 255)
        slot_base = 0x000000
        for index, length in enumerate(lengths):
            slot = slot_base + index * _SLOT
            next_slot = slot_base + (index + 1) * _SLOT
            src_addr = 0x001000 + index * 0x200
            dest_addr = 0x010000 + index * 0x200
            if length:
                mem.write(0, src_addr, _increment(length, start=(index * 17) & 0xFF))
            _place(
                mem,
                HEAD_DEVICE,
                slot,
                Tcd(
                    src_ptr=src_addr,
                    dest_ptr=dest_addr,
                    transfer_len=length,
                    next_tcd=next_slot,
                ),
            )
        quit_slot = slot_base + len(lengths) * _SLOT
        _place(mem, HEAD_DEVICE, quit_slot, Tcd(quit=True))

    return build_case(
        "TC-LEN-CORNERS",
        "length corners at N=5",
        build,
        markers=("length",),
    )


def case_quit() -> HilCase:
    """TC-QUIT: QUIT=1 with non-zero length and pointers (not executed)."""

    def build(mem: MemoryImage) -> None:
        _place(
            mem,
            HEAD_DEVICE,
            HEAD_ADDRESS,
            Tcd(
                src_ptr=0x001234,
                dest_ptr=0x005678,
                transfer_len=0x22,
                next_tcd=0x00ABCD,
                quit=True,
                src_device=1,
                dest_device=0,
                next_device=1,
            ),
        )
        # Decoy payload that must never be copied.
        mem.write(1, 0x001234, b"\xA5" * 0x22)

    return build_case(
        "TC-QUIT",
        "QUIT with nonzero fields at head",
        build,
        markers=("quit",),
    )


def case_empty() -> HilCase:
    """TC-EMPTY: QUIT at fixed head 0x000000 on PSRAM0."""

    def build(mem: MemoryImage) -> None:
        _place(mem, HEAD_DEVICE, HEAD_ADDRESS, Tcd(quit=True))

    return build_case(
        "TC-EMPTY",
        "QUIT at fixed head",
        build,
        markers=("quit", "smoke"),
    )


def case_restart() -> HilCase:
    """TC-RESTART: layout that a second START must re-fetch from the fixed head."""

    def build(mem: MemoryImage) -> None:
        _install_copy(
            mem,
            src_device=0,
            dest_device=0,
            length=5,
            src_addr=0x0001A0,
            dest_addr=0x0002A0,
        )

    return build_case(
        "TC-RESTART",
        "restart re-fetches fixed head",
        build,
        markers=("restart", "start"),
    )


def case_address_corners() -> HilCase:
    """TC-ADDR-WIDE: below 64k, >=64k, 1k page cross, near top, ptr[23]=1."""

    def build(mem: MemoryImage) -> None:
        corners = (
            # below 64k
            (0x000800, 0x000900, 4, 0, 0),
            # >= 64k boundary
            (0x010000, 0x010100, 4, 0, 0),
            # 1 KiB page cross (PAGE_SIZE): span crosses a page edge
            (PAGE_SIZE - 2, 0x002000, 4, 0, 0),
            # near 0x7FFFFF
            (PTR_MAX - 3, 0x003000, 4, 0, 0),
            # ptr[23]=1 don't-care (D35) on SRC/DEST/NEXT
            (0x000400, 0x000500, 4, 1, PTR_BIT23),
        )
        for index, (src, dest, length, dest_dev, bit23) in enumerate(corners):
            slot = index * _SLOT
            next_slot = (index + 1) * _SLOT
            mem.write(0, src & PTR_MAX, _increment(length, start=0x10 + index))
            next_bits = bit23 if index == len(corners) - 1 else 0
            _place(
                mem,
                HEAD_DEVICE,
                slot,
                Tcd(
                    src_ptr=(src & PTR_MAX) | bit23,
                    dest_ptr=(dest & PTR_MAX) | bit23,
                    transfer_len=length,
                    next_tcd=(next_slot & PTR_MAX) | next_bits,
                    dest_device=dest_dev,
                    next_device=HEAD_DEVICE,
                ),
            )
        _place(mem, HEAD_DEVICE, len(corners) * _SLOT, Tcd(quit=True))

    return build_case(
        "TC-ADDR-WIDE",
        "address corners including ptr[23] don't-care",
        build,
        markers=("address",),
    )


def case_overlap() -> HilCase:
    """TC-OVERLAP: same-device overlapping src/dest showing N=5 chunk behavior."""

    def build(mem: MemoryImage) -> None:
        # Forward overlap, length N+1=6 so two chunks (5 then 1) at tapeout depth.
        length = DEFAULT_DMA_BUF_DEPTH + 1
        src = 0x001000
        dest = src + 2
        mem.write(0, src, _increment(length + 2, start=1))
        _place(
            mem,
            HEAD_DEVICE,
            HEAD_ADDRESS,
            Tcd(
                src_ptr=src,
                dest_ptr=dest,
                transfer_len=length,
                next_tcd=_QUIT_SLOT,
            ),
        )
        _place(mem, HEAD_DEVICE, _QUIT_SLOT, Tcd(quit=True))

    return build_case(
        "TC-OVERLAP",
        "overlapping src/dest at N=5 chunking",
        build,
        markers=("overlap",),
    )


def get_all_cases() -> list[HilCase]:
    """Return every directed HIL case in catalog order."""
    return [
        case_smoke(),
        case_same_0(),
        case_same_1(),
        case_cross_01(),
        case_cross_10(),
        case_chain(),
        case_next_device(),
        case_length_corners(),
        case_quit(),
        case_empty(),
        case_restart(),
        case_address_corners(),
        case_overlap(),
    ]


__all__ = [
    "HilCase",
    "build_case",
    "case_address_corners",
    "case_chain",
    "case_cross_01",
    "case_cross_10",
    "case_empty",
    "case_length_corners",
    "case_next_device",
    "case_overlap",
    "case_quit",
    "case_restart",
    "case_same_0",
    "case_same_1",
    "case_smoke",
    "get_all_cases",
]
