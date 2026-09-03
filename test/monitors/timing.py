"""QSPI timing monitors (``Q-CEM`` through ``Q-RXEDGE``).

The monitor observes the DUT-visible CE#/SCK plane.  It reports setup, hold,
and read-termination margins using integer simulator time; the parser and
``Q-RXEDGE`` (each launched read nibble captured on the following rising SCK)
own device-plane read-data timing. L0 matches ``rdata_valid`` pulse plus nibble.
L1/L2 have no ``rdata_valid`` alias; the armed external rising SCK is the
D16 capture and still applies ``tACLK`` / ``PSRAM_TACLK_NS`` (read data valid
after falling SCK) independently of the wrapper.

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

from common.lifecycle import (
    PendingLedger,
    REASON_SCOPE,
    REASON_STOP,
    SEV_FAIL,
)
from common.constants import RESULT_BLOCKED, RESULT_FAIL, RESULT_NA, RESULT_PASS

Q_CEM = "Q-CEM"
Q_CPH = "Q-CPH"
Q_CSP = "Q-CSP"
Q_CHD = "Q-CHD"
Q_TERM = "Q-TERM"
Q_LAUNCH = "Q-LAUNCH"
Q_RXEDGE = "Q-RXEDGE"

CE_TIMING_CHECK_IDS = (Q_CEM, Q_CPH, Q_CSP, Q_CHD, Q_TERM, Q_LAUNCH, Q_RXEDGE)

# APS6404L Rev 2.3 Table 10 via docs/llm/verification/04-timing-in-sim.md
PSRAM_TCEM_NS_EXT = 4_000.0
PSRAM_TCEM_NS_STD = 8_000.0
PSRAM_TCPH_NS = 18.0
PSRAM_TCSP_NS = 2.5
PSRAM_TCHD_NS = 3.0
PSRAM_TACLK_NS = 5.5
PSRAM_TSP_NS = 2.0
PSRAM_THD_NS = 2.0

_KNOWN_LEVEL = {"0": 0, "1": 1}


def _level(handle) -> "int | None":
    text = str(handle.value).strip().lower()
    return _KNOWN_LEVEL.get(text[-1] if text else "")


def _nibble(handle) -> "int | None":
    if handle is None:
        return None
    try:
        return int(handle.value)
    except (TypeError, ValueError):
        return None


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
    observed_ns: "float | None" = None
    required_ns: "float | None" = None
    source_time_ns: "float | None" = None

    def __str__(self) -> str:
        prefix = "RESET-TRUNCATED " if self.reset_truncated else ""
        return f"{prefix}{self.check_id} at {self.time_ns:.3f}ns: {self.detail}"


class CeTimingMonitor:
    """Always-on CE# AC and read-termination checker for resolved RAM nets.

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
        tcsp_ns: float = PSRAM_TCSP_NS,
        tchd_ns: float = PSRAM_TCHD_NS,
        taclk_ns: float = PSRAM_TACLK_NS,
        tsp_ns: float = PSRAM_TSP_NS,
        thd_ns: float = PSRAM_THD_NS,
        sck=None,
        sck_oe=None,
        sio_out=None,
        sio_oe=None,
        rdata_valid=None,
        rdata=None,
        busy=None,
        byte_len=None,
        timed_devices=(),
        read_expected_nibbles=None,
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
        self._tcsp_ns = float(tcsp_ns)
        self._tchd_ns = float(tchd_ns)
        self._taclk_ns = float(taclk_ns)
        self._tsp_ns = float(tsp_ns)
        self._thd_ns = float(thd_ns)
        self._tcem_fs = _ns_to_fs(self._tcem_ns)
        self._tcph_fs = _ns_to_fs(self._tcph_ns)
        self._tcsp_fs = _ns_to_fs(self._tcsp_ns)
        self._tchd_fs = _ns_to_fs(self._tchd_ns)
        self._taclk_fs = _ns_to_fs(self._taclk_ns)
        self._tsp_fs = _ns_to_fs(self._tsp_ns)
        self._thd_fs = _ns_to_fs(self._thd_ns)
        self._sck = sck
        self._sck_oe = sck_oe
        self._sio_out = sio_out
        self._sio_oe = sio_oe
        self._rdata_valid = rdata_valid
        self._rdata = rdata
        self._busy = busy
        self._byte_len = byte_len
        self._timed_devices = tuple(
            device for device in timed_devices if hasattr(device, "timing_events")
        )
        self._timed_event_cursor = {id(device): 0 for device in self._timed_devices}
        self._read_expected_nibbles = read_expected_nibbles
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
        self._first_rise_fs: "dict[str, int | None]" = {
            label: None for label, _ in self._ram_ce_n
        }
        self._last_rise_fs: "dict[str, int | None]" = {
            label: None for label, _ in self._ram_ce_n
        }
        self._prior_rise_fs: "dict[str, int | None]" = {
            label: None for label, _ in self._ram_ce_n
        }
        self._read_commits: "dict[str, int]" = {
            label: 0 for label, _ in self._ram_ce_n
        }
        self._last_read_commit_fs: "dict[str, int | None]" = {
            label: None for label, _ in self._ram_ce_n
        }
        self._cem_reported: "dict[str, bool]" = {
            label: False for label, _ in self._ram_ce_n
        }
        self._last_ce_rise_fs: "int | None" = None
        self._last_rise_label: "str | None" = None
        self._min_cem_margin_ns: "float | None" = None
        self._min_cph_margin_ns: "float | None" = None
        self._min_csp_margin_ns: "float | None" = None
        self._min_chd_margin_ns: "float | None" = None
        self._min_term_margin_ns: "float | None" = None
        self.term_margins: "list[str]" = []
        self._prev_sck = _level(sck) if sck is not None else None
        self._prev_rdata_valid = (
            _level(rdata_valid) if rdata_valid is not None else None
        )
        self._prev_sio_out = str(sio_out.value) if sio_out is not None else None
        self._prev_sio_oe = str(sio_oe.value) if sio_oe is not None else None
        self._launch_change_fs = None
        self._launch_change_kind = ""
        self._launch_hold_until_fs = None
        self._launch_device = None
        self._launch_hits = 0
        self._rx_pending = []
        self._rx_min_setup_ns = None
        self._rx_min_hold_ns = None
        self._rx_captures = 0
        self._rx_launches = 0
        self._rx_expected_nibbles = {}
        self._samples = 0
        self._suppressed = 0
        self._overflow_ids = set()
        self._active = True
        self._cem_tasks: list = []
        self.pending = PendingLedger(
            owner=self.name,
            record=self._record_pending,
            in_reset=self._in_reset,
            now_ns=_now_ns,
        )

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Launch the background checker. Call before reset release."""
        self._active = True
        return cocotb.start_soon(self._run())

    def stop(self) -> None:
        """Soft-stop so a later test in the same module can re-attach."""
        self.pending.audit(reason=REASON_STOP)
        self.cancel_tasks()
        self._active = False

    def cancel_tasks(self) -> None:
        """Cancel CE# ``tCEM`` deadline tasks and invalidate their generation.

        ``tCEM`` is max CE# low time. Dispose/clear must not leave those
        timers running into the next window.
        """
        for label in list(self._fall_gen):
            self._fall_gen[label] += 1
        tasks, self._cem_tasks = self._cem_tasks, []
        for task in tasks:
            task.cancel()

    def clear(self) -> None:
        """Drop recorded findings and edge history for a fresh directed window."""
        self.events.clear()
        self.violations.clear()
        self.reset_truncated.clear()
        self._suppressed = 0
        self._overflow_ids.clear()
        self._last_ce_rise_fs = None
        self._last_rise_label = None
        self._min_cem_margin_ns = None
        self._min_cph_margin_ns = None
        self._min_csp_margin_ns = None
        self._min_chd_margin_ns = None
        self._min_term_margin_ns = None
        self.term_margins.clear()
        self._prev_sck = _level(self._sck) if self._sck is not None else None
        self._prev_rdata_valid = (
            _level(self._rdata_valid) if self._rdata_valid is not None else None
        )
        self._prev_sio_out = (
            str(self._sio_out.value) if self._sio_out is not None else None
        )
        self._prev_sio_oe = (
            str(self._sio_oe.value) if self._sio_oe is not None else None
        )
        self._launch_change_fs = None
        self._launch_change_kind = ""
        self._launch_hold_until_fs = None
        self._launch_device = None
        self._launch_hits = 0
        self._rx_pending.clear()
        self.pending.clear()
        self._rx_min_setup_ns = None
        self._rx_min_hold_ns = None
        self._rx_captures = 0
        self._rx_launches = 0
        self._rx_expected_nibbles.clear()
        self._timed_event_cursor = {id(device): 0 for device in self._timed_devices}
        for label, handle in self._ram_ce_n:
            self._fall_fs[label] = None
            self._first_rise_fs[label] = None
            self._last_rise_fs[label] = None
            self._prior_rise_fs[label] = None
            self._read_commits[label] = 0
            self._last_read_commit_fs[label] = None
            self._fall_gen[label] += 1
            self._cem_reported[label] = False
            self._prev_levels[label] = _level(handle)

    async def _run(self) -> None:
        watched = [handle for _, handle in self._ram_ce_n]
        if self._rst_n is not None:
            watched.append(self._rst_n)
        if self._sck is not None:
            watched.append(self._sck)
        if self._sio_out is not None:
            watched.append(self._sio_out)
        if self._sio_oe is not None:
            watched.append(self._sio_oe)
        if self._rdata_valid is not None:
            watched.append(self._rdata_valid)
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
            self._overflow_ids.add(event.check_id)
            return

        self.events.append(event)
        self.violations.append(f"{self.name} {event}")
        if self._log is not None:
            self._log.error(
                "CHECKER FAIL id=%s level=%s %s", event.check_id, self.level, event
            )
        if self._strict:
            raise AssertionError(str(event))

    def _record_pending(
        self, check_id: str, detail: str, *, reset_truncated: bool
    ) -> TimingViolation:
        event = TimingViolation(
            check_id=check_id,
            time_ns=_now_ns(),
            detail=detail,
            reset_truncated=reset_truncated,
        )
        self._record(event)
        return event

    def _fail_edge(self, check_id: str, detail: str, *, in_reset: bool) -> None:
        if in_reset:
            detail += (
                f"; reset-sample={_now_ns():.3f}ns "
                "(forced OE/SCK reset convergence)"
            )
        self._record(
            TimingViolation(
                check_id=check_id,
                time_ns=_now_ns(),
                detail=detail,
                reset_truncated=in_reset,
            )
        )

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

    def _record_margin(
        self,
        *,
        check_id: str,
        label: str,
        observed_fs: int,
        required_fs: int,
        source_fs: int,
        in_reset: bool,
        relation: str,
    ) -> None:
        observed_ns = observed_fs / 1_000_000.0
        required_ns = required_fs / 1_000_000.0
        margin_ns = (observed_fs - required_fs) / 1_000_000.0
        detail = (
            f"{label} {relation}: observed={observed_ns:.3f}ns "
            f"required={required_ns:.3f}ns margin={margin_ns:.3f}ns "
            f"(source={source_fs / 1_000_000.0:.3f}ns)"
        )
        if in_reset:
            detail += (
                f"; reset-sample={_now_ns():.3f}ns "
                "(forced CE#/SCK reset convergence)"
            )
        self._record(
            TimingViolation(
                check_id=check_id,
                time_ns=_now_ns(),
                detail=detail,
                reset_truncated=in_reset,
                limit_ns=required_ns,
                ce_label=label,
                observed_ns=observed_ns,
                required_ns=required_ns,
                source_time_ns=source_fs / 1_000_000.0,
            )
        )

    # -- checks ------------------------------------------------------------

    def _evaluate(self) -> None:
        self._samples += 1
        in_reset = self._in_reset()
        now_fs = _now_fs()
        ce_edges = []

        for label, handle in self._ram_ce_n:
            level = _level(handle)
            prev = self._prev_levels[label]
            self._prev_levels[label] = level
            if level is None or prev is None or level == prev:
                continue
            if prev == 1 and level == 0:
                ce_edges.append((label, "fall"))
            elif prev == 0 and level == 1:
                ce_edges.append((label, "rise"))

        # A same-timestamp CE# fall and SCK rise is a zero-margin CSP failure.
        for label, edge in ce_edges:
            if edge == "fall":
                self._on_ce_fall(label, now_fs=now_fs, in_reset=in_reset)
        self._on_launch_source(now_fs=now_fs, in_reset=in_reset)
        self._collect_timed_events(in_reset=in_reset)
        self._on_sck(now_fs=now_fs, in_reset=in_reset)
        self._on_rdata_valid(now_fs, in_reset=in_reset)
        for label, edge in ce_edges:
            if edge == "rise":
                self._on_ce_rise(label, now_fs=now_fs, in_reset=in_reset)

    def _device_by_id(self, device_id):
        for device in self._timed_devices:
            if getattr(device, "device_id", None) == device_id:
                return device
        return None

    def _selected_timed_device(self):
        """Return the timed wrapper whose DUT-plane CE# is currently low."""
        for label, handle in self._ram_ce_n:
            if _level(handle) != 0:
                continue
            device_id = 0 if label == "PSRAM0" else 1
            device = self._device_by_id(device_id)
            if device is not None:
                return device
        return None

    def _d_out_fs(self, signal: str, device=None) -> int:
        """Per-device D_OUT_* (TB output delay toward that device)."""
        if device is None:
            device = self._selected_timed_device()
        if device is None:
            return 0
        return _ns_to_fs(device.timing_params.get(f"D_OUT_{signal}_NS", 0.0))

    def _asic_drives_sck(self) -> bool:
        """True when Q-LAUNCH applies because the ASIC currently drives SCK.

        L0 ``tb_engine`` has no ``asic_sck_oe``: the engine always owns SCK, so
        a missing OE handle is treated as 1 only at L0. At L1/L2 a missing OE
        is 0 (not driving), never a silent 1.
        """
        if self._sck_oe is None:
            return str(self.level).upper().startswith("L0")
        return _level(self._sck_oe) == 1

    def _computed_rx_ready_fs(self, entry) -> "int | None":
        """Independent tACLK ready time: device-plane fall + tACLK + TB_FLIGHT_IN.

        ``PSRAM_TACLK_NS`` / ``tACLK`` is read data valid after falling SCK.
        ``D_IN_SIO_NS`` carries ``TB_FLIGHT_IN`` (return-path delay to the DUT).
        """
        fall_fs = entry.get("device_fall_fs")
        if fall_fs is None:
            fall_fs = entry.get("source_fall_fs")
        if fall_fs is None:
            return None
        device = self._device_by_id(entry.get("device_id"))
        din_fs = 0
        if device is not None:
            params = device.timing_params
            din_fs = _ns_to_fs(
                params.get("D_IN_SIO_NS", params.get("TB_FLIGHT_IN_NS", 0.0))
            )
        return int(fall_fs) + self._taclk_fs + din_fs

    def _on_launch_source(self, *, now_fs: int, in_reset: bool) -> None:
        """Check DUT-plane launch legality and retain delayed setup/hold windows."""
        changed = []
        if self._sio_out is not None:
            value = str(self._sio_out.value)
            if value != self._prev_sio_out:
                self._prev_sio_out = value
                changed.append("SIO")
        if self._sio_oe is not None:
            value = str(self._sio_oe.value)
            if value != self._prev_sio_oe:
                self._prev_sio_oe = value
                changed.append("OE")
        # Q-LAUNCH ignores SIO value changes while OE=0 (not driving the bus).
        if "SIO" in changed:
            oe_now = _nibble(self._sio_oe) if self._sio_oe is not None else 0
            if not oe_now:
                changed = [kind for kind in changed if kind != "SIO"]
        if not changed:
            return

        sck = _level(self._sck) if self._sck is not None else None
        # Q-LAUNCH (driven SIO/OE changes only while SCK is low, with modeled
        # setup/hold) applies only while the ASIC drives SCK. Grant/park and
        # reset OE collapse with asic_sck_oe==0 are CHK-ARB-* / CHK-RST-OE, not
        # launch setup/hold. Fail only on known high SCK; X/Z/None means the
        # net is undriven or unresolved, not a high-half launch window.
        if not self._asic_drives_sck():
            return
        self._launch_hits += 1
        kind = "+".join(changed)
        if sck == 1:
            self._fail_edge(
                Q_LAUNCH,
                f"{kind} changed while external SCK={sck} sck_oe=1; "
                f"required SCK low (DUT={now_fs / 1_000_000.0:.3f}ns)",
                in_reset=in_reset,
            )
        device = self._selected_timed_device()
        delays = []
        if "SIO" in changed:
            delays.append(self._d_out_fs("SIO", device))
        if "OE" in changed:
            delays.append(self._d_out_fs("OE", device))
        device_change_fs = now_fs + (max(delays) if delays else 0)
        # Inclusive hold: a same-fs change at the tHD deadline fails (tb-pin-07).
        if (
            self._launch_hold_until_fs is not None
            and device_change_fs <= self._launch_hold_until_fs
        ):
            observed = (
                device_change_fs - (self._launch_hold_until_fs - self._thd_fs)
            ) / 1_000_000.0
            self._fail_edge(
                Q_LAUNCH,
                f"{kind} device-plane hold={observed:.3f}ns <= tHD={self._thd_ns:.3f}ns "
                f"(change={device_change_fs / 1_000_000.0:.3f}ns)",
                in_reset=in_reset,
            )
        self._launch_change_fs = device_change_fs
        self._launch_change_kind = kind
        self._launch_device = device

    def _collect_timed_events(self, *, in_reset: bool) -> None:
        for device in self._timed_devices:
            events = device.timing_events
            cursor = self._timed_event_cursor[id(device)]
            for event in events[cursor:]:
                kind = event["kind"]
                if kind == "read-launch":
                    entry = dict(event)
                    entry["device_id"] = device.device_id
                    entry["input_valid_fs"] = None
                    entry["required_rise_fs"] = None
                    entry["capture_fs"] = None
                    entry["token"] = self.pending.open(
                        Q_RXEDGE,
                        severity=SEV_FAIL,
                        detail=(
                            f"launched read nibble=0x{entry['nibble']:X} "
                            f"awaiting capture for PSRAM{device.device_id}"
                        ),
                        scope=device.device_id,
                    )
                    self._rx_pending.append(entry)
                    self._rx_launches += 1
                    if device.device_id not in self._rx_expected_nibbles:
                        byte_len = _nibble(self._byte_len)
                        if byte_len is not None:
                            self._rx_expected_nibbles[device.device_id] = 2 * byte_len
                elif kind == "read-input-valid":
                    for pending in self._rx_pending:
                        if (
                            pending["device_id"] == device.device_id
                            and pending["generation"] == event["generation"]
                            and pending["input_valid_fs"] is None
                        ):
                            pending["input_valid_fs"] = event["time_fs"]
                            if pending["capture_fs"] is not None:
                                hold_ns = (
                                    event["time_fs"] - pending["capture_fs"]
                                ) / 1_000_000.0
                                self._rx_min_hold_ns = (
                                    hold_ns
                                    if self._rx_min_hold_ns is None
                                    else min(self._rx_min_hold_ns, hold_ns)
                                )
                            break
                elif kind == "read-stale":
                    self._fail_edge(
                        Q_RXEDGE,
                        f"stale read response generation={event['generation']} "
                        f"nibble=0x{event['nibble']:X} discarded at "
                        f"{event['time_fs'] / 1_000_000.0:.3f}ns",
                        in_reset=in_reset,
                    )
                elif kind == "ce-rise-committed":
                    # A device-plane SCK-fall landing between the DUT-plane
                    # CE# rise (already scope-closed by _on_ce_rise) and this
                    # generation bump can still open a fresh Q-RXEDGE pending
                    # item. Re-close the scope (idempotent: earlier items are
                    # already gone) and sweep any residual entry this device
                    # left behind before it can leak into a later CE# session.
                    self.pending.close_scope(device.device_id, reason=REASON_SCOPE)
                    self._rx_pending = [
                        entry
                        for entry in self._rx_pending
                        if not (
                            entry["device_id"] == device.device_id
                            and entry["capture_fs"] is None
                        )
                    ]
            self._timed_event_cursor[id(device)] = len(events)

    def _resolve_rx_capture(
        self,
        expected,
        now_fs: int,
        *,
        in_reset: bool,
        captured_nibble: "int | None" = None,
        require_rise_coincidence: bool = False,
    ) -> None:
        """Close one launched nibble at *now_fs* and apply Q-RXEDGE checks.

        ``Q-RXEDGE`` (each launched read nibble captured on the following
        rising SCK) requires return-plane data to be valid at or before this
        capture. Validity is the wrapper ``read-input-valid`` timestamp when
        present, and independently ``t_fall + tACLK + TB_FLIGHT_IN``.
        """
        expected["capture_fs"] = now_fs
        token = expected.get("token")
        if token is not None:
            self.pending.resolve(token)
        self._rx_captures += 1
        required_rise_fs = expected.get("required_rise_fs")
        if (
            require_rise_coincidence
            and required_rise_fs is not None
            and now_fs != required_rise_fs
        ):
            self._fail_edge(
                Q_RXEDGE,
                f"capture at {now_fs / 1_000_000.0:.3f}ns did not coincide "
                f"with required external rising edge at "
                f"{required_rise_fs / 1_000_000.0:.3f}ns",
                in_reset=in_reset,
            )
        input_valid_fs = expected.get("input_valid_fs")
        computed_fs = self._computed_rx_ready_fs(expected)
        if computed_fs is not None and computed_fs > now_fs:
            self._fail_edge(
                Q_RXEDGE,
                f"capture at {now_fs / 1_000_000.0:.3f}ns before independent "
                f"tACLK window (ready={computed_fs / 1_000_000.0:.3f}ns, "
                f"tACLK={self._taclk_ns:.3f}ns) for nibble="
                f"0x{expected['nibble']:X}",
                in_reset=in_reset,
            )
        if input_valid_fs is None or input_valid_fs > now_fs:
            shown_valid = (
                "none"
                if input_valid_fs is None
                else f"{input_valid_fs / 1_000_000.0:.3f}ns"
            )
            self._fail_edge(
                Q_RXEDGE,
                f"capture at {now_fs / 1_000_000.0:.3f}ns before return data "
                f"was valid for nibble=0x{expected['nibble']:X} "
                f"(input-valid={shown_valid})",
                in_reset=in_reset,
            )
        else:
            setup_ns = (now_fs - input_valid_fs) / 1_000_000.0
            self._rx_min_setup_ns = (
                setup_ns
                if self._rx_min_setup_ns is None
                else min(self._rx_min_setup_ns, setup_ns)
            )
            if captured_nibble is not None and captured_nibble != expected["nibble"]:
                shown = "?" if captured_nibble is None else f"0x{captured_nibble:X}"
                self._fail_edge(
                    Q_RXEDGE,
                    f"capture nibble={shown} expected=0x{expected['nibble']:X} "
                    f"(launch={expected['source_fall_fs'] / 1_000_000.0:.3f}ns "
                    f"input-valid={input_valid_fs / 1_000_000.0:.3f}ns "
                    f"capture={now_fs / 1_000_000.0:.3f}ns)",
                    in_reset=in_reset,
                )
        for label, _ in self._ram_ce_n:
            if self._fall_fs[label] is not None:
                self._read_commits[label] += 1
                self._last_read_commit_fs[label] = now_fs

    def _on_rdata_valid(self, now_fs: int, *, in_reset: bool) -> None:
        if self._rdata_valid is None:
            return
        level = _level(self._rdata_valid)
        prev = self._prev_rdata_valid
        self._prev_rdata_valid = level
        if level != 1 or prev == 1:
            return
        expected = next(
            (
                item
                for item in self._rx_pending
                if item["required_rise_fs"] is not None and item["capture_fs"] is None
            ),
            None,
        )
        if expected is None:
            self._fail_edge(
                Q_RXEDGE,
                "rdata_valid asserted without a pending read-nibble rising edge",
                in_reset=in_reset,
            )
            return
        self._resolve_rx_capture(
            expected,
            now_fs,
            in_reset=in_reset,
            captured_nibble=_nibble(self._rdata),
            require_rise_coincidence=True,
        )

    def _on_sck(self, *, now_fs: int, in_reset: bool) -> None:
        if self._sck is None:
            return
        level = _level(self._sck)
        prev = self._prev_sck
        self._prev_sck = level
        if level != 1 or prev != 0:
            return
        self._check_launch_rise(now_fs=now_fs, in_reset=in_reset)
        self._check_rx_rise(now_fs=now_fs, in_reset=in_reset)
        for label, _ in self._ram_ce_n:
            fall_fs = self._fall_fs[label]
            if fall_fs is None:
                continue
            if self._first_rise_fs[label] is None:
                self._first_rise_fs[label] = now_fs
                observed_fs = now_fs - fall_fs
                margin_ns = (observed_fs - self._tcsp_fs) / 1_000_000.0
                if self._min_csp_margin_ns is None or margin_ns < self._min_csp_margin_ns:
                    self._min_csp_margin_ns = margin_ns
                if observed_fs < self._tcsp_fs:
                    self._record_margin(
                        check_id=Q_CSP,
                        label=label,
                        observed_fs=observed_fs,
                        required_fs=self._tcsp_fs,
                        source_fs=fall_fs,
                        in_reset=in_reset,
                        relation="CE# fall to first SCK rise",
                    )
            self._prior_rise_fs[label] = self._last_rise_fs[label]
            self._last_rise_fs[label] = now_fs

    def _check_launch_rise(self, *, now_fs: int, in_reset: bool) -> None:
        if self._launch_change_fs is None:
            return
        sck_delay_fs = self._d_out_fs("SCK", self._launch_device)
        device_rise_fs = now_fs + sck_delay_fs
        if self._launch_change_fs > device_rise_fs:
            return
        observed_fs = device_rise_fs - self._launch_change_fs
        if observed_fs < self._tsp_fs:
            self._fail_edge(
                Q_LAUNCH,
                f"{self._launch_change_kind} device-plane setup="
                f"{observed_fs / 1_000_000.0:.3f}ns < tSP={self._tsp_ns:.3f}ns "
                f"(change={self._launch_change_fs / 1_000_000.0:.3f}ns "
                f"edge={device_rise_fs / 1_000_000.0:.3f}ns)",
                in_reset=in_reset,
            )
        self._launch_hold_until_fs = device_rise_fs + self._thd_fs
        self._launch_change_fs = None
        self._launch_change_kind = ""
        self._launch_device = None

    def _check_rx_rise(self, *, now_fs: int, in_reset: bool) -> None:
        pending_required = next(
            (
                item
                for item in self._rx_pending
                if item["required_rise_fs"] is not None and item["capture_fs"] is None
            ),
            None,
        )
        if pending_required is not None:
            self._fail_edge(
                Q_RXEDGE,
                f"missing capture for nibble=0x{pending_required['nibble']:X} "
                f"required at {pending_required['required_rise_fs'] / 1_000_000.0:.3f}ns",
                in_reset=in_reset,
            )
            pending_required["capture_fs"] = -1
            token = pending_required.get("token")
            if token is not None:
                self.pending.resolve(token)
        launch = next(
            (
                item
                for item in self._rx_pending
                if item["required_rise_fs"] is None
                and item["source_fall_fs"] < now_fs
            ),
            None,
        )
        if launch is None:
            return
        launch["required_rise_fs"] = now_fs
        # L1/L2 have no rdata_valid alias (tb-pin-06). The armed external
        # rising SCK is the D16 capture; apply the same tACLK check as L0.
        if self._rdata_valid is None:
            self._resolve_rx_capture(launch, now_fs, in_reset=in_reset)

    def _on_ce_fall(self, label: str, *, now_fs: int, in_reset: bool) -> None:
        device_id = 0 if label == "PSRAM0" else 1
        self.pending.close_scope(device_id, reason=REASON_SCOPE)
        self._rx_pending = [
            entry for entry in self._rx_pending if entry["device_id"] != device_id
        ]
        self._rx_expected_nibbles.pop(device_id, None)
        if self._last_ce_rise_fs is not None:
            gap_fs = now_fs - self._last_ce_rise_fs
            gap_ns = gap_fs / 1_000_000.0
            margin_ns = gap_ns - self._tcph_ns
            if self._min_cph_margin_ns is None or margin_ns < self._min_cph_margin_ns:
                self._min_cph_margin_ns = margin_ns
            if gap_fs < self._tcph_fs:
                self._fail_cph(label, gap_ns=gap_ns, in_reset=in_reset)

        self._fall_fs[label] = now_fs
        self._first_rise_fs[label] = None
        self._last_rise_fs[label] = None
        self._prior_rise_fs[label] = None
        self._read_commits[label] = 0
        self._last_read_commit_fs[label] = None
        self._fall_gen[label] += 1
        self._cem_reported[label] = False
        self._cem_tasks.append(
            cocotb.start_soon(
                self._cem_deadline(
                    label, fall_fs=now_fs, generation=self._fall_gen[label]
                )
            )
        )

    def _on_ce_rise(self, label: str, *, now_fs: int, in_reset: bool) -> None:
        device_id = 0 if label == "PSRAM0" else 1
        expected_nibbles = self._rx_expected_nibbles.pop(device_id, None)
        if expected_nibbles is not None:
            captured_nibbles = sum(
                1
                for entry in self._rx_pending
                if entry["device_id"] == device_id and entry["capture_fs"] not in (None, -1)
            )
            if captured_nibbles != expected_nibbles:
                self._fail_edge(
                    Q_RXEDGE,
                    f"read captures={captured_nibbles}, expected={expected_nibbles} "
                    f"for PSRAM{device_id}",
                    in_reset=in_reset,
                )
        # The APS6404L-style model prefetches one unread nibble on the final
        # SCK fall before CE# rises. That launch never receives a following
        # rising edge (SCK stays frozen low under Q-TERM), so resolve it here
        # instead of letting dispose treat it as a missed capture.
        remaining = []
        for entry in self._rx_pending:
            if entry["device_id"] != device_id:
                remaining.append(entry)
                continue
            token = entry.get("token")
            if entry["capture_fs"] is not None:
                # Already captured, or already marked missing (-1) on a later rise.
                continue
            if entry["required_rise_fs"] is None:
                if token is not None:
                    self.pending.resolve(token)
                continue
            self._fail_edge(
                Q_RXEDGE,
                f"missing capture for nibble=0x{entry['nibble']:X} "
                f"required at {entry['required_rise_fs'] / 1_000_000.0:.3f}ns "
                f"before CE# rise",
                in_reset=in_reset,
            )
            if token is not None:
                self.pending.resolve(token)
        self._rx_pending = remaining
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

        final_rise_fs = self._last_rise_fs[label]
        if final_rise_fs is not None:
            observed_fs = now_fs - final_rise_fs
            margin_ns = (observed_fs - self._tchd_fs) / 1_000_000.0
            if self._min_chd_margin_ns is None or margin_ns < self._min_chd_margin_ns:
                self._min_chd_margin_ns = margin_ns
            if observed_fs < self._tchd_fs:
                self._record_margin(
                    check_id=Q_CHD,
                    label=label,
                    observed_fs=observed_fs,
                    required_fs=self._tchd_fs,
                    source_fs=final_rise_fs,
                    in_reset=in_reset,
                    relation="final SCK rise to CE# rise",
                )
        self._check_term(label, now_fs=now_fs, in_reset=in_reset)
        self._fall_fs[label] = None
        self._fall_gen[label] += 1
        self._cem_reported[label] = False
        self._last_ce_rise_fs = now_fs
        self._last_rise_label = label

    def _expected_read_nibbles(self, label: str):
        if self._read_expected_nibbles is None:
            return None
        try:
            return self._read_expected_nibbles(label)
        except TypeError:
            return self._read_expected_nibbles()

    def _check_term(self, label: str, *, now_fs: int, in_reset: bool) -> None:
        expected = self._expected_read_nibbles(label)
        commits = self._read_commits[label]
        is_read = expected is not None or commits > 0
        if not is_read:
            return
        final_rise_fs = self._last_rise_fs[label]
        sck_low = self._sck is None or _level(self._sck) == 0
        committed = commits if expected is None else commits == expected
        if final_rise_fs is None or not committed or not sck_low:
            reasons = []
            if final_rise_fs is None:
                reasons.append("no final rising SCK")
            if not committed:
                reasons.append(
                    f"committed read nibbles={commits}, expected={expected}"
                )
            if not sck_low:
                reasons.append("SCK was not frozen low at CE# rise")
            self._record(
                TimingViolation(
                    check_id=Q_TERM,
                    time_ns=_now_ns(),
                    detail=(
                        f"{label} read termination failed: {'; '.join(reasons)} "
                        f"(CE# rise={now_fs / 1_000_000.0:.3f}ns)"
                        + (
                            f"; reset-sample={_now_ns():.3f}ns "
                            "(forced CE#/SCK reset convergence)"
                            if in_reset
                            else ""
                        )
                    ),
                    reset_truncated=in_reset,
                    ce_label=label,
                )
            )
            return

        prior_rise_fs = self._prior_rise_fs[label]
        if prior_rise_fs is None:
            observed_period_fs = 0
        else:
            observed_period_fs = final_rise_fs - prior_rise_fs
        hold_fs = now_fs - final_rise_fs
        advisory_fs = hold_fs - (self._taclk_ns * 1_000_000 + observed_period_fs)
        advisory_ns = advisory_fs / 1_000_000.0
        if self._min_term_margin_ns is None or advisory_ns < self._min_term_margin_ns:
            self._min_term_margin_ns = advisory_ns
        detail = (
            f"{label} Q-TERM advisory: final CE# hold={hold_fs / 1_000_000.0:.3f}ns "
            f"required-guidance=(tACLK={self._taclk_ns:.3f}ns + "
            f"tCLK={observed_period_fs / 1_000_000.0:.3f}ns) "
            f"margin={advisory_ns:.3f}ns; commits={commits}"
        )
        self.term_margins.append(detail)
        if self._log is not None:
            self._log.info("CHECKER ADVISORY id=%s level=%s %s", Q_TERM, self.level, detail)

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
        dispositions = {}
        for check_id in CE_TIMING_CHECK_IDS:
            if counts[check_id] or check_id in self._overflow_ids:
                dispositions[check_id] = RESULT_FAIL
            elif check_id == Q_RXEDGE:
                if not self._timed_devices:
                    dispositions[check_id] = RESULT_BLOCKED
                elif not self._rx_launches:
                    dispositions[check_id] = RESULT_NA
                else:
                    dispositions[check_id] = RESULT_PASS
            elif check_id == Q_LAUNCH:
                if not self._launch_hits:
                    dispositions[check_id] = RESULT_NA
                else:
                    dispositions[check_id] = RESULT_PASS
            else:
                dispositions[check_id] = RESULT_PASS
        return dispositions

    def blocked_reasons(self) -> "dict[str, str]":
        """Return why ``Q-RXEDGE`` is blocked when there is no timed stream."""
        if self._timed_devices or self.counts()[Q_RXEDGE] or Q_RXEDGE in self._overflow_ids:
            return {}
        return {
            Q_RXEDGE: "no timed read stream (timed_devices empty); cannot apply tACLK"
        }

    @property
    def suppressed(self) -> int:
        """Count of findings dropped after ``max_events`` (tb-pin-09)."""
        return self._suppressed

    def violations_for(self, check_id: str) -> "list[TimingViolation]":
        return [event for event in self.events if event.check_id == check_id]

    def summary(self) -> str:
        parts = [f"{check_id}={result}" for check_id, result in self.results().items()]
        if self._min_cem_margin_ns is not None:
            parts.append(f"min_cem_margin_ns={self._min_cem_margin_ns:.3f}")
        if self._min_cph_margin_ns is not None:
            parts.append(f"min_cph_margin_ns={self._min_cph_margin_ns:.3f}")
        if self._min_csp_margin_ns is not None:
            parts.append(f"min_csp_margin_ns={self._min_csp_margin_ns:.3f}")
        if self._min_chd_margin_ns is not None:
            parts.append(f"min_chd_margin_ns={self._min_chd_margin_ns:.3f}")
        if self._min_term_margin_ns is not None:
            parts.append(f"min_term_advisory_margin_ns={self._min_term_margin_ns:.3f}")
        if self._rx_min_setup_ns is not None:
            parts.append(f"min_rx_setup_margin_ns={self._rx_min_setup_ns:.3f}")
        if self._rx_min_hold_ns is not None:
            parts.append(f"min_rx_hold_margin_ns={self._rx_min_hold_ns:.3f}")
        if self._rx_captures:
            parts.append(f"rx_captures={self._rx_captures}")
        if self.reset_truncated:
            parts.append(f"reset_truncated={len(self.reset_truncated)}")
        if self._suppressed:
            parts.append(f"suppressed={self._suppressed}")
        return (
            f"{self.name} ({self.level}, tCEM={self._tcem_ns:g}ns, "
            f"tCPH={self._tcph_ns:g}ns, tCSP={self._tcsp_ns:g}ns, "
            f"tCHD={self._tchd_ns:g}ns, {self._samples} samples): "
            + " ".join(parts)
        )


