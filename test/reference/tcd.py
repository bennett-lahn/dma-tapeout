"""11-byte TCD encode/decode/validate (``TC-TCD-BE`` unit vector).

Pure Python only; no cocotb imports. See ``05-reference-model.md``.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tcd:
    """Decoded 11-byte transfer control descriptor."""

    src_ptr: int
    dest_ptr: int
    transfer_len: int
    next_tcd: int
    quit: bool
    src_device: int
    dest_device: int
    next_device: int
    reserved: int


def encode_tcd(tcd: Tcd) -> bytes:
    """Serialize *tcd* to exactly 11 big-endian pointer bytes.

    Raises:
        NotImplementedError: Phase 0 scaffold only.
    """
    raise NotImplementedError("M2+ implements TCD encode")


def decode_tcd(raw: bytes) -> Tcd:
    """Decode exactly 11 *raw* bytes, preserving reserved bits.

    Raises:
        NotImplementedError: Phase 0 scaffold only.
    """
    raise NotImplementedError("M2+ implements TCD decode")


def validate_tcd(tcd: Tcd) -> Tcd:
    """Apply V1 range checks; reject booleans masquerading as integers.

    Raises:
        NotImplementedError: Phase 0 scaffold only.
    """
    raise NotImplementedError("M2+ implements TCD validate")
