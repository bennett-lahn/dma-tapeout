"""Coarse CE# pulse and gap monitors (``Q-CEM``, ``Q-CPH``).

M1 owns only the ideal-profile, timestamped CE# width and inter-burst gap
checks from ``docs/llm/verification/04-timing-in-sim.md``. Transport delay,
``Q-LAUNCH``, ``Q-RXEDGE``, and setup/hold sweeps remain M3.

Defaults match APS6404L Table 10 as recorded in that doc:

* ``tCEM`` = 4.0 us (extended grade; tighter than the 8.0 us standard grade)
* ``tCPH`` = 18.0 ns (minimum CE# high between bursts)

``Q-CPH`` is bus-wide: the gap from any RAM CE# rising edge to the next RAM
CE# falling edge, including cross-device handoffs. ``Q-CEM`` is per-CE#: each
continuous selected-low interval is measured against ``tCEM``.

These checks live here, not in :mod:`monitors.qspi`, so ownership suites that
share the frozen ``start_shared_bus_monitor`` API are not coupled to CE# AC
thresholds (MCU pass-through frames currently leave only ~15 ns of CE# high).
"""

from dataclasses import dataclass

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import First, ReadOnly, Timer

Q_CEM = "Q-CEM"
Q_CPH = "Q-CPH"

CE_TIMING_CHECK_IDS = (Q_CEM, Q_CPH)

# APS6404L Rev 2.3 Table 10 via docs/llm/verification/04-timing-in-sim.md
PSRAM_TCEM_NS_EXT = 4_000.0
PSRAM_TCEM_NS_STD = 8_000.0
PSRAM_TCPH_NS = 18.0

RESULT_PASS = "pass"
RESULT_FAIL = "fail"

_KNOWN_LEVEL = {"0": 0, "1": 1}


def _level(handle) -> "int | None":
    text = str(handle.value).strip().lower()
    return _KNOWN_LEVEL.get(text[-1] if text else "")


def _now_ns() -> float:
    return float(get_sim_time(unit="ns"))


def _now_fs() -> int:
    """Integer simulator time in femtoseconds for margin compares."""
    return int(get_sim_time(unit="fs"))


def _ns_to_fs(value_ns: float) -> int:
    # 1 ns = 1e6 fs
    return int(round(float(value_ns) * 1_000_000.0))


@dataclass(frozen=True)
class TimingViolation:
    """One timestamped CE# timing finding."""

    check_id: str
    time_ns: float
    detail: str
    reset_truncated: bool = False
    low_ns: "float | None" = None
    gap_ns: "float | None" = None
    limit_ns: "float | None" = None
    ce_label: str = ""

    def __str__(self) -> str:
        prefix = "RESET-TRUNCATED " if self.reset_truncated else ""
        return f"{prefix}{self.check_id} at {self.time_ns:.3f}ns: {self.detail}"


