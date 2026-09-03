"""Unified violation dispose: one call decides a run's checker outcome.

Every M1 suite grew its own end-of-test assertions (PSRAM logs, ownership
violations, CE# timing, ``RESET-TRUNCATED`` review, expected-negative IDs).
This module is the single place that turns recorded findings into a pass/fail
decision, so no test can quietly drop a catalog row.

Preferred API: :func:`dispose_run` (and, for pin rows alone,
:func:`monitors.qspi.dispose_pin_checks`). Prefer those over the legacy
:func:`monitors.qspi.dispose_model_pin_checks` /
:func:`monitors.qspi.assert_model_pin_disposition` helpers; M1 call-sites of
the model-only path remain until a later migration wave.

Contract, from ``docs/llm/verification/06-checkers.md`` and
``04-timing-in-sim.md``:

* an **ordinary** finding fails the test unless the test declared it,
* a declared negative names the ID and, optionally, the exact count,
* a ``RESET-TRUNCATED`` finding is never an ordinary fail and is never silently
  ignored: the test must choose ``review`` or ``require``, and
* every applicable ID gets a printed disposition (``pass`` / ``fail`` / ``na`` /
  ``blocked``), including the rows a monitor could not judge.

Typical use::

    report = dispose_run(bringup, test="TC-SMOKE", log=dut._log, repro=repro)

Expected-negative use::

    dispose_run(
        bringup,
        test="TC-QNEG-OPCODE",
        expect_fail=[expect(Q_OPCODE, count=1)],
        log=dut._log,
    )

Reset review use::

    dispose_run(bringup, test="TC-QRST-ACTIVE", reset_truncated=REQUIRE, ...)
"""

from dataclasses import dataclass, field

from common.bringup import BringUp
from common.lifecycle import REASON_DISPOSE, finalize_all
from models.psram import (
    CLASS_RESET_TRUNCATED,
    PsramDevice,
    PsramQpiAgent,
    QpiViolation,
)
from monitors.arbitration import ArbitrationMonitor
from monitors.handshake import ControllerMonitor, HandshakeMonitor
from monitors.qspi import (
    CHK_PIN_KNOWN,
    DUAL_FINDING_TWINS,
    MODEL_PIN_CHECK_IDS,
    Q_SIO_X,
    QspiPinMonitor,
    SharedBusMonitor,
    twin_ids,
)
from monitors.timing import CeTimingMonitor

from common.constants import (
    FORBID,
    REQUIRE,
    RESULT_BLOCKED,
    RESULT_FAIL,
    RESULT_NA,
    RESULT_PASS,
    REVIEW,
)

_RESET_POLICIES = (FORBID, REVIEW, REQUIRE)


@dataclass(frozen=True)
class Expected:
    """One declared negative: a catalog/model ID and its allowed count.

    ``count=None`` (the default) means at least one occurrence (``>=1``).
    Pass ``count=N`` for an exact count.
    """

    check_id: str
    count: "int | None" = None  # None means "one or more"

    def matches(self, observed: int) -> bool:
        if self.count is None:
            return observed >= 1
        return observed == self.count

    def __str__(self) -> str:
        return (
            f"{self.check_id} x{self.count}"
            if self.count is not None
            else f"{self.check_id} (>=1)"
        )


def expect(check_id: str, count: "int | None" = None) -> Expected:
    """Declare an expected failing ID, optionally with an exact count."""
    return Expected(check_id=check_id, count=count)


@dataclass(frozen=True)
class Finding:
    """One normalized recorded finding from any monitor or model."""

    check_id: str
    source: str
    time_ns: float
    detail: str
    reset_truncated: bool = False

    def __str__(self) -> str:
        prefix = "RESET-TRUNCATED " if self.reset_truncated else ""
        return (
            f"{prefix}{self.check_id} [{self.source}] "
            f"t={self.time_ns:.3f}ns: {self.detail}"
        )


@dataclass
class DisposeReport:
    """Per-ID disposition for one test window."""

    test: str
    results: "dict[str, str]" = field(default_factory=dict)
    counts: "dict[str, int]" = field(default_factory=dict)
    blocked_reasons: "dict[str, str]" = field(default_factory=dict)
    via: "dict[str, str]" = field(default_factory=dict)
    ordinary: "list[Finding]" = field(default_factory=list)
    reset_truncated: "list[Finding]" = field(default_factory=list)
    truncated_counts: "dict[str, int]" = field(default_factory=dict)
    expected: "tuple[Expected, ...]" = ()
    sources: "tuple[str, ...]" = ()
    pin_transactions: tuple = ()

    def failures(self) -> "list[str]":
        return [check_id for check_id, result in self.results.items() if result == RESULT_FAIL]

    def blocked(self) -> "list[str]":
        return [check_id for check_id, result in self.results.items() if result == RESULT_BLOCKED]

    def summary(self) -> str:
        parts = [f"{check_id}={result}" for check_id, result in sorted(self.results.items())]
        if self.reset_truncated:
            parts.append(f"reset_truncated={len(self.reset_truncated)}")
        return f"{self.test} dispose: " + " ".join(parts)


