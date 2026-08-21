"""Pure-Python unit tests for the dual-axis scoreboard.

Covers ordered-log equality, final-memory classification and guards, the
reset-prefix rules behind ``TC-RESET-ACTIVE``, and the two-epoch equality
``TC-RESET-REPEAT`` depends on. No cocotb import.

See ``docs/llm/verification/05-reference-model.md``.
"""

import pytest

from reference.chain import (
    DATA_READ,
    DATA_WRITE,
    FETCH_READ,
    OBSERVED_READ,
    OBSERVED_WRITE,
    MemoryImage,
    interpret_chain,
    transaction,
)
from reference.scoreboard import (
    AXIS_MEMORY,
    AXIS_TRANSACTIONS,
    CLASS_MISSING_WRITE,
    CLASS_UNEXPECTED_WRITE,
    CLASS_WRONG_DATA,
    RunContext,
    Scoreboard,
    ScoreboardError,
    compare_epoch_logs,
    guard_region,
)
from common.constants import DST_ADDR, QUIT_ADDR, SRC_ADDR
from reference.tcd import Tcd, encode_tcd
PAYLOAD = b"\x11\x22"

CONTEXT = RunContext(level="L1", sim="icarus", seed=1, depth=1, test="unit", repro="REPRO: unit")


def build_chain(transfer_len: int = 2, dest_device: int = 0) -> MemoryImage:
    memory = MemoryImage(fill=0x00)
    memory.write(
        0,
        0x000000,
        encode_tcd(
            Tcd(
                src_ptr=SRC_ADDR,
                dest_ptr=DST_ADDR,
                transfer_len=transfer_len,
                next_tcd=QUIT_ADDR,
                dest_device=dest_device,
            )
        ),
    )
    memory.write(0, QUIT_ADDR, encode_tcd(Tcd(quit=True)))
    memory.write(0, SRC_ADDR, PAYLOAD[:transfer_len])
    return memory


def oracle(transfer_len: int = 2, dest_device: int = 0, depth: int = 1):
    return interpret_chain(build_chain(transfer_len, dest_device), depth)


def scoreboard(result, **kwargs) -> Scoreboard:
    return Scoreboard.from_result(result, context=CONTEXT, **kwargs)


def observed_from(result, *, neutral: bool = False) -> list:
    """Return an observed log that mirrors the oracle, optionally pin-neutral."""
    records = []
    for txn in result.transactions:
        kind = txn.kind
        if neutral:
            kind = OBSERVED_WRITE if txn.kind == DATA_WRITE else OBSERVED_READ
        records.append(
            transaction(
                txn.index, kind, txn.device, txn.address, txn.data, start_time_ns=10.0 * txn.index
            )
        )
    return records


# -- axis 1: ordered transactions -----------------------------------------


def test_matching_log_passes_both_axes():
    result = oracle()
    board = scoreboard(result)
    board.compare(observed_from(result), result.final_memory)


def test_neutral_read_write_records_are_classified():
    result = oracle()
    board = scoreboard(result)
    board.compare_transactions(observed_from(result, neutral=True))


def test_read_at_a_non_fetch_position_classifies_as_data_read():
    result = oracle()
    board = scoreboard(result)
    neutral = observed_from(result, neutral=True)
    resolved = [board.classify_observed(record, index) for index, record in enumerate(neutral)]
    assert [record.kind for record in resolved] == [
        FETCH_READ,
        DATA_READ,
        DATA_WRITE,
        DATA_READ,
        DATA_WRITE,
        FETCH_READ,
    ]


def test_missing_transaction_fails_axis_one():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)
    del observed[2]
    with pytest.raises(ScoreboardError) as error:
        board.compare_transactions(observed)
    assert error.value.axis == AXIS_TRANSACTIONS
    assert "axis=transactions" in str(error.value)


def test_extra_transaction_fails_axis_one():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)
    observed.append(transaction(len(observed), DATA_WRITE, 0, DST_ADDR, b"\x11"))
    with pytest.raises(ScoreboardError) as error:
        board.compare_transactions(observed)
    assert "<missing>" in str(error.value)


def test_duplicated_transaction_fails_axis_one():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)
    observed.insert(2, observed[2])
    with pytest.raises(ScoreboardError):
        board.compare_transactions(_renumber(observed))


def test_reordered_transactions_fail_axis_one():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)
    observed[1], observed[2] = observed[2], observed[1]
    with pytest.raises(ScoreboardError) as error:
        board.compare_transactions(_renumber(observed))
    assert "first mismatching index=1" in str(error.value)


