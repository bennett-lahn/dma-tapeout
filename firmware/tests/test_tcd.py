"""Pack/unpack and the copied TC_TCD_BE_BYTES vector (no test/ imports)."""

from firmware.tcd import (
    TCD_BYTES,
    TC_TCD_BE_BYTES,
    TC_TCD_BE_TCD,
    Tcd,
    decode_tcd,
    encode_tcd,
    validate_tcd,
)

# Restated independently of tcd.py so a bad copy cannot quietly redefine the vector.
MANDATORY_BYTES = bytes(
    [0x12, 0x34, 0x56, 0x23, 0x45, 0x67, 0x89, 0x34, 0x56, 0x78, 0xA0]
)
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


def test_tc_tcd_be_bytes_roundtrip():
    assert TC_TCD_BE_BYTES == MANDATORY_BYTES
    assert TC_TCD_BE_TCD == MANDATORY_TCD
    assert encode_tcd(MANDATORY_TCD) == MANDATORY_BYTES
    assert decode_tcd(MANDATORY_BYTES) == MANDATORY_TCD
    assert len(MANDATORY_BYTES) == TCD_BYTES


def test_quit_tcd_encodes_ctrl_flags_bit4():
    raw = encode_tcd(Tcd(quit=True))
    assert raw[10] == 0x10
    assert decode_tcd(raw).quit is True


def test_validate_rejects_nonzero_reserved():
    tcd = decode_tcd(bytes(10) + bytes([0x01]))
    try:
        validate_tcd(tcd)
    except Exception as error:
        assert "reserved" in str(error)
    else:
        raise AssertionError("expected reserved rejection")
