"""Focused timed-wrapper transport checks for schedule order and model OE delay.

``wrap_device`` always returns a live ``_TimedPsramDevice`` (including under
``TIMING_PROFILE=ideal``, which keeps datasheet AC live and zeros only TB
placeholders). The sequence-order and OE-delay cases below still require
``TIMING_PROFILE=nominal`` so they exercise a non-identity transport path and
the OE override against the documented APS6404L AC base.

Test-case IDs:
    TC-TIMED-IDEAL-AC
    TC-TIMED-SEQUENCE-ORDER
    TC-TIMED-OE-DELAY
    TC-TIMED-UNEQUAL-DOUT
"""

import cocotb
from cocotb.triggers import Timer

from common.bringup import bring_up_engine
from common.config import parse_run_config
from common.constants import FILL
from common.engine_bfm import engine_qpi_read, engine_qpi_write
from models.psram_timing import resolve_timing_params

_OE_DELAY_NS = 12.0
# Unequal DUT-to-device path delays: CE# slower than SCK, then SCK slower.
_UNEQUAL_CE_NS = 8.0
_UNEQUAL_SCK_NS = 2.0
_UNEQUAL_SCK_SLOW_NS = 8.0
_UNEQUAL_CE_FAST_NS = 2.0
_UNEQUAL_ADDR = 0x000410
_UNEQUAL_PAYLOAD = bytes((0xA5, 0x5A, 0x3C))


def _repro(config: dict, test_filter: str, *, timing: str = "nominal") -> str:
    return (
        "REPRO: source test/env.sh && test/scripts/run_test.sh "
        "LEVEL=engine SIM={sim} SEED={seed} TIMING_PROFILE={timing} "
        "COCOTB_TEST_MODULES=tests.test_qspi_timing_model "
        "TEST_FILTER={test_filter}"
    ).format(
        sim=config["sim"],
        seed=config["seed"],
        timing=timing,
        test_filter=test_filter,
    )


def _assert_ideal_resolve_keeps_device_ac() -> None:
    """``ideal`` keeps datasheet AC; only TB_* path placeholders are zero."""

    params = resolve_timing_params("ideal")
    assert params["PSRAM_TSP_NS"] == 2.0, (
        f"ideal must keep PSRAM_TSP_NS=2.0 (tSP SIO setup vs rising SCK), "
        f"got {params['PSRAM_TSP_NS']!r}"
    )
    assert params["PSRAM_THD_NS"] == 2.0
    assert params["PSRAM_TACLK_NS"] == 5.5, (
        f"ideal must keep PSRAM_TACLK_NS=5.5 (tACLK read data valid after "
        f"falling SCK), got {params['PSRAM_TACLK_NS']!r}"
    )
    assert params["TB_TCO_NS"] == 0.0, (
        f"ideal must zero TB_TCO_NS (TB clock-to-out placeholder), "
        f"got {params['TB_TCO_NS']!r}"
    )
    assert params["TB_FLIGHT_OUT_NS"] == 0.0
    assert params["TB_FLIGHT_IN_NS"] == 0.0


def _assert_timed_wrapper(device) -> None:
    assert hasattr(device, "timing_params"), (
        "wrap_device must return a timed wrapper with timing_params"
    )
    assert hasattr(device, "timing_events"), (
        "wrap_device must return a timed wrapper with timing_events"
    )
    assert hasattr(device, "_schedule"), (
        "expected a timed wrapper with _schedule"
    )


async def _bring_up_timed(dut, *, require_profile: str = "nominal"):
    bringup = await bring_up_engine(
        dut,
        fill=FILL,
        bus_monitor=False,
        ce_monitor=False,
        handshake_monitor=False,
        pin_monitor=False,
        arbitration_monitor=False,
        controller_monitor=False,
    )
    assert bringup.timing_profile == require_profile, (
        f"test_qspi_timing_model requires TIMING_PROFILE={require_profile}, got "
        f"{bringup.timing_profile!r}"
    )
    device = bringup.devices[0]
    _assert_timed_wrapper(device)
    return bringup, device