# -- source normalization --------------------------------------------------


def _source_name(obj, fallback: str) -> str:
    return getattr(obj, "name", None) or fallback


def _model_findings(agent: PsramQpiAgent) -> "list[Finding]":
    findings = []
    source = f"PSRAM{agent.device_id}"
    for record in agent.violations:
        assert isinstance(record, QpiViolation), (
            f"{source} violation log holds {type(record).__name__}, "
            "expected QpiViolation"
        )
        findings.append(
            Finding(
                check_id=record.code,
                source=source,
                time_ns=record.sim_time_ns,
                detail=record.detail,
                reset_truncated=record.classification == CLASS_RESET_TRUNCATED,
            )
        )
    return findings


def _carryover_findings(records) -> "list[Finding]":
    """Normalize snapshotted model/monitor records from ``BringUp.clear``."""
    findings = []
    seen = set()
    for record in records:
        check_id = getattr(record, "check_id", None) or getattr(record, "code", None)
        if check_id is None:
            continue
        time_ns = getattr(record, "time_ns", None)
        if time_ns is None:
            time_ns = getattr(record, "sim_time_ns", 0.0)
        detail = getattr(record, "detail", "") or str(record)
        reset_truncated = bool(getattr(record, "reset_truncated", False))
        if getattr(record, "classification", None) == CLASS_RESET_TRUNCATED:
            reset_truncated = True
        key = (check_id, float(time_ns), str(detail), reset_truncated)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            Finding(
                check_id=check_id,
                source="carryover",
                time_ns=float(time_ns),
                detail=str(detail),
                reset_truncated=reset_truncated,
            )
        )
    return findings


def _monitor_findings(monitor) -> "list[Finding]":
    source = _source_name(monitor, type(monitor).__name__)
    findings = []
    pending = getattr(monitor, "pending", None)
    carryover = getattr(pending, "carryover", ()) if pending is not None else ()
    seen = set()
    for event in (
        list(getattr(monitor, "events", ()))
        + list(getattr(monitor, "reset_truncated", ()))
        + list(carryover)
    ):
        check_id = getattr(event, "check_id", None)
        time_ns = getattr(event, "time_ns", None)
        detail = getattr(event, "detail", None)
        if check_id is None or time_ns is None or detail is None:
            continue
        reset_truncated = bool(getattr(event, "reset_truncated", False))
        key = (check_id, time_ns, detail, reset_truncated)
        if key in seen:
            continue
        seen.add(key)
        source_name = getattr(event, "source", source)
        findings.append(
            Finding(
                check_id=check_id,
                source=source_name,
                time_ns=time_ns,
                detail=detail,
                reset_truncated=reset_truncated,
            )
        )
        # Dual dispose rows for shared-bus Q twins (Q-MUX / Q-SIO-OWN /
        # Q-SCKIDLE). Q-SIO-X is not dual-emitted here: the model already
        # records it, and a second pin finding would double-count.
        timing_id = getattr(event, "timing_id", None)
        if (
            timing_id
            and timing_id != check_id
            and timing_id in DUAL_FINDING_TWINS
            and not reset_truncated
        ):
            twin_key = (timing_id, time_ns, detail, reset_truncated)
            if twin_key not in seen:
                seen.add(twin_key)
                findings.append(
                    Finding(
                        check_id=timing_id,
                        source=source_name,
                        time_ns=time_ns,
                        detail=detail,
                        reset_truncated=reset_truncated,
                    )
                )
    return findings


# Monitor types dispose_run accepts directly. Every entry exposes the same
# surface collect() relies on: results(), counts(), blocked_reasons(),
# violations_for(), review_reset_truncated().
_MONITOR_TYPES = (
    SharedBusMonitor,
    CeTimingMonitor,
    HandshakeMonitor,
    QspiPinMonitor,
    ArbitrationMonitor,
    ControllerMonitor,
)


def _unique_identity(items) -> list:
    unique = []
    seen = set()
    for item in items:
        identity = id(item)
        if identity not in seen:
            seen.add(identity)
            unique.append(item)
    return unique


