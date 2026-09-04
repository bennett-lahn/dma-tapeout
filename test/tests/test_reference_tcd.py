"""Pure-Python unit tests for the 11-byte TCD codec.

Test-case IDs:
    TC-TCD-BE (oracle half; the L1 DUT half lives in ``test_dma_directed.py``)

Runs under pytest without a simulator: nothing here imports cocotb. See
``docs/llm/verification/05-reference-model.md``.
"""

import pytest

from reference.tcd import (
    CTRL_DEST_DEVICE_BIT,
    CTRL_NEXT_DEVICE_BIT,
    CTRL_QUIT_BIT,
    CTRL_RESERVED_SHIFT,
    CTRL_SRC_DEVICE_BIT,
    PTR_BIT23,
    PTR_FIELD_MAX,
    PTR_MAX,
    TCD_BYTES,
    TC_TCD_BE_BYTES,
    TC_TCD_BE_TCD,
    Tcd,
    TcdError,
    decode_tcd,
    encode_tcd,
    validate_tcd,
)

# Restated independently of the module constants so a bad edit to tcd.py cannot
# quietly redefine the mandatory vector (05-reference-model.md).
MANDATORY_BYTES = bytes([0x12, 0x34, 0x56, 0x23, 0x45, 0x67, 0x89, 0x34, 0x56, 0x78, 0xA0])
MANDATORY_TCD = Tcd(
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

def test_mandatory_vector_encodes_exactly():
    assert encode_tcd(MANDATORY_TCD) == MANDATORY_BYTES
    assert len(MANDATORY_BYTES) == TCD_BYTES

def test_mandatory_vector_decodes_exactly():
    assert decode_tcd(MANDATORY_BYTES) == MANDATORY_TCD

def test_module_vector_matches_restated_vector():
    assert TC_TCD_BE_BYTES == MANDATORY_BYTES
    assert TC_TCD_BE_TCD == MANDATORY_TCD

def test_dest1_bit23_module_vector_matches_restated():
    from reference.tcd import TC_TCD_DEST1_BIT23_BYTES, TC_TCD_DEST1_BIT23_TCD

    assert TC_TCD_DEST1_BIT23_BYTES == DEST1_BIT23_BYTES
    assert TC_TCD_DEST1_BIT23_TCD == DEST1_BIT23_TCD

# Destination-1 vector with ptr[23]=1 (cov-refu-02 / cov-refu-07). Frozen A0
# vector above stays dest_device=0. Restated independently of tcd.py.
DEST1_BIT23_BYTES = bytes([0x12, 0x34, 0x56, 0x81, 0x00, 0x00, 0x04, 0x34, 0x56, 0x78, 0x40])
DEST1_BIT23_TCD = Tcd(
    src_ptr=0x123456,
    dest_ptr=PTR_BIT23 | 0x010000,
    transfer_len=0x04,
    next_tcd=0x345678,
    quit=False,
    src_device=0,
    dest_device=1,
    next_device=0,
    reserved=0,
)

def test_dest1_bit23_vector_encodes_and_decodes():
    """cov-refu-02: dest_device=1 with dest_ptr[23]=1 round-trips."""
    assert encode_tcd(DEST1_BIT23_TCD) == DEST1_BIT23_BYTES
    assert decode_tcd(DEST1_BIT23_BYTES) == DEST1_BIT23_TCD
    assert DEST1_BIT23_TCD.dest_device == 1
    assert DEST1_BIT23_TCD.dest_ptr & PTR_BIT23
    assert (DEST1_BIT23_TCD.dest_ptr & PTR_MAX) == 0x010000

def test_pointers_are_big_endian_per_offset():
    raw = encode_tcd(MANDATORY_TCD)
    assert raw[0:3] == bytes([0x12, 0x34, 0x56])
    assert raw[3:6] == bytes([0x23, 0x45, 0x67])
    assert raw[6] == 0x89
    assert raw[7:10] == bytes([0x34, 0x56, 0x78])

@pytest.mark.parametrize(
    "field, bit",
    [
        ("quit", CTRL_QUIT_BIT),
        ("src_device", CTRL_SRC_DEVICE_BIT),
        ("dest_device", CTRL_DEST_DEVICE_BIT),
        ("next_device", CTRL_NEXT_DEVICE_BIT),
    ],
)
def test_each_ctrl_flag_owns_one_bit(field, bit):
    """One flag set at a time lands only on its assigned CTRL_FLAGS bit."""
    value = True if field == "quit" else 1
    raw = encode_tcd(Tcd(**{field: value}))
    assert raw[10] == 1 << bit
    assert raw[:10] == bytes(10)
    assert getattr(decode_tcd(raw), field) in (1, True)

def test_all_flags_together_decode_at_their_positions():
    raw = encode_tcd(
        Tcd(quit=True, src_device=1, dest_device=1, next_device=1)
    )
    assert raw[10] == 0xF0
    decoded = decode_tcd(raw)
    assert (decoded.quit, decoded.src_device, decoded.dest_device, decoded.next_device) == (
        True,
        1,
        1,
        1,
    )
    assert decoded.reserved == 0

@pytest.mark.parametrize(
    "tcd",
    [
        Tcd(),
        Tcd(quit=True),
        Tcd(src_ptr=PTR_MAX, dest_ptr=PTR_MAX, next_tcd=PTR_MAX, transfer_len=255),
        Tcd(src_ptr=0x000001, dest_ptr=0x7FFFFF, transfer_len=1, next_tcd=0x010000),
        Tcd(transfer_len=255, src_device=1, dest_device=0, next_device=1),
        Tcd(src_ptr=0x00FFFF, dest_ptr=0x010000, transfer_len=0, next_tcd=0x000000),
        MANDATORY_TCD,
    ],
)
def test_encode_decode_round_trip(tcd):
    raw = encode_tcd(tcd)
    assert len(raw) == TCD_BYTES
    assert decode_tcd(raw) == tcd

def test_round_trip_over_swept_flag_combinations():
    for flags in range(16):
        tcd = Tcd(
            src_ptr=0x0123AB,
            dest_ptr=0x004000,
            transfer_len=17,
            next_tcd=0x000020,
            quit=bool(flags & 1),
            src_device=(flags >> 1) & 1,
            dest_device=(flags >> 2) & 1,
            next_device=(flags >> 3) & 1,
        )
        assert decode_tcd(encode_tcd(tcd)) == tcd

@pytest.mark.parametrize(
    "tcd",
    [
        Tcd(src_ptr=-1),
        Tcd(dest_ptr=-1),
        Tcd(next_tcd=0x1000000),
        Tcd(src_ptr=PTR_FIELD_MAX + 1),
        Tcd(transfer_len=256),
        Tcd(transfer_len=-1),
        Tcd(src_device=2),
        Tcd(dest_device=-1),
        Tcd(next_device=2),
        Tcd(reserved=1),
        Tcd(reserved=0xF),
        Tcd(reserved=0x10),
    ],
)
def test_encode_rejects_illegal_fields(tcd):
    with pytest.raises(TcdError):
        encode_tcd(tcd)

def test_encode_accepts_pointer_bit_23():
    """D35: ptr[23] is don't-care and may be set."""
    encoded = encode_tcd(Tcd(src_ptr=PTR_BIT23 | 0x123456))
    assert decode_tcd(encoded).src_ptr == PTR_BIT23 | 0x123456

@pytest.mark.parametrize(
    "tcd",
    [
        Tcd(src_ptr=True),
        Tcd(dest_ptr=False),
        Tcd(transfer_len=True),
        Tcd(src_device=True),
        Tcd(dest_device=True),
        Tcd(next_device=True),
        Tcd(reserved=True),
    ],
)
def test_integer_fields_reject_booleans(tcd):
    """bool is a subclass of int, so range checks must reject it explicitly."""
    with pytest.raises(TcdError):
        validate_tcd(tcd)

@pytest.mark.parametrize(
    "tcd",
    [Tcd(src_ptr=0.0), Tcd(transfer_len="1"), Tcd(next_tcd=None), Tcd(quit="yes")],
)
def test_non_integer_fields_are_rejected(tcd):
    with pytest.raises(TcdError):
        encode_tcd(tcd)

def test_encode_does_not_mask_out_of_range_into_range():
    """0x1000000 must fail, not silently encode as 0x000000."""
    with pytest.raises(TcdError):
        encode_tcd(Tcd(src_ptr=0x1000000))

def test_encode_accepts_bit23_only_pointer():
    """0x800000 is a legal 24-bit field (A[22:0]=0 with bit 23 set)."""
    encoded = encode_tcd(Tcd(src_ptr=0x800000))
    assert decode_tcd(encoded).src_ptr == 0x800000

def test_quit_accepts_bool_or_zero_one():
    assert encode_tcd(Tcd(quit=1))[10] == 1 << CTRL_QUIT_BIT
    assert encode_tcd(Tcd(quit=0))[10] == 0
    with pytest.raises(TcdError):
        encode_tcd(Tcd(quit=2))

def test_validate_returns_the_same_value():
    assert validate_tcd(MANDATORY_TCD) is MANDATORY_TCD

def test_validate_rejects_non_tcd():
    with pytest.raises(TcdError):
        validate_tcd(MANDATORY_BYTES)

@pytest.mark.parametrize("length", [0, 10, 12, 22])
def test_decode_requires_exactly_eleven_bytes(length):
    with pytest.raises(TcdError):
        decode_tcd(bytes(length))

def test_decode_preserves_reserved_bits():
    raw = bytearray(MANDATORY_BYTES)
    raw[10] = (0xB << CTRL_RESERVED_SHIFT) | 0xA0
    decoded = decode_tcd(bytes(raw))
    assert decoded.reserved == 0xB
    assert decoded.src_device == 1 and decoded.next_device == 1

def test_decoded_nonzero_reserved_fails_validation_not_decoding():
    raw = bytearray(MANDATORY_BYTES)
    raw[10] |= 0x01
    decoded = decode_tcd(bytes(raw))
    assert decoded.reserved == 1
    with pytest.raises(TcdError):
        validate_tcd(decoded)

def test_decode_preserves_pointer_bit_23():
    raw = bytearray(MANDATORY_BYTES)
    raw[0] = 0x92  # src_ptr = 0x923456, bit 23 set
    decoded = decode_tcd(bytes(raw))
    assert decoded.src_ptr == 0x923456
    assert validate_tcd(decoded) is decoded

def test_decode_accepts_bytearray_and_memoryview():
    assert decode_tcd(bytearray(MANDATORY_BYTES)) == MANDATORY_TCD
    assert decode_tcd(memoryview(MANDATORY_BYTES)) == MANDATORY_TCD

def test_decode_rejects_text():
    with pytest.raises(TcdError):
        decode_tcd("12 34 56 23 45 67 89 34 56 78 0A")
