"""Pack/unpack covering BE, ptr[23], dest=1, reserved, QUIT (no test/ imports)."""

import pytest

from firmware.tcd import (
    CTRL_DEST_DEVICE_BIT,
    CTRL_NEXT_DEVICE_BIT,
    CTRL_QUIT_BIT,
    CTRL_SRC_DEVICE_BIT,
    PTR_BIT23,
    PTR_FIELD_MAX,
    TCD_BYTES,
    TC_TCD_BE_BYTES,
    TC_TCD_BE_TCD,
    Tcd,
    TcdError,
    ctrl_flags,
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
    assert MANDATORY_BYTES[10] == 0xA0  # frozen dest bit is 0


def test_quit_tcd_encodes_ctrl_flags_bit4():
    raw = encode_tcd(Tcd(quit=True))
    assert raw[10] == 0x10
    decoded = decode_tcd(raw)
    assert decoded.quit is True
    assert Tcd(quit=1) == decoded


def test_validate_rejects_nonzero_reserved_with_value():
    tcd = decode_tcd(bytes(10) + bytes([0x01]))
    with pytest.raises(TcdError, match="reserved=0x1") as error:
        validate_tcd(tcd)
    assert "CTRL_FLAGS[3:0]" in str(error.value)


def test_encode_rejects_reserved_and_oor():
    with pytest.raises(TcdError, match="reserved=0x1"):
        encode_tcd(Tcd(reserved=1))
    with pytest.raises(TcdError, match="src_ptr"):
        encode_tcd(Tcd(src_ptr=PTR_FIELD_MAX + 1))
    with pytest.raises(TcdError, match="transfer_len"):
        encode_tcd(Tcd(transfer_len=256))
    with pytest.raises(TcdError, match="dest_device"):
        encode_tcd(Tcd(dest_device=2))


def test_ptr23_roundtrip_on_src_dest_next():
    tcd = Tcd(
        src_ptr=PTR_BIT23 | 0x111111,
        dest_ptr=PTR_BIT23 | 0x222222,
        next_tcd=PTR_BIT23 | 0x333333,
        transfer_len=1,
    )
    encoded = encode_tcd(tcd)
    decoded = decode_tcd(encoded)
    assert decoded.src_ptr == tcd.src_ptr
    assert decoded.dest_ptr == tcd.dest_ptr
    assert decoded.next_tcd == tcd.next_tcd
    assert validate_tcd(decoded) is decoded


def test_dest_device_1_roundtrip_and_crossed_devices():
    tcd = Tcd(
        src_ptr=0x10,
        dest_ptr=0x20,
        transfer_len=2,
        next_tcd=0x30,
        src_device=0,
        dest_device=1,
        next_device=1,
    )
    raw = encode_tcd(tcd)
    assert (raw[10] >> CTRL_DEST_DEVICE_BIT) & 1 == 1
    assert (raw[10] >> CTRL_SRC_DEVICE_BIT) & 1 == 0
    assert (raw[10] >> CTRL_NEXT_DEVICE_BIT) & 1 == 1
    assert decode_tcd(raw) == tcd
    assert raw[10] == 0xC0


def test_ctrl_flags_rejects_oor_without_encode():
    with pytest.raises(TcdError, match="dest_device"):
        ctrl_flags(Tcd(dest_device=2))


def test_public_encode_always_validates():
    with pytest.raises(TcdError):
        encode_tcd(Tcd(reserved=0xF))
    with pytest.raises(TcdError):
        encode_tcd(Tcd(quit=2))