def _expand(sources) -> "tuple[list, list, list]":
    """Split arbitrary dispose sources into agents, monitors, and participants."""
    agents: list = []
    monitors: list = []
    participants: list = []
    for item in sources:
        if item is None:
            continue
        if isinstance(item, BringUp):
            agents.extend(
                device.agent for device in item.devices if device.agent is not None
            )
            monitors.extend(item.monitors)
            participants.extend(item.participants)
            continue
        if isinstance(item, PsramDevice):
            participants.append(item)
            if item.agent is not None:
                agents.append(item.agent)
                participants.append(item.agent)
            continue
        if isinstance(item, PsramQpiAgent):
            agents.append(item)
            participants.append(item)
            continue
        if isinstance(item, _MONITOR_TYPES):
            monitors.append(item)
            participants.append(item)
            continue
        if hasattr(item, "device_id") and hasattr(item, "agent"):
            participants.append(item)
            agent = item.agent
            if agent is not None:
                agents.append(agent)
                participants.append(agent)
            continue
        raise TypeError(
            f"dispose source {type(item).__name__} is not a BringUp, PsramDevice, "
            "PsramQpiAgent, or one of "
            f"{', '.join(cls.__name__ for cls in _MONITOR_TYPES)}"
        )
    return (
        _unique_identity(agents)
        , _unique_identity(monitors)
        , _unique_identity(participants)
    )


def _normalize_expected(expect_fail) -> "tuple[Expected, ...]":
    normalized = []
    for entry in expect_fail:
        if isinstance(entry, Expected):
            normalized.append(entry)
        elif isinstance(entry, str):
            normalized.append(Expected(check_id=entry))
        else:
            raise TypeError(
                f"expect_fail entries must be str or Expected, got {type(entry).__name__}"
            )
    return tuple(normalized)


# -- dispose ---------------------------------------------------------------


def collect(*sources) -> "tuple[list[Finding], dict[str, str], dict[str, int], dict[str, str], dict[str, str], tuple[str, ...], tuple[str, ...]]":
    """Return ``(findings, results, counts, blocked_reasons, via, source_names)``.

    Results start from each monitor's own per-ID disposition so structurally
    unavailable (``na``) and unjudgeable (``blocked``) rows survive into the
    report even when nothing was recorded against them.

    ``CHK-PIN-KNOWN`` prefers a live
    :class:`QspiPinMonitor` (``via=pin``). The Q twin ``Q-SIO-X`` is a
    dispose row from that pin monitor. When no usable pin monitor is present,
    both ``CHK-PIN-KNOWN`` and ``Q-SIO-X`` are ``na`` (L0 defaults
    ``pin_monitor=False``; do not claim pin coverage via a tautological model
    mapping). Model ``Q-SIO-X`` records remain ordinary findings when they fire.
    """
    agents, monitors, participants = _expand(sources)
    finalize_all(participants, reason=REASON_DISPOSE)

    findings: "list[Finding]" = []
    results: "dict[str, str]" = {}
    counts: "dict[str, int]" = {}
    blocked_reasons: "dict[str, str]" = {}
    via: "dict[str, str]" = {}
    names: "list[str]" = []

    for agent in agents:
        names.append(f"PSRAM{agent.device_id}")
        findings.extend(_model_findings(agent))

    for item in sources:
        if isinstance(item, BringUp) and item.event_carryover:
            findings.extend(_carryover_findings(item.event_carryover))

    for monitor in monitors:
        names.append(_source_name(monitor, type(monitor).__name__))
        findings.extend(_monitor_findings(monitor))
        results.update(monitor.results())
        reasons = getattr(monitor, "blocked_reasons", None)
        if reasons is not None:
            blocked_reasons.update(reasons())

    usable_pin = [
        monitor
        for monitor in monitors
        if isinstance(monitor, QspiPinMonitor) and not monitor.blocked
    ]

    blocked_pin = [
        monitor
        for monitor in monitors
        if isinstance(monitor, QspiPinMonitor) and monitor.blocked
    ]

    # Pin-decoded rows win when a usable pin monitor ran. Without one, pin
    # coverage of CHK-PIN-KNOWN / Q-SIO-X is na (not a tautological map from
    # model Q-SIO-X). A blocked pin monitor keeps ``blocked``. Model Q-SIO-X
    # findings still fail that Q id below.
    if usable_pin:
        for check_id in MODEL_PIN_CHECK_IDS:
            via[check_id] = "pin"
            hit = sum(monitor.counts()[check_id] for monitor in usable_pin)
            results[check_id] = RESULT_FAIL if hit else RESULT_PASS
            via[Q_SIO_X] = "pin"
            results[Q_SIO_X] = results[check_id]
    elif not blocked_pin:
        results[CHK_PIN_KNOWN] = RESULT_NA
        results[Q_SIO_X] = RESULT_NA
        via[CHK_PIN_KNOWN] = "na"
        via[Q_SIO_X] = "na"
        blocked_reasons.pop(CHK_PIN_KNOWN, None)
        blocked_reasons.pop(Q_SIO_X, None)

    overflow = []
    for monitor in monitors:
        suppressed = int(getattr(monitor, "_suppressed", 0) or 0)
        if suppressed:
            overflow.append(
                f"{_source_name(monitor, type(monitor).__name__)} "
                f"dropped {suppressed} event(s) past max_events"
            )

    return findings, results, counts, blocked_reasons, via, tuple(names), tuple(overflow)


