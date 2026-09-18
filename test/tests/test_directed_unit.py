"""Unit tests for directed pin-log zip and Wave 3 handle / nibble helpers."""

from types import SimpleNamespace

from common.directed import pin_by_kind, pin_log
from common.engine_bfm import bytes_to_nibbles, level_or_none


class _Handle:
    def __init__(self, value):
        self.value = value


def test_level_or_none_int():
    assert level_or_none(_Handle(1)) == 1
    assert level_or_none(_Handle(0)) == 0


def test_level_or_none_xz():
    class XZ:
        @property
        def value(self):
            raise ValueError("x")

    assert level_or_none(XZ()) is None


def test_bytes_to_nibbles_upper_first():
    assert bytes_to_nibbles(b"\xa5") == [0xA, 0x5]


def test_pin_log_zips_1to1():
    pin = [SimpleNamespace(device=0), SimpleNamespace(device=1)]
    golden = SimpleNamespace(
        transactions=[
            SimpleNamespace(kind="FETCH_READ"),
            SimpleNamespace(kind="DATA_READ"),
        ]
    )
    observed, expected = pin_log(pin, golden, test="TC-UNIT", repro="repro")
    assert len(observed) == 2
    assert expected[1].kind == "DATA_READ"


def test_pin_by_kind_filters_golden_kind():
    pin = [
        SimpleNamespace(device=0),
        SimpleNamespace(device=1),
        SimpleNamespace(device=1),
    ]
    golden = SimpleNamespace(
        transactions=[
            SimpleNamespace(kind="FETCH_READ"),
            SimpleNamespace(kind="DATA_READ"),
            SimpleNamespace(kind="DATA_WRITE"),
        ]
    )
    reads = pin_by_kind(pin, golden, "DATA_READ", test="TC-UNIT", repro="repro")
    assert [txn.device for txn in reads] == [1]