# -- attachment ------------------------------------------------------------


def _optional(dut, name):
    return getattr(dut, name, None)


def _first_optional(dut, *names):
    for name in names:
        handle = _optional(dut, name)
        if handle is not None:
            return handle
    return None


def resolve_ce_timing_thresholds(
    timing_params=None,
    *,
    grade: str = "extended",
    tcem_ns: "float | None" = None,
    tcph_ns: "float | None" = None,
) -> "tuple[float, float]":
    """Return ``(tCEM_ns, tCPH_ns)`` from explicit args, else the profile manifest.

    ``tCEM`` is max CE# low; ``tCPH`` is min CE# high between bursts. Explicit
    arguments win. Profile keys ``PSRAM_TCEM_NS`` or ``PSRAM_TCEM_US_*`` and
    ``PSRAM_TCPH_NS`` feed the monitor when args are omitted.
    """
    if grade not in ("extended", "standard"):
        raise ValueError(f"grade must be 'extended' or 'standard', got {grade!r}")
    params = {} if timing_params is None else dict(timing_params)
    if tcem_ns is None:
        if "PSRAM_TCEM_NS" in params and params["PSRAM_TCEM_NS"] is not None:
            tcem_ns = float(params["PSRAM_TCEM_NS"])
        else:
            us_key = "PSRAM_TCEM_US_STD" if grade == "standard" else "PSRAM_TCEM_US_EXT"
            if us_key in params and params[us_key] is not None:
                tcem_ns = float(params[us_key]) * 1000.0
            else:
                tcem_ns = PSRAM_TCEM_NS_STD if grade == "standard" else PSRAM_TCEM_NS_EXT
    if tcph_ns is None:
        tcph_ns = float(params["PSRAM_TCPH_NS"]) if params.get("PSRAM_TCPH_NS") is not None else PSRAM_TCPH_NS
    return float(tcem_ns), float(tcph_ns)