def dispose_run(
    *sources,
    test: str,
    log=None,
    expect_fail=(),
    expect_blocked=(),
    reset_truncated: str = FORBID,
    reset_truncated_allow=None,
    reset_truncated_count: "int | None" = None,
    repro: str = "",
    quiet: bool = False,
) -> DisposeReport:
    """Dispose every recorded finding from *sources* and assert the outcome.

    *sources* may be :class:`common.bringup.BringUp` bundles, PSRAM devices or
    agents, or any started monitor (including :class:`QspiPinMonitor`).
    ``CHK-PIN-KNOWN`` prefers a live pin monitor
    (``via=pin`` in the disposition log) and prints the ``Q-SIO-X`` twin as
    its own row. Without a pin monitor both pin IDs are ``na`` (L0 default);
    a model ``Q-SIO-X`` finding still fails that Q id. Ordinary findings must
    be empty unless declared through *expect_fail* (a string ID, or
    :func:`expect` with a count). Twin IDs (``Q-MUX``/``CHK-PIN-CS-MUTEX``,
    ``Q-SIO-X``/``CHK-PIN-KNOWN``, ...) are accepted as declared together:
    expecting one is not surprised by the other. Expecting a dual-emitted Q
    twin (``Q-MUX``, ``Q-SIO-OWN``, ``Q-SCKIDLE``) fails if only the CHK row
    ran. ``RESET-TRUNCATED`` findings follow *reset_truncated*:
    :data:`FORBID` (default), :data:`REVIEW`, or :data:`REQUIRE`.
    Monitor ``max_events`` overflow (``_suppressed``) fails the dispose.

    Raises:
        AssertionError: on an undeclared finding, a declared ID that did not
            fire the declared number of times, or an unreviewed
            ``RESET-TRUNCATED`` finding.
    """
    if reset_truncated not in _RESET_POLICIES:
        raise ValueError(
            f"reset_truncated must be one of {_RESET_POLICIES}, got {reset_truncated!r}"
        )

    expected = _normalize_expected(expect_fail)
    findings, results, counts, blocked_reasons, via, names, overflow = collect(*sources)

    ordinary = [finding for finding in findings if not finding.reset_truncated]
    truncated = [finding for finding in findings if finding.reset_truncated]
    truncated_counts: "dict[str, int]" = {}
    for finding in truncated:
        truncated_counts[finding.check_id] = truncated_counts.get(finding.check_id, 0) + 1

    for finding in ordinary:
        counts[finding.check_id] = counts.get(finding.check_id, 0) + 1
        results[finding.check_id] = RESULT_FAIL
    for check_id in results:
        counts.setdefault(check_id, 0)

    for item in sources:
        if isinstance(item, BringUp):
            item._disposed = True
            item.event_carryover.clear()
            # The judged window must not leak into the next clear()/dispose on
            # the same BringUp (ownership sub-steps park+clear between cases).
            for monitor in item.monitors:
                monitor.clear()
            for agent in item.agents:
                agent.violations.clear()

    report = DisposeReport(
        test=test,
        results=results,
        counts=counts,
        blocked_reasons=blocked_reasons,
        via=via,
        ordinary=ordinary,
        reset_truncated=truncated,
        truncated_counts=truncated_counts,
        expected=expected,
        sources=names,
    )

    if log is not None and not quiet:
        _log_report(report, log)

    suffix = f" {repro}" if repro else ""
    prefix = f"{test}: " if test else ""

    assert not overflow, (
        f"{prefix}monitor event cap overflow hid later IDs: "
        f"{'; '.join(overflow)}.{suffix}"
    )

    expected_ids = set()
    for entry in expected:
        expected_ids.add(entry.check_id)
        expected_ids.update(twin_ids(entry.check_id))

    for entry in expected:
        observed = counts.get(entry.check_id, 0)
        if not entry.matches(observed):
            # Q-SIO-X accepts CHK-PIN-KNOWN (and vice versa) when the
            # requested ID itself did not fire. Dual-emitted ownership twins
            # must still appear under their Q name: do not accept CHK-only
            # for Q-MUX / Q-SIO-OWN / Q-SCKIDLE.
            if entry.check_id not in DUAL_FINDING_TWINS:
                for twin in twin_ids(entry.check_id):
                    twin_count = counts.get(twin, 0)
                    if entry.matches(twin_count):
                        observed = twin_count
                        break
        assert entry.matches(observed), (
            f"{prefix}expected {entry}, observed {observed} occurrence(s). "
            f"Recorded: {format_findings(ordinary) or '<none>'}.{suffix}"
        )

    surprises = [finding for finding in ordinary if finding.check_id not in expected_ids]
    assert not surprises, (
        f"{prefix}undeclared checker/model failures: "
        f"{format_findings(surprises)}.{suffix}"
    )

    allowed_blocked = set()
    for entry in expect_blocked:
        allowed_blocked.add(entry.check_id if isinstance(entry, Expected) else str(entry))
    unexpected_blocked = [
        check_id
        for check_id, result in report.results.items()
        if result == RESULT_BLOCKED and check_id not in allowed_blocked
    ]
    assert not unexpected_blocked, (
        f"{prefix}unexpected blocked checker IDs: {unexpected_blocked}. "
        "Pass expect_blocked=() with those IDs; na rows are not blocked.{suffix}"
    )

    if reset_truncated == FORBID:
        assert not truncated, (
            f"{prefix}unreviewed RESET-TRUNCATED findings: "
            f"{format_findings(truncated)}. Pass reset_truncated=REVIEW or "
            f"REQUIRE to dispose them explicitly.{suffix}"
        )
    elif reset_truncated == REQUIRE:
        assert truncated, (
            f"{prefix}expected at least one RESET-TRUNCATED finding from the "
            f"reset window, observed none.{suffix}"
        )
        if reset_truncated_count is not None:
            assert len(truncated) == reset_truncated_count, (
                f"{prefix}expected exactly {reset_truncated_count} RESET-TRUNCATED "
                f"finding(s), observed {len(truncated)}: "
                f"{format_findings(truncated)}.{suffix}"
            )
    elif reset_truncated == REVIEW and reset_truncated_allow is not None:
        allowed_trunc = set(reset_truncated_allow)
        surprise_trunc = [
            finding for finding in truncated if finding.check_id not in allowed_trunc
        ]
        assert not surprise_trunc, (
            f"{prefix}RESET-TRUNCATED IDs not on the REVIEW allowlist "
            f"{sorted(allowed_trunc)}: {format_findings(surprise_trunc)}.{suffix}"
        )

    return report


