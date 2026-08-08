"""11-byte TCD encode/decode/validate (``TC-TCD-BE`` unit vector).

Pure Python only; no cocotb imports. See ``docs/llm/verification/05-reference-model.md``.

Architecture constants (byte offsets, ``CTRL_FLAGS`` bit positions, pointer
width) are declared here as verification-side copies of ``../04-tcd-and-datapath.md``
and ``src/rtl/types.svh`` ``tcd_t``. They are never parsed out of SystemVerilog.

Representation and V1 validity are deliberately separate:

* :func:`decode_tcd` requires exactly 11 bytes and preserves every encoded bit,
  including ``reserved`` and a set pointer bit 23, so a negative test can decode
  and diagnose a malformed descriptor.
* :func:`validate_tcd` applies the V1 ranges and rejects nonzero ``reserved``.
* :func:`encode_tcd` validates first and never masks an out-of-range field into
  range.

Python ``bool`` is a subclass of ``int``, so integer fields reject booleans
explicitly instead of silently accepting ``True`` as ``1``.
"""

from dataclasses import dataclass

TCD_BYTES = 11

PTR_BITS = 23
PTR_MAX = (1 << PTR_BITS) - 1  # 0x7FFFFF, complete APS6404L address space
PTR_BIT23 = 1 << PTR_BITS  # device selection is CTRL_FLAGS, never this bit
TRANSFER_LEN_MAX = 0xFF
RESERVED_MAX = 0xF

OFFSET_SRC_PTR = 0
OFFSET_DEST_PTR = 3
OFFSET_TRANSFER_LEN = 6
OFFSET_NEXT_TCD = 7
OFFSET_CTRL_FLAGS = 10

CTRL_QUIT_BIT = 0
CTRL_SRC_DEVICE_BIT = 1
CTRL_DEST_DEVICE_BIT = 2
CTRL_NEXT_DEVICE_BIT = 3
CTRL_RESERVED_SHIFT = 4

POINTER_FIELDS = ("src_ptr", "dest_ptr", "next_tcd")
DEVICE_FIELDS = ("src_device", "dest_device", "next_device")


class ReferenceModelError(Exception):
    """Reference-axis error: invalid input, bad range, or exhausted budget.

    Never reported as a DUT mismatch (``05-reference-model.md``, mismatch
    diagnostics: ``axis=reference``).
    """


class TcdError(ReferenceModelError, ValueError):
    """A descriptor is not representable or not legal V1 stimulus."""


@dataclass(frozen=True)
class Tcd:
    """Decoded 11-byte transfer control descriptor.

    Field order is frozen for M2: ``src_ptr``, ``dest_ptr``, ``transfer_len``,
    ``next_tcd``, ``quit``, ``src_device``, ``dest_device``, ``next_device``,
    ``reserved``. Defaults describe the all-zero descriptor, so a quit
    descriptor is ``Tcd(quit=True)``.
    """

    src_ptr: int = 0
    dest_ptr: int = 0
    transfer_len: int = 0
    next_tcd: int = 0
    quit: bool = False
    src_device: int = 0
    dest_device: int = 0
    next_device: int = 0
    reserved: int = 0


# Mandatory unit vector from 05-reference-model.md. Unit tests restate the byte
# literal independently; this pair exists so stimulus code has one reference.
TC_TCD_BE_TCD = Tcd(
    src_ptr=0x123456,
    dest_ptr=0x234567,
    transfer_len=0x89,
    next_tcd=0x345678,
    quit=False,
    src_device=1,
    dest_device=0,
    next_device=1,
    reserved=0,
)
TC_TCD_BE_BYTES = bytes.fromhex("123456234567893456780A")


def _reject_bool(name: str, value) -> None:
    if isinstance(value, bool):
        raise TcdError(
            f"{name} must be an int, got bool {value!r}; bool is a subclass of "
            "int and is rejected explicitly for integer fields"
        )


def _check_integer(name: str, value, low: int, high: int) -> int:
    """Return *value* after rejecting bools, non-integers, and out-of-range."""
    _reject_bool(name, value)
    if not isinstance(value, int):
        raise TcdError(f"{name} must be an int, got {type(value).__name__} {value!r}")
    if value < low or value > high:
        raise TcdError(
            f"{name}={value} (0x{value:X}) outside 0x{low:X}..0x{high:X}; "
            "the encoder does not mask invalid input into range"
        )
    return value


def _check_pointer(name: str, value) -> int:
    """Range-check one 24-bit pointer field; bit 23 must be clear."""
    _reject_bool(name, value)
    if not isinstance(value, int):
        raise TcdError(f"{name} must be an int, got {type(value).__name__} {value!r}")
    if value < 0:
        raise TcdError(f"{name}={value} is negative")
    if value & PTR_BIT23:
        raise TcdError(
            f"{name}=0x{value:06X} has pointer bit 23 set; device selection comes "
            "only from CTRL_FLAGS and complete ranges live in 0x000000..0x7FFFFF"
        )
    if value > PTR_MAX:
        raise TcdError(f"{name}=0x{value:X} is past 0x{PTR_MAX:06X}")
    return value