def test_wrong_device_is_reported_field_by_field():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)
    observed[2] = transaction(2, DATA_WRITE, 1, DST_ADDR, observed[2].data)
    with pytest.raises(ScoreboardError) as error:
        board.compare_transactions(observed)
    message = str(error.value)
    assert "device: expected=0 observed=1" in message
    assert "expected context:" in message and "observed context:" in message


def test_wrong_chunk_length_is_reported():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)
    observed[1] = transaction(1, DATA_READ, 0, SRC_ADDR, PAYLOAD)
    with pytest.raises(ScoreboardError) as error:
        board.compare_transactions(observed)
    assert "length: expected=1 observed=2" in str(error.value)


def test_compensating_write_is_caught_by_the_transaction_axis():
    """Wrong source data plus a later fixing write leaves memory correct."""
    result = oracle(transfer_len=1)
    board = scoreboard(result)
    observed = observed_from(result)
    observed[1] = transaction(1, DATA_READ, 0, SRC_ADDR, b"\x00")
    observed.insert(2, transaction(2, DATA_WRITE, 0, DST_ADDR, b"\x00"))
    del observed[3]
    observed.append(transaction(0, DATA_WRITE, 0, DST_ADDR, PAYLOAD[:1]))
    with pytest.raises(ScoreboardError):
        board.compare_transactions(_renumber(observed))
    # Memory alone would have passed, which is why both axes are required.
    board.compare_memory(result.final_memory)


def test_illegal_data_transaction_after_quit_fails():
    result = oracle(transfer_len=0)
    board = scoreboard(result)
    observed = observed_from(result)
    observed.append(transaction(len(observed), DATA_READ, 0, SRC_ADDR, b"\x11"))
    with pytest.raises(ScoreboardError):
        board.compare_transactions(observed)


def test_index_mismatch_is_reported():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)
    observed[3] = observed[3].with_index(9)
    with pytest.raises(ScoreboardError) as error:
        board.compare_transactions(observed)
    assert "index: expected=3 observed=9" in str(error.value)


# -- axis 2: final memory --------------------------------------------------


def test_missing_write_is_classified():
    result = oracle()
    board = scoreboard(result)
    observed = result.final_memory.clone()
    observed.poke(0, DST_ADDR, result.initial_memory.byte(0, DST_ADDR))
    with pytest.raises(ScoreboardError) as error:
        board.compare_memory(observed)
    assert error.value.axis == AXIS_MEMORY
    assert CLASS_MISSING_WRITE in str(error.value)
    assert board.mismatches[0].address == DST_ADDR
    assert board.mismatches[0].region == "destination"


def test_wrong_data_is_classified():
    result = oracle()
    board = scoreboard(result)
    observed = result.final_memory.clone()
    observed.poke(0, DST_ADDR + 1, 0x5A)
    with pytest.raises(ScoreboardError) as error:
        board.compare_memory(observed)
    assert CLASS_WRONG_DATA in str(error.value)
    assert f"addr=0x{DST_ADDR + 1:06X}" in str(error.value)


def test_unexpected_write_outside_the_expected_set_is_classified():
    result = oracle()
    board = scoreboard(result)
    observed = result.final_memory.clone()
    observed.poke(1, 0x004000, 0x77)
    with pytest.raises(ScoreboardError) as error:
        board.compare_memory(observed)
    assert CLASS_UNEXPECTED_WRITE in str(error.value)
    assert "dev=1" in str(error.value)


def test_guard_bytes_are_compared():
    result = oracle()
    board = scoreboard(result, guards=[guard_region(0, DST_ADDR + 8, 4)])
    observed = result.final_memory.clone()
    observed.poke(0, DST_ADDR + 9, 0x01)
    with pytest.raises(ScoreboardError) as error:
        board.compare_memory(observed)
    assert "region=guard" in str(error.value)


def test_source_region_corruption_is_caught():
    result = oracle()
    board = scoreboard(result)
    observed = result.final_memory.clone()
    observed.poke(0, SRC_ADDR, 0x00)
    with pytest.raises(ScoreboardError) as error:
        board.compare_memory(observed)
    assert "region=source" in str(error.value)


def test_observed_memory_may_be_a_device_mapping():
    result = oracle()
    board = scoreboard(result)
    snapshot = result.final_memory.snapshot()
    board.compare_memory(snapshot)


