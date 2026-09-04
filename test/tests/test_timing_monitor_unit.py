"""Pure-Python unit tests for ``CeTimingMonitor`` disposition (no simulator).

Covers tb-pin-05 (Q-RXEDGE blocked/na vs pass), tb-pin-04 (zero-event Q-LAUNCH
is na), tb-pin-09 (overflow fails the dropped ID), and tb-pin-11 (per-device
D_OUT_* and missing SCK OE).
"""

from common.constants import RESULT_BLOCKED, RESULT_FAIL, RESULT_NA, RESULT_PASS
from monitors.timing import (
    Q_CEM,
    Q_LAUNCH,
    Q_RXEDGE,
    CeTimingMonitor,
    TimingViolation,
    resolve_ce_timing_thresholds,
)

class _Handle:
    def __init__(self, value=1):
        self.value = value

class _TimedDevice:
    def __init__(self, device_id, **params):
        self.device_id = device_id
        self.timing_events = []
        self.timing_params = params

def _monitor(**kwargs) -> CeTimingMonitor:
    ram = kwargs.pop(
        "ram_ce_n",
        (("PSRAM0", _Handle(1)), ("PSRAM1", _Handle(1))),
    )
    return CeTimingMonitor(ram_ce_n=ram, **kwargs)

def test_rxedge_blocked_without_timed_devices():
    """tb-pin-05: no timed read stream is blocked, not pass."""
    monitor = _monitor()
    assert monitor.results()[Q_RXEDGE] == RESULT_BLOCKED
    assert "tACLK" in monitor.blocked_reasons()[Q_RXEDGE]

def test_rxedge_na_without_read_launch():
    """tb-pin-05/06: wrappers attached but write-only / no launch is na."""
    monitor = _monitor(timed_devices=(_TimedDevice(0), _TimedDevice(1)))
    assert monitor.results()[Q_RXEDGE] == RESULT_NA

def test_launch_na_without_hits():
    """tb-pin-04: zero-event Q-LAUNCH is na, not pass."""
    monitor = _monitor()
    assert monitor.results()[Q_LAUNCH] == RESULT_NA

def test_overflow_fails_dropped_id():
    """tb-pin-09: max_events overflow fails the suppressed ID via results()."""
    monitor = _monitor(max_events=1)
    monitor._record(TimingViolation(check_id=Q_CEM, time_ns=0.0, detail="kept"))
    monitor._record(TimingViolation(check_id=Q_RXEDGE, time_ns=0.0, detail="dropped"))
    assert monitor.suppressed == 1
    assert monitor.results()[Q_CEM] == RESULT_FAIL
    assert monitor.results()[Q_RXEDGE] == RESULT_FAIL
    assert Q_RXEDGE not in {event.check_id for event in monitor.events}

def test_per_device_d_out_uses_selected_ce():
    """tb-pin-11: D_OUT_* comes from the CE#-selected device, not [0]."""
    ce0 = _Handle(0)
    ce1 = _Handle(1)
    monitor = _monitor(
        ram_ce_n=(("PSRAM0", ce0), ("PSRAM1", ce1)),
        timed_devices=(
            _TimedDevice(0, D_OUT_SIO_NS=1.0, D_OUT_OE_NS=0.0, D_OUT_SCK_NS=0.0),
            _TimedDevice(1, D_OUT_SIO_NS=9.0, D_OUT_OE_NS=0.0, D_OUT_SCK_NS=0.0),
        ),
    )
    assert monitor._d_out_fs("SIO") == 1_000_000
    ce0.value = 1
    ce1.value = 0
    assert monitor._d_out_fs("SIO") == 9_000_000

def test_missing_sck_oe_is_drive_only_at_l0():
    """tb-pin-11: missing asic_sck_oe is 1 at L0 (engine always owns SCK)."""
    l0 = _monitor(level="L0")
    l1 = _monitor(level="L1")
    assert l0._asic_drives_sck() is True
    assert l1._asic_drives_sck() is False

def test_clear_resets_overflow_and_hits():
    monitor = _monitor(max_events=1, timed_devices=(_TimedDevice(0),))
    monitor._launch_hits = 3
    monitor._rx_launches = 2
    monitor._record(TimingViolation(check_id=Q_CEM, time_ns=0.0, detail="kept"))
    monitor._record(TimingViolation(check_id=Q_LAUNCH, time_ns=0.0, detail="dropped"))
    monitor.clear()
    assert monitor.suppressed == 0
    assert monitor.results()[Q_LAUNCH] == RESULT_NA
    assert monitor.results()[Q_RXEDGE] == RESULT_NA
    assert monitor.results()[Q_CEM] == RESULT_PASS

def test_ce_thresholds_consume_profile_unless_explicit():
    """cov-tim-06: tCEM (max CE# low) and tCPH (CE# high gap) follow the manifest."""
    tcem, tcph = resolve_ce_timing_thresholds(
        {"PSRAM_TCEM_US_EXT": 4.0, "PSRAM_TCPH_NS": 18.0}
    )
    assert tcem == 4000.0
    assert tcph == 18.0
    tcem, tcph = resolve_ce_timing_thresholds(
        {"PSRAM_TCEM_US_EXT": 4.0, "PSRAM_TCPH_NS": 18.0},
        tcem_ns=100.0,
        tcph_ns=9.0,
    )
    assert tcem == 100.0 and tcph == 9.0
    tcem, tcph = resolve_ce_timing_thresholds(
        {"PSRAM_TCEM_NS": 250.0, "PSRAM_TCPH_NS": 22.0}
    )
    assert tcem == 250.0 and tcph == 22.0