def _check_quit(value) -> int:
    """Return ``0``/``1`` for a legal quit flag (``bool`` or ``0``/``1``)."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int) and value in (0, 1):
        return value
    raise TcdError(f"quit must be False/True or 0/1, got {value!r}")


def validate_tcd(tcd: Tcd) -> Tcd:
    """Apply V1 ranges to *tcd* and return it unchanged.

    Rejects booleans masquerading as integers, negative or oversized fields,
    pointer bit 23, and nonzero ``reserved``.

    Raises:
        TcdError: on any field outside the frozen V1 ranges.
    """
    if not isinstance(tcd, Tcd):
        raise TcdError(f"expected a Tcd value, got {type(tcd).__name__}")

    for name in POINTER_FIELDS:
        _check_pointer(name, getattr(tcd, name))
    _check_integer("transfer_len", tcd.transfer_len, 0, TRANSFER_LEN_MAX)
    _check_quit(tcd.quit)
    for name in DEVICE_FIELDS:
        _check_integer(name, getattr(tcd, name), 0, 1)
    _check_integer("reserved", tcd.reserved, 0, RESERVED_MAX)
    if tcd.reserved != 0:
        raise TcdError(
            f"reserved=0x{tcd.reserved:X} must be 0 for V1 stimulus "
            "(CTRL_FLAGS[7:4] is reserved)"
        )
    return tcd


def ctrl_flags(tcd: Tcd) -> int:
    """Return byte 10 of *tcd*: reserved, NEXT, DEST, SRC, QUIT."""
    return (
        (tcd.reserved << CTRL_RESERVED_SHIFT)
        | (tcd.next_device << CTRL_NEXT_DEVICE_BIT)
        | (tcd.dest_device << CTRL_DEST_DEVICE_BIT)
        | (tcd.src_device << CTRL_SRC_DEVICE_BIT)
        | (_check_quit(tcd.quit) << CTRL_QUIT_BIT)
    )


def encode_tcd(tcd: Tcd) -> bytes:
    """Serialize *tcd* to exactly 11 bytes, pointers most-significant byte first.

    Raises:
        TcdError: when *tcd* is not legal V1 stimulus.
    """
    validate_tcd(tcd)
    return bytes(
        [
            (tcd.src_ptr >> 16) & 0xFF,
            (tcd.src_ptr >> 8) & 0xFF,
            tcd.src_ptr & 0xFF,
            (tcd.dest_ptr >> 16) & 0xFF,
            (tcd.dest_ptr >> 8) & 0xFF,
            tcd.dest_ptr & 0xFF,
            tcd.transfer_len,
            (tcd.next_tcd >> 16) & 0xFF,
            (tcd.next_tcd >> 8) & 0xFF,
            tcd.next_tcd & 0xFF,
            ctrl_flags(tcd),
        ]
    )


def _as_bytes(raw) -> bytes:
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    if isinstance(raw, str):
        raise TcdError("descriptor bytes must be bytes-like, got str")
    try:
        data = bytes(raw)
    except (TypeError, ValueError) as error:
        raise TcdError(f"descriptor bytes must be bytes-like: {error}") from error
    return data


def decode_tcd(raw) -> Tcd:
    """Decode exactly 11 *raw* bytes, preserving reserved bits and pointer bit 23.

    Raises:
        TcdError: when *raw* is not exactly 11 bytes of byte values.
    """
    data = _as_bytes(raw)
    if len(data) != TCD_BYTES:
        raise TcdError(
            f"descriptor must be exactly {TCD_BYTES} bytes, got {len(data)} "
            f"({format_bytes(data)})"
        )
    flags = data[OFFSET_CTRL_FLAGS]
    return Tcd(
        src_ptr=int.from_bytes(data[OFFSET_SRC_PTR : OFFSET_SRC_PTR + 3], "big"),
        dest_ptr=int.from_bytes(data[OFFSET_DEST_PTR : OFFSET_DEST_PTR + 3], "big"),
        transfer_len=data[OFFSET_TRANSFER_LEN],
        next_tcd=int.from_bytes(data[OFFSET_NEXT_TCD : OFFSET_NEXT_TCD + 3], "big"),
        quit=bool((flags >> CTRL_QUIT_BIT) & 1),
        src_device=(flags >> CTRL_SRC_DEVICE_BIT) & 1,
        dest_device=(flags >> CTRL_DEST_DEVICE_BIT) & 1,
        next_device=(flags >> CTRL_NEXT_DEVICE_BIT) & 1,
        reserved=(flags >> CTRL_RESERVED_SHIFT) & RESERVED_MAX,
    )


def format_bytes(data) -> str:
    """Render bytes as uppercase space-separated hex (``12 34 56``)."""
    return " ".join(f"{value:02X}" for value in bytes(data))


def format_tcd(tcd: Tcd) -> str:
    """Render decoded fields on one diagnostic line."""
    return (
        f"src=0x{tcd.src_ptr:06X}(dev{tcd.src_device}) "
        f"dest=0x{tcd.dest_ptr:06X}(dev{tcd.dest_device}) "
        f"len={tcd.transfer_len} "
        f"next=0x{tcd.next_tcd:06X}(dev{tcd.next_device}) "
        f"quit={int(_check_quit(tcd.quit))} reserved=0x{tcd.reserved:X}"
    )


__all__ = [
    "CTRL_DEST_DEVICE_BIT",
    "CTRL_NEXT_DEVICE_BIT",
    "CTRL_QUIT_BIT",
    "CTRL_RESERVED_SHIFT",
    "CTRL_SRC_DEVICE_BIT",
    "PTR_BIT23",
    "PTR_MAX",
    "RESERVED_MAX",
    "TCD_BYTES",
    "TC_TCD_BE_BYTES",
    "TC_TCD_BE_TCD",
    "TRANSFER_LEN_MAX",
    "ReferenceModelError",
    "Tcd",
    "TcdError",
    "ctrl_flags",
    "decode_tcd",
    "encode_tcd",
    "format_bytes",
    "format_tcd",
    "validate_tcd",
]