def start_ce_timing_monitor(
    dut,
    *,
    strict: bool = False,
    tcem_ns: "float | None" = None,
    tcph_ns: "float | None" = None,
    tcsp_ns: "float | None" = None,
    tchd_ns: "float | None" = None,
    taclk_ns: "float | None" = None,
    tsp_ns: "float | None" = None,
    thd_ns: "float | None" = None,
    timing_params=None,
    timed_devices=(),
    read_expected_nibbles=None,
    grade: str = "extended",
    **kwargs,
) -> CeTimingMonitor:
    """Create and start the CE# timing monitor for *dut*.

    Works against ``tb_top`` / ``tb_gl`` / ``tb_engine`` via the wrapper aliases
    ``bus_ram_a_cs_n`` / ``bus_ram_b_cs_n``. Pass ``strict=True`` to raise at the
    first violation; otherwise collect into :attr:`CeTimingMonitor.events`.

    ``grade`` selects the datasheet ``tCEM`` when ``tcem_ns`` is omitted:
    ``extended`` → 4 us, ``standard`` → 8 us. An explicit ``tcem_ns`` wins.
    ``timing_params`` is the resolved transport manifest when attached through
    :mod:`common.bringup`; explicit threshold overrides take precedence.  A
    directed read may provide ``read_expected_nibbles(label)`` so ``Q-TERM``
    can require the exact final capture count rather than only observing a
    committed ``rdata_valid`` pulse.

    ``timed_devices`` is the wrapped device tuple returned by bring-up. It
    supplies the modeled falling-edge launch and return-plane valid timestamps
    needed by ``Q-RXEDGE``. Without a timed wrapper, ``Q-RXEDGE`` is
    ``blocked`` (cannot apply ``tACLK`` independently). Write-only traffic
    with wrappers attached but no ``read-launch`` is ``na``, not pass.

    L0 captures through ``rdata_valid`` plus nibble match. L1/L2 have no
    ``rdata_valid`` alias on ``tb_top`` / ``tb_uio_bus.svh``; the armed
    external rising SCK is the D16 capture. L0 ``tb_engine`` has no
    ``asic_sck_oe`` because the engine always drives SCK; missing SCK OE is
    treated as 1 only at L0. Per-device ``D_OUT_*`` delays apply to the
    currently selected CE#; SIO value changes while OE=0 are not launches.
    """
    tcem_ns, tcph_ns = resolve_ce_timing_thresholds(
        timing_params, grade=grade, tcem_ns=tcem_ns, tcph_ns=tcph_ns
    )
    timing_params = {} if timing_params is None else timing_params
    if tcsp_ns is None:
        tcsp_ns = timing_params.get("PSRAM_TCSP_NS", PSRAM_TCSP_NS)
    if tchd_ns is None:
        tchd_ns = timing_params.get("PSRAM_TCHD_NS", PSRAM_TCHD_NS)
    if taclk_ns is None:
        taclk_ns = timing_params.get("PSRAM_TACLK_NS", PSRAM_TACLK_NS)
    if tsp_ns is None:
        tsp_ns = timing_params.get("PSRAM_TSP_NS", PSRAM_TSP_NS)
    if thd_ns is None:
        thd_ns = timing_params.get("PSRAM_THD_NS", PSRAM_THD_NS)

    monitor = CeTimingMonitor(
        ram_ce_n=(("PSRAM0", dut.bus_ram_a_cs_n), ("PSRAM1", dut.bus_ram_b_cs_n)),
        rst_n=_optional(dut, "rst_n"),
        tcem_ns=tcem_ns,
        tcph_ns=tcph_ns,
        tcsp_ns=tcsp_ns,
        tchd_ns=tchd_ns,
        taclk_ns=taclk_ns,
        tsp_ns=tsp_ns,
        thd_ns=thd_ns,
        sck=_first_optional(dut, "bus_sck", "psram_sck", "sclk"),
        sck_oe=_first_optional(dut, "asic_sck_oe", "sck_oe"),
        sio_out=_first_optional(dut, "asic_sio_out", "sio_out"),
        sio_oe=_first_optional(dut, "asic_sio_oe", "sio_oe"),
        rdata_valid=_first_optional(dut, "rdata_valid", "qspi_rdata_valid"),
        rdata=_first_optional(dut, "rdata", "qspi_rdata"),
        busy=_first_optional(dut, "busy", "qspi_busy"),
        byte_len=_first_optional(dut, "byte_len", "qspi_byte_len"),
        timed_devices=timed_devices,
        read_expected_nibbles=read_expected_nibbles,
        level=kwargs.pop("level", "L0" if _optional(dut, "bus_flash_cs_n") is None else "L1"),
        strict=strict,
        log=kwargs.pop("log", dut._log),
        **kwargs,
    )
    monitor.start()
    return monitor
