"""Pure host checks that every directed HIL case builder is well-formed.

No Session and no hardware: each builder must produce a valid MemoryImage,
valid 11-byte TCDs at the expected descriptor addresses, and non-empty
expected_writes except for TC-QUIT / TC-EMPTY.
"""

from __future__ import annotations

import pytest

from hil.cases import get_all_cases
from test.reference.chain import HEAD_ADDRESS, HEAD_DEVICE, interpret_chain
from test.reference.tcd import TCD_BYTES, decode_tcd, validate_tcd

_EMPTY_WRITE_IDS = frozenset({"TC-QUIT", "TC-EMPTY"})


@pytest.mark.parametrize("case", get_all_cases(), ids=lambda c: c.id)
def test_case_builds_valid_memory_and_oracle(case):
    assert case.id
    assert case.name
    assert case.spans, f"{case.id}: spans must include installed TCDs/payload"

    # Re-interpret from the case image; must match the catalog's golden writes.
    golden = interpret_chain(case.initial_mem)
    assert dict(golden.expected_writes) == case.expected_writes

    if case.id in _EMPTY_WRITE_IDS:
        assert case.expected_writes == {}
        assert case.dump_extents == ()
    else:
        assert case.expected_writes, f"{case.id}: expected_writes must be non-empty"
        assert case.dump_extents, f"{case.id}: dump_extents must cover destinations"

    # Every fetched descriptor on the oracle path must be a valid 11-byte TCD.
    for device, address in golden.path:
        raw = case.initial_mem.read(device, address, TCD_BYTES)
        assert len(raw) == TCD_BYTES
        validate_tcd(decode_tcd(raw))

    # Fixed head is always present on PSRAM0.
    head = decode_tcd(case.initial_mem.read(HEAD_DEVICE, HEAD_ADDRESS, TCD_BYTES))
    validate_tcd(head)