def _log_report(report: DisposeReport, log) -> None:
    """Print one disposition line per ID, then any reviewed reset findings."""
    for check_id, result in sorted(report.results.items()):
        count = report.counts.get(check_id, 0)
        reason = report.blocked_reasons.get(check_id, "")
        evidence = report.via.get(check_id, "")
        detail = ""
        if evidence:
            detail += f" via={evidence}"
        if result == RESULT_BLOCKED and reason:
            detail += f" reason={reason}"
        log.info(
            "DISPOSE test=%s id=%s result=%s count=%d%s",
            report.test,
            check_id,
            result,
            count,
            detail,
        )
    for finding in report.reset_truncated:
        log.info("DISPOSE test=%s reviewed %s", report.test, finding)


def format_findings(findings) -> str:
    """Render normalized findings as one diagnostic line."""
    return "; ".join(str(finding) for finding in findings)


def clear_sources(*sources) -> None:
    """Drop recorded findings from every source (fresh directed window)."""
    agents, monitors, _ = _expand(sources)
    for agent in agents:
        agent.violations.clear()
    for monitor in monitors:
        monitor.clear()


__all__ = [
    "DisposeReport",
    "Expected",
    "FORBID",
    "Finding",
    "REQUIRE",
    "REVIEW",
    "RESULT_BLOCKED",
    "RESULT_FAIL",
    "RESULT_NA",
    "RESULT_PASS",
    "clear_sources",
    "collect",
    "dispose_run",
    "expect",
    "format_findings",
]