class CeTimingMonitor:
    """Always-on coarse ``Q-CEM`` / ``Q-CPH`` checker for resolved RAM CE# nets.

    Wakes on CE# level changes, samples in the read-only phase, and records each
    violation once per pulse or gap. A deadline task also fails ``Q-CEM`` if CE#
    stays low past ``tCEM`` without a rising edge (so a hang does not wait forever
    for termination).
    """

    def __init__(
        self,
        *,
        ram_ce_n,
        rst_n=None,
        tcem_ns: float = PSRAM_TCEM_NS_EXT,
        tcph_ns: float = PSRAM_TCPH_NS,
        name: str = "ce-timing",
        level: str = "L1",
        strict: bool = False,
        max_events: int = 64,
        log=None,
    ) -> None:
        self._ram_ce_n = list(ram_ce_n)
        self._rst_n = rst_n
        self._tcem_ns = float(tcem_ns)
        self._tcph_ns = float(tcph_ns)
        self._tcem_fs = _ns_to_fs(self._tcem_ns)
        self._tcph_fs = _ns_to_fs(self._tcph_ns)
        self._strict = strict
        self._max_events = max_events
        self._log = log

        self.name = name
        self.level = level
        self.violations: "list[str]" = []
        self.events: "list[TimingViolation]" = []
        self.reset_truncated: "list[TimingViolation]" = []

        self._prev_levels: "dict[str, int | None]" = {
            label: None for label, _ in self._ram_ce_n
        }
        self._fall_fs: "dict[str, int | None]" = {
            label: None for label, _ in self._ram_ce_n
        }
        self._fall_gen: "dict[str, int]" = {label: 0 for label, _ in self._ram_ce_n}
        self._cem_reported: "dict[str, bool]" = {
            label: False for label, _ in self._ram_ce_n
        }
        self._last_rise_fs: "int | None" = None
        self._last_rise_label: "str | None" = None
        self._min_cem_margin_ns: "float | None" = None
        self._min_cph_margin_ns: "float | None" = None
        self._samples = 0
        self._suppressed = 0
        self._active = True

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Launch the background checker. Call before reset release."""
        self._active = True
        return cocotb.start_soon(self._run())

    def stop(self) -> None:
        """Soft-stop so a later test in the same module can re-attach."""
        self._active = False

    def clear(self) -> None:
        """Drop recorded findings and edge history for a fresh directed window."""
        self.events.clear()
        self.violations.clear()
        self.reset_truncated.clear()
        self._suppressed = 0
        self._last_rise_fs = None
        self._last_rise_label = None
        self._min_cem_margin_ns = None
        self._min_cph_margin_ns = None
        for label, handle in self._ram_ce_n:
            self._fall_fs[label] = None
            self._fall_gen[label] += 1
            self._cem_reported[label] = False
            self._prev_levels[label] = _level(handle)

    async def _run(self) -> None:
        watched = [handle for _, handle in self._ram_ce_n]
        if self._rst_n is not None:
            watched.append(self._rst_n)
        while True:
            await First(*[handle.value_change for handle in watched])
            await ReadOnly()
            if self._active:
                self._evaluate()

    # -- reporting ---------------------------------------------------------

    def _in_reset(self) -> bool:
        if self._rst_n is None:
            return False
        return _level(self._rst_n) != 1

    def _record(self, event: TimingViolation) -> None:
        if event.reset_truncated:
            self.reset_truncated.append(event)
            return

        if len(self.events) >= self._max_events:
            self._suppressed += 1
            return

        self.events.append(event)
        self.violations.append(f"{self.name} {event}")
        if self._log is not None:
            self._log.error(
                "CHECKER FAIL id=%s level=%s %s", event.check_id, self.level, event
            )
        if self._strict:
            raise AssertionError(str(event))

    def _fail_cem(
        self,
        label: str,
        *,
        low_ns: float,
        in_reset: bool,
        still_low: bool,
    ) -> None:
        if self._cem_reported[label]:
            return
        self._cem_reported[label] = True
        margin_ns = self._tcem_ns - low_ns
        if self._min_cem_margin_ns is None or margin_ns < self._min_cem_margin_ns:
            self._min_cem_margin_ns = margin_ns
        how = "still low" if still_low else "pulse ended"
        detail = (
            f"{label} CE# low {low_ns:.3f}ns exceeds tCEM={self._tcem_ns:.3f}ns "
            f"({how}; margin={margin_ns:.3f}ns)"
        )
        self._record(
            TimingViolation(
                check_id=Q_CEM,
                time_ns=_now_ns(),
                detail=detail,
                reset_truncated=in_reset,
                low_ns=low_ns,
                limit_ns=self._tcem_ns,
                ce_label=label,
            )
        )

    def _fail_cph(
        self,
        label: str,
        *,
        gap_ns: float,
        in_reset: bool,
    ) -> None:
        margin_ns = gap_ns - self._tcph_ns
        if self._min_cph_margin_ns is None or margin_ns < self._min_cph_margin_ns:
            self._min_cph_margin_ns = margin_ns
        prior = self._last_rise_label or "?"
        detail = (
            f"CE# high gap {gap_ns:.3f}ns < tCPH={self._tcph_ns:.3f}ns "
            f"({prior} rise → {label} fall; margin={margin_ns:.3f}ns)"
        )
        self._record(
            TimingViolation(
                check_id=Q_CPH,
                time_ns=_now_ns(),
                detail=detail,
                reset_truncated=in_reset,
                gap_ns=gap_ns,
                limit_ns=self._tcph_ns,
                ce_label=label,
            )
        )

    # -- checks ------------------------------------------------------------

    def _evaluate(self) -> None:
        self._samples += 1
        in_reset = self._in_reset()
        now_fs = _now_fs()

        for label, handle in self._ram_ce_n:
            level = _level(handle)
            prev = self._prev_levels[label]
            self._prev_levels[label] = level
            if level is None or prev is None or level == prev:
                continue

            if prev == 1 and level == 0:
                self._on_ce_fall(label, now_fs=now_fs, in_reset=in_reset)
            elif prev == 0 and level == 1:
                self._on_ce_rise(label, now_fs=now_fs, in_reset=in_reset)

    def _on_ce_fall(self, label: str, *, now_fs: int, in_reset: bool) -> None:
        if self._last_rise_fs is not None:
            gap_fs = now_fs - self._last_rise_fs
            gap_ns = gap_fs / 1_000_000.0
            margin_ns = gap_ns - self._tcph_ns
            if self._min_cph_margin_ns is None or margin_ns < self._min_cph_margin_ns:
                self._min_cph_margin_ns = margin_ns
            if gap_fs < self._tcph_fs:
                self._fail_cph(label, gap_ns=gap_ns, in_reset=in_reset)

        self._fall_fs[label] = now_fs
        self._fall_gen[label] += 1
        self._cem_reported[label] = False
        cocotb.start_soon(
            self._cem_deadline(label, fall_fs=now_fs, generation=self._fall_gen[label])
        )

    def _on_ce_rise(self, label: str, *, now_fs: int, in_reset: bool) -> None:
        fall_fs = self._fall_fs[label]
        if fall_fs is not None and not self._cem_reported[label]:
            low_fs = now_fs - fall_fs
            low_ns = low_fs / 1_000_000.0
            margin_ns = self._tcem_ns - low_ns
            if self._min_cem_margin_ns is None or margin_ns < self._min_cem_margin_ns:
                self._min_cem_margin_ns = margin_ns
            if low_fs > self._tcem_fs:
                self._fail_cem(
                    label, low_ns=low_ns, in_reset=in_reset, still_low=False
                )

        self._fall_fs[label] = None
        self._fall_gen[label] += 1
        self._cem_reported[label] = False
        self._last_rise_fs = now_fs
        self._last_rise_label = label

    async def _cem_deadline(self, label: str, *, fall_fs: int, generation: int) -> None:
        # Wait just past tCEM so a pulse that ends exactly on the limit still
        # passes ("longer than tCEM" / remains at or below the datasheet max).
        await Timer(self._tcem_ns, unit="ns")
        await Timer(0.001, unit="ns")
        await ReadOnly()
        if not self._active:
            return
        if self._fall_gen[label] != generation:
            return
        if self._fall_fs[label] != fall_fs:
            return
        handle = next(h for name, h in self._ram_ce_n if name == label)
        if _level(handle) != 0:
            return
        low_fs = _now_fs() - fall_fs
        if low_fs <= self._tcem_fs:
            return
        low_ns = low_fs / 1_000_000.0
        self._fail_cem(
            label, low_ns=low_ns, in_reset=self._in_reset(), still_low=True
        )

    # -- results -----------------------------------------------------------

    def counts(self) -> "dict[str, int]":
        counts = {check_id: 0 for check_id in CE_TIMING_CHECK_IDS}
        for event in self.events:
            counts[event.check_id] += 1
        return counts

    def results(self) -> "dict[str, str]":
        counts = self.counts()
        return {
            check_id: RESULT_FAIL if counts[check_id] else RESULT_PASS
            for check_id in CE_TIMING_CHECK_IDS
        }

    def violations_for(self, check_id: str) -> "list[TimingViolation]":
        return [event for event in self.events if event.check_id == check_id]

    def summary(self) -> str:
        parts = [f"{check_id}={result}" for check_id, result in self.results().items()]
        if self._min_cem_margin_ns is not None:
            parts.append(f"min_cem_margin_ns={self._min_cem_margin_ns:.3f}")
        if self._min_cph_margin_ns is not None:
            parts.append(f"min_cph_margin_ns={self._min_cph_margin_ns:.3f}")
        if self.reset_truncated:
            parts.append(f"reset_truncated={len(self.reset_truncated)}")
        if self._suppressed:
            parts.append(f"suppressed={self._suppressed}")
        return (
            f"{self.name} ({self.level}, tCEM={self._tcem_ns:g}ns, "
            f"tCPH={self._tcph_ns:g}ns, {self._samples} samples): "
            + " ".join(parts)
        )


# -- attachment ------------------------------------------------------------


def _optional(dut, name):
    return getattr(dut, name, None)


def start_ce_timing_monitor(
    dut,
    *,
    strict: bool = False,
    tcem_ns: "float | None" = None,
    tcph_ns: float = PSRAM_TCPH_NS,
    grade: str = "extended",
    **kwargs,
) -> CeTimingMonitor:
    """Create and start the coarse CE# pulse/gap monitor for *dut*.

    Works against ``tb_top`` / ``tb_gl`` / ``tb_engine`` via the wrapper aliases
    ``bus_ram_a_cs_n`` / ``bus_ram_b_cs_n``. Pass ``strict=True`` to raise at the
    first violation; otherwise collect into :attr:`CeTimingMonitor.events`.

    ``grade`` selects the datasheet ``tCEM`` when ``tcem_ns`` is omitted:
    ``extended`` → 4 us, ``standard`` → 8 us. An explicit ``tcem_ns`` wins.
    """
    if grade not in ("extended", "standard"):
        raise ValueError(f"grade must be 'extended' or 'standard', got {grade!r}")
    if tcem_ns is None:
        tcem_ns = PSRAM_TCEM_NS_STD if grade == "standard" else PSRAM_TCEM_NS_EXT

    monitor = CeTimingMonitor(
        ram_ce_n=(("PSRAM0", dut.bus_ram_a_cs_n), ("PSRAM1", dut.bus_ram_b_cs_n)),
        rst_n=_optional(dut, "rst_n"),
        tcem_ns=tcem_ns,
        tcph_ns=tcph_ns,
        level=kwargs.pop("level", "L0" if _optional(dut, "bus_flash_cs_n") is None else "L1"),
        strict=strict,
        log=kwargs.pop("log", dut._log),
        **kwargs,
    )
    monitor.start()
    return monitor
