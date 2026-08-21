"""Shared L1 directed-suite plumbing for chain install, DONE wait, and dual-axis compare.

Used by ``tests.test_dma_directed`` and ``tests.test_reset_and_bus``. Both suites
are DUT-master: backdoor-install descriptors/payload into bring-up PSRAM models,
hand the ASIC one accepted START (or more specialized START/BUS/reset stimulus),
then compare the pin-decoded ordered transaction log and backdoor-read final
memory against :mod:`reference.chain`'s golden interpretation before
:func:`common.dispose.dispose_run`.
"""

import os
from dataclasses import replace
from cocotb.triggers import RisingEdge, SimTimeoutError, with_timeout

from common.coverage_l1 import L1CoverageAdapter
from common.constants import DONE_MASK, DONE_TIMEOUT_NS
from common.dispose import dispose_run
from common.host import pulse_start
from reference.coverage import FRAGMENT_FILENAME, CoverageSampler
from reference.scoreboard import RunContext, Scoreboard
from reference.tcd import TCD_BYTES

# One sampler per RUN_DIR so successive directed windows merge into coverage.json.
_coverage_samplers: "dict[str, CoverageSampler]" = {}
def align_chain_depth(chain, config: dict):
    """Return *chain* with dma_buf_depth matching this compile."""
    return replace(chain, dma_buf_depth=int(config["dma_buf_depth"]))



def contiguous_runs(values: dict):
    """Yield ``(start_address, bytes)`` for maximal contiguous runs in *values*."""
    runs = []
    start = None
    run = bytearray()
    previous = None
    for address in sorted(values):
        if previous is not None and address == previous + 1:
            run.append(values[address])
        else:
            if start is not None:
                runs.append((start, bytes(run)))
            start = address
            run = bytearray([values[address]])
        previous = address
    if start is not None:
        runs.append((start, bytes(run)))
    return runs


def install_chain(bringup, chain) -> None:
    """Backdoor-install a generated chain's descriptor and payload bytes.

    Only the addresses the golden model itself defines are written, coalesced
    into contiguous runs; the DMA never observes this on the bus.
    """
    for device_id, values in chain.memory.snapshot().items():
        if not values:
            continue
        psram = bringup.device(device_id)
        for address, payload in contiguous_runs(values):
            psram.write(address, payload)


def read_back(bringup, chain) -> "dict[int, dict[int, int]]":
    """Read back only the addresses the golden chain defines, per device.

    Restricting the observed-memory axis to exactly the golden model's defined
    addresses (rather than the whole PSRAM model) keeps a multi-window TC's
    later windows from tripping over an earlier window's unrelated bytes.
    """
    observed: "dict[int, dict[int, int]]" = {}
    for device_id, values in chain.memory.snapshot().items():
        if not values:
            continue
        psram = bringup.device(device_id)
        observed[device_id] = {address: psram.byte(address) for address in values}
    return observed


def auto_timeout_ns(chain) -> int:
    """Scale the DONE timeout to a chain's total payload.

    A fixed budget tuned for ``TC-SMOKE``'s single byte would spuriously time
    out on a multi-hundred-byte directed transfer.
    """
    total_bytes = sum(tcd.transfer_len for tcd in chain.executable)
    fetches = len(chain.tcds)
    return max(DONE_TIMEOUT_NS, 2_000 * (total_bytes + TCD_BYTES * fetches))


async def wait_for_done_pulse(dut) -> None:
    """DONE (uo_out[0]) is high in IDLE; wait for it to drop then return high."""
    while int(dut.uo_out.value) & DONE_MASK:
        await RisingEdge(dut.clk)
    while not (int(dut.uo_out.value) & DONE_MASK):
        await RisingEdge(dut.clk)


async def wait_until_done(dut) -> None:
    """Wait until DONE is high (no-op if already idle after an overlapped window)."""
    while (int(dut.uo_out.value) & DONE_MASK) != 1:
        await RisingEdge(dut.clk)


def run_context(config: dict, test: str, repro: str) -> RunContext:
    """Build a :class:`reference.scoreboard.RunContext` from parsed run config."""
    return RunContext(
        level=config["level"],
        sim=config["sim"],
        seed=config["seed"],
        depth=config["dma_buf_depth"],
        timing=config["timing_profile"],
        test=test,
        repro=repro,
    )


def _coverage_sampler(config: dict, *, test: str) -> CoverageSampler:
    """Return a :class:`CoverageSampler` for this compile's ``RUN_DIR``.

    A new process loads any retained ``coverage.json`` so later suites
    (reset/bus, length-address corners) append instead of overwriting.
    """
    cov_config = dict(config)
    cov_config["test"] = test
    fresh = CoverageSampler.from_config(cov_config)
    key = str(fresh.run_dir or "")
    existing = _coverage_samplers.get(key)
    if existing is None:
        fragment = os.path.join(key, FRAGMENT_FILENAME) if key else ""
        if fragment and os.path.isfile(fragment):
            fresh.absorb_fragment(fragment)
        _coverage_samplers[key] = fresh
        return fresh
    existing.context = replace(existing.context, test=test)
    return existing