def test_observed_memory_may_be_a_model_like_object():
    result = oracle()
    board = scoreboard(result)

    class _Model:
        def __init__(self, image, device):
            self._image = image
            self._device = device

        def read(self, address, length):
            return self._image.read(self._device, address, length)

        def snapshot(self):
            return dict(self._image.snapshot()[self._device])

    board.compare_memory(
        {0: _Model(result.final_memory, 0), 1: _Model(result.final_memory, 1)}
    )


def test_failure_header_carries_run_dimensions_and_repro():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)
    del observed[-1]
    with pytest.raises(ScoreboardError) as error:
        board.compare_transactions(observed)
    message = str(error.value)
    assert message.startswith("SCOREBOARD FAIL axis=transactions")
    assert "level=L1 sim=icarus seed=1 depth=1 timing=ideal" in message
    assert "epoch=0 expected_transactions=6 observed_transactions=5" in message
    assert "REPRO: unit" in message


# -- reset-interrupted epochs ---------------------------------------------


def test_reset_prefix_accepts_a_short_completed_log():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)[:3]
    board.compare_reset_prefix(observed, reset_index=3)


def test_reset_prefix_rejects_a_wrong_record_before_the_edge():
    result = oracle()
    board = scoreboard(result)
    observed = observed_from(result)[:3]
    observed[2] = transaction(2, DATA_WRITE, 0, DST_ADDR + 4, observed[2].data)
    with pytest.raises(ScoreboardError) as error:
        board.compare_reset_prefix(observed, reset_index=3)
    assert "reset prefix" in str(error.value)


def test_reset_prefix_does_not_demand_the_full_chain():
    result = oracle()
    board = scoreboard(result)
    board.compare_reset_prefix(observed_from(result)[:1])


def test_reset_prefix_memory_derives_from_the_completed_prefix():
    result = oracle()
    board = scoreboard(result)
    committed = result.initial_memory.clone()
    committed.write(0, DST_ADDR, PAYLOAD[:1])
    board.compare_reset_memory(committed, 3)
    with pytest.raises(ScoreboardError):
        board.compare_reset_memory(result.final_memory, 3)


def test_reset_prefix_memory_skips_aborted_addresses():
    result = oracle()
    board = scoreboard(result)
    committed = result.initial_memory.clone()
    committed.write(0, DST_ADDR, PAYLOAD[:1])
    committed.poke(0, DST_ADDR + 1, 0xFF)  # partially committed aborted write
    board.compare_reset_memory(committed, 3, aborted_addresses=[(0, DST_ADDR + 1)])


def test_reset_index_beyond_the_completed_log_is_a_reference_error():
    result = oracle()
    board = scoreboard(result)
    with pytest.raises(Exception):
        board.compare_reset_prefix(observed_from(result)[:2], reset_index=5)


# -- repeated-run epochs --------------------------------------------------


def test_two_epoch_logs_must_be_identical():
    result = oracle()
    first = observed_from(result)
    second = observed_from(result)
    compare_epoch_logs(first, second, context=CONTEXT)
    Scoreboard.compare_epochs(first, second, context=CONTEXT)


def test_two_epoch_difference_fails_even_when_both_match_the_oracle():
    result = oracle()
    board = scoreboard(result)
    first = observed_from(result)
    second = observed_from(result)
    # Both epochs still match the oracle field-for-field ...
    board.compare_transactions(first)
    board.compare_transactions(second)
    # ... so the cross-epoch check needs its own difference to detect.
    second.append(transaction(len(second), DATA_READ, 1, SRC_ADDR, b"\x00"))
    with pytest.raises(ScoreboardError) as error:
        compare_epoch_logs(first, second, context=CONTEXT)
    assert "two-epoch equality" in str(error.value)


def test_two_epoch_field_difference_is_reported():
    result = oracle()
    first = observed_from(result)
    second = observed_from(result)
    second[2] = transaction(2, DATA_WRITE, 1, DST_ADDR, second[2].data)
    with pytest.raises(ScoreboardError) as error:
        compare_epoch_logs(first, second, context=CONTEXT)
    assert "epoch1=0 epoch2=1" in str(error.value)


def test_cross_device_epoch_passes_both_axes():
    result = oracle(dest_device=1)
    board = scoreboard(result)
    board.compare(observed_from(result), result.final_memory)


def _renumber(records) -> list:
    return [record.with_index(index) for index, record in enumerate(records)]