@cocotb.test()
async def test_ideal_ac_and_wrap(dut):
    """ideal keeps device AC and always wraps (not an identity passthrough)."""

    config = parse_run_config()
    repro = _repro(config, "test_ideal_ac_and_wrap", timing="ideal")
    dut._log.info(repro)

    _assert_ideal_resolve_keeps_device_ac()

    if config["timing_profile"] != "ideal":
        # Sequence/OE cells run this module at nominal; resolve check above
        # still covers the ideal AC contract without requiring a second sim.
        dut._log.info(
            "TC-TIMED-IDEAL-AC: resolve check pass; wrap body skipped "
            "(need TIMING_PROFILE=ideal, observed %s)",
            config["timing_profile"],
        )
        return

    _bringup, device = await _bring_up_timed(dut, require_profile="ideal")
    assert device.timing_params["PSRAM_TSP_NS"] == 2.0
    assert device.timing_params["TB_TCO_NS"] == 0.0
    dut._log.info(
        "TC-TIMED-IDEAL-AC pass: ideal AC live, TB zero, wrapped device"
    )


@cocotb.test()
async def test_timed_sequence_order(dut):
    """Same-delay scheduled callbacks keep source schedule order."""

    config = parse_run_config()
    repro = _repro(config, "test_timed_sequence_order")
    dut._log.info(repro)
    _assert_ideal_resolve_keeps_device_ac()
    _bringup, device = await _bring_up_timed(dut)

    order = []
    delay_ns = 8.0
    device._schedule(delay_ns, order.append, "first")
    device._schedule(delay_ns, order.append, "second")
    device._schedule(delay_ns, order.append, "third")

    await Timer(delay_ns / 2.0, unit="ns")
    assert order == [], (
        f"TC-TIMED-SEQUENCE-ORDER: callbacks ran before delay; got {order!r}"
    )
    await Timer(delay_ns, unit="ns")
    assert order == ["first", "second", "third"], (
        "TC-TIMED-SEQUENCE-ORDER: same-delay applies must keep schedule "
        f"sequence, got {order!r}. {repro}"
    )
    dut._log.info("TC-TIMED-SEQUENCE-ORDER pass: %s", order)


@cocotb.test()
async def test_timed_oe_delay(dut):
    """Non-zero ``D_OUT_OE_NS`` delays model SIO OE relative to an immediate drive."""

    config = parse_run_config()
    repro = _repro(config, "test_timed_oe_delay")
    dut._log.info(repro)
    _assert_ideal_resolve_keeps_device_ac()
    _bringup, device = await _bring_up_timed(dut)

    # Point override: keep nominal device AC, set OE path delay only.
    device.timing_params = resolve_timing_params(
        "nominal", TB_TCO_OE_NS=_OE_DELAY_NS
    )
    d_out_oe_ns = device.timing_params["D_OUT_OE_NS"]
    assert d_out_oe_ns == _OE_DELAY_NS, (
        f"expected D_OUT_OE_NS={_OE_DELAY_NS}, got {d_out_oe_ns}"
    )

    device._release_sio()
    assert int(device.agent._oe_handle.value) == 0

    device._delayed_drive(0xA)
    assert int(device.agent._oe_handle.value) == 0, (
        "TC-TIMED-OE-DELAY: model OE must not assert before D_OUT_OE_NS"
    )
    await Timer(_OE_DELAY_NS - 1.0, unit="ns")
    assert int(device.agent._oe_handle.value) == 0, (
        "TC-TIMED-OE-DELAY: model OE asserted early "
        f"(D_OUT_OE_NS={_OE_DELAY_NS}). {repro}"
    )
    await Timer(2.0, unit="ns")
    assert int(device.agent._oe_handle.value) == 0xF, (
        "TC-TIMED-OE-DELAY: model OE did not assert after D_OUT_OE_NS. "
        f"{repro}"
    )
    dut._log.info(
        "TC-TIMED-OE-DELAY pass: D_OUT_OE_NS=%.1f delayed model OE", d_out_oe_ns
    )