def coverage_sampler(config: dict, *, test: str) -> CoverageSampler:
    """Public wrapper for the per-``RUN_DIR`` :class:`CoverageSampler`."""
    return _coverage_sampler(config, test=test)


def l1_adapter(config: dict, *, test: str) -> L1CoverageAdapter:
    """Return the L1 adapter bound to this compile's shared sampler."""
    return L1CoverageAdapter(_coverage_sampler(config, test=test))


def commit_l1_window(
    config: dict,
    *,
    test: str,
    checkers_ok: bool = True,
    scoreboard_ok: bool = True,
) -> None:
    """Commit pending L1 observations when there is no golden-chain compare."""
    cov = _coverage_sampler(config, test=test)
    cov.commit_window(checkers_ok=checkers_ok, scoreboard_ok=scoreboard_ok)
    cov.write_fragment()


def record_passing_coverage(config: dict, chain, golden, *, test: str) -> None:
    """Commit golden-chain ``COV-*`` hits and write ``RUN_DIR/coverage.json``.

    Call only after scoreboard compare and :func:`dispose_run` both succeed so
    ``checkers_ok`` / ``scoreboard_ok`` match the Wave 2 oracle contract. Hits
    ``COV-DEPTH`` (compile-time ``DMA_BUF_DEPTH`` bin) for this N; ``make depth``
    therefore fills depths 1..``DMA_BUF_DEPTH_MAX`` via the harness loop.
    """
    cov = _coverage_sampler(config, test=test)
    cov.record_chain(golden, generated=chain)
    cov.commit_window(checkers_ok=True, scoreboard_ok=True)
    cov.write_fragment()


async def compare_and_dispose(
    dut, bringup, chain, *, test: str, config: dict, repro: str
) -> None:
    """Full dual-axis compare against *chain*'s golden model, then dispose."""
    chain = align_chain_depth(chain, config)
    if bringup.pin is None or bringup.pin.blocked:
        reason = (
            "missing"
            if bringup.pin is None
            else f"blocked ({bringup.pin.blocked_reason})"
        )
        raise AssertionError(
            f"{test}: pin monitor {reason}; L1 directed cases require a live "
            "pin axis for dual-axis scoreboard compare. " + repro
        )
    golden = chain.interpret(dma_buf_depth=config["dma_buf_depth"])
    Scoreboard.from_result(
        golden,
        guards=chain.guards,
        regions=chain.regions,
        context=run_context(config, test, repro),
        log=dut._log,
    ).compare(
        bringup.pin.transactions(),
        observed_memory=read_back(bringup, chain),
    )
    dispose_run(bringup, test=test, log=dut._log, repro=repro)
    record_passing_coverage(config, chain, golden, test=test)


async def run_directed_window(
    dut, bringup, chain, *, test: str, config: dict, repro: str
):
    """Install *chain*, pulse START, and dual-axis-compare one DMA run.

    DUT-master: after backdoor descriptor/payload installation, one accepted
    START pulse is the only stimulus; the DMA fetches and moves data itself.
    Safe to call more than once on the same live *bringup* (no ``rst_n``
    toggling) so a TC can chain several windows, each with a clean scoreboard
    epoch and dispose window.

    Returns:
        ``(golden, report)``: the golden :class:`reference.chain.ChainResult`
        and the :class:`common.dispose.DisposeReport` for this window.
    """
    chain = align_chain_depth(chain, config)
    bringup.clear()
    install_chain(bringup, chain)

    golden = chain.interpret(dma_buf_depth=config["dma_buf_depth"])
    timeout_ns = auto_timeout_ns(chain)

    await pulse_start(dut)
    try:
        await with_timeout(wait_for_done_pulse(dut), timeout_ns, "ns")
    except SimTimeoutError:
        dut._log.error(repro)
        raise AssertionError(
            f"{test}: DONE did not return within {timeout_ns} ns; classify "
            "DUT vs TB before retry. " + repro
        )

    if bringup.pin is None or bringup.pin.blocked:
        reason = (
            "missing"
            if bringup.pin is None
            else f"blocked ({bringup.pin.blocked_reason})"
        )
        raise AssertionError(
            f"{test}: pin monitor {reason}; L1 directed cases require a live "
            "pin axis for dual-axis scoreboard compare. " + repro
        )

    Scoreboard.from_result(
        golden,
        guards=chain.guards,
        regions=chain.regions,
        context=run_context(config, test, repro),
        log=dut._log,
    ).compare(
        bringup.pin.transactions(),
        observed_memory=read_back(bringup, chain),
    )

    report = dispose_run(bringup, test=test, log=dut._log, repro=repro)
    record_passing_coverage(config, chain, golden, test=test)
    dut._log.info(
        "%s passed: %d transaction(s) (%s)",
        test,
        len(golden.transactions),
        report.summary(),
    )
    return golden, report