@cocotb.test()
async def test_timed_unequal_dout(dut):
    """Unequal ``D_OUT_CE_NS`` / ``D_OUT_SCK_NS`` still sample command and payload.

    ``D_OUT_CE_NS`` / ``D_OUT_SCK_NS`` are DUT-to-device CE# and SCK transport
    delays (``TB_TCO_*`` + ``TB_FLIGHT_OUT_*``). With CE# slower than SCK, the
    first command nibble must still land after delayed ``_begin_transaction``.
    With SCK slower than CE#, the last write nibble must still commit before
    delayed CE# rise (legal source ``tCSP`` / ``tCHD``: CE# setup before first
    rising SCK / hold after last SCK).
    """

    config = parse_run_config()
    repro = _repro(config, "test_timed_unequal_dout")
    dut._log.info(repro)
    bringup, device = await _bring_up_timed(dut)

    # Point A: D_OUT_CE > D_OUT_SCK (first-nibble skew).
    device.timing_params = resolve_timing_params(
        "nominal",
        TB_TCO_CE_NS=_UNEQUAL_CE_NS,
        TB_TCO_SCK_NS=_UNEQUAL_SCK_NS,
    )
    assert device.timing_params["D_OUT_CE_NS"] == _UNEQUAL_CE_NS
    assert device.timing_params["D_OUT_SCK_NS"] == _UNEQUAL_SCK_NS

    device.write(_UNEQUAL_ADDR, bytes([0x00] * len(_UNEQUAL_PAYLOAD)))
    await engine_qpi_write(
        dut, device=0, address=_UNEQUAL_ADDR, payload=_UNEQUAL_PAYLOAD
    )
    assert device.read(_UNEQUAL_ADDR, len(_UNEQUAL_PAYLOAD)) == _UNEQUAL_PAYLOAD, (
        "TC-TIMED-UNEQUAL-DOUT: CE>SCK skew lost write payload "
        f"(got {device.read(_UNEQUAL_ADDR, len(_UNEQUAL_PAYLOAD)).hex()}). {repro}"
    )
    txn = device.agent.transactions[-1]
    assert txn.cmd_nibbles >= 2, (
        "TC-TIMED-UNEQUAL-DOUT: first command nibbles missing under CE>SCK. "
        f"{repro}"
    )
    assert bytes(txn.write_bytes) == _UNEQUAL_PAYLOAD, (
        "TC-TIMED-UNEQUAL-DOUT: write_bytes truncated under CE>SCK "
        f"({bytes(txn.write_bytes).hex()}). {repro}"
    )

    # Point B: D_OUT_SCK > D_OUT_CE (last-nibble / inverse skew).
    device.timing_params = resolve_timing_params(
        "nominal",
        TB_TCO_CE_NS=_UNEQUAL_CE_FAST_NS,
        TB_TCO_SCK_NS=_UNEQUAL_SCK_SLOW_NS,
    )
    assert device.timing_params["D_OUT_CE_NS"] == _UNEQUAL_CE_FAST_NS
    assert device.timing_params["D_OUT_SCK_NS"] == _UNEQUAL_SCK_SLOW_NS

    addr_b = _UNEQUAL_ADDR + 0x20
    payload_b = bytes((0x12, 0x34, 0x56))
    device.write(addr_b, bytes([0x00] * len(payload_b)))
    await engine_qpi_write(dut, device=0, address=addr_b, payload=payload_b)
    assert device.read(addr_b, len(payload_b)) == payload_b, (
        "TC-TIMED-UNEQUAL-DOUT: SCK>CE skew lost last write nibble "
        f"(got {device.read(addr_b, len(payload_b)).hex()}). {repro}"
    )
    txn_b = device.agent.transactions[-1]
    assert bytes(txn_b.write_bytes) == payload_b, (
        "TC-TIMED-UNEQUAL-DOUT: write_bytes truncated under SCK>CE "
        f"({bytes(txn_b.write_bytes).hex()}). {repro}"
    )

    # Read-back under CE>SCK confirms command + address nibbles still sample.
    device.timing_params = resolve_timing_params(
        "nominal",
        TB_TCO_CE_NS=_UNEQUAL_CE_NS,
        TB_TCO_SCK_NS=_UNEQUAL_SCK_NS,
    )
    result = await engine_qpi_read(
        dut, device=0, address=_UNEQUAL_ADDR, length=len(_UNEQUAL_PAYLOAD)
    )
    assert result.data == _UNEQUAL_PAYLOAD, (
        "TC-TIMED-UNEQUAL-DOUT: read payload mismatch under CE>SCK "
        f"(got {result.data.hex()}). {repro}"
    )
    dut._log.info(
        "TC-TIMED-UNEQUAL-DOUT pass: CE>SCK and SCK>CE write/read intact"
    )
