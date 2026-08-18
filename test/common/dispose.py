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
    MODEL_DISPOSE_VIA,
    MODEL_PIN_CHECK_IDS,
    QspiPinMonitor,
    SharedBusMonitor,
)
from monitors.timing import CeTimingMonitor

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_NA = "na"
RESULT_BLOCKED = "blocked"

# RESET-TRUNCATED policy for one dispose call.
FORBID = "forbid"  # any truncated finding is an unreviewed surprise -> fail
REVIEW = "review"  # log each; zero is acceptable
REQUIRE = "require"  # log each; at least one must exist (reset was exercised)

_RESET_POLICIES = (FORBID, REVIEW, REQUIRE)


@dataclass(frozen=True)
class Expected:
    """One declared negative: a catalog/model ID and its allowed count."""

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
    expected: "tuple[Expected, ...]" = ()
    sources: "tuple[str, ...]" = ()

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
        findings.append(
            Finding(
                check_id=check_id,
                source=getattr(event, "source", source),
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


def collect(*sources) -> "tuple[list[Finding], dict[str, str], dict[str, int], dict[str, str], dict[str, str], tuple[str, ...]]":
    """Return ``(findings, results, counts, blocked_reasons, via, source_names)``.

    Results start from each monitor's own per-ID disposition so structurally
    unavailable (``na``) and unjudgeable (``blocked``) rows survive into the
    report even when nothing was recorded against them.

    ``CHK-PIN-KNOWN`` prefers a live
    :class:`QspiPinMonitor` (``via=pin``). Model ``Q-SIO-X``
    records are the fallback when no usable pin monitor is present (absent or
    ``blocked``), matching :func:`monitors.qspi.dispose_pin_checks`.
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

    # Pin-decoded rows win when a usable pin monitor ran. Counts for those IDs
    # come from the pin findings themselves in dispose_run. Otherwise derive the
    # catalog IDs from model Q-* twins so the rows are never silently skipped.
    if usable_pin:
        for check_id in MODEL_PIN_CHECK_IDS:
            via[check_id] = "pin"
            # Re-assert pin disposition after any earlier monitor.results() pass;
            # blocked pin monitors are not in usable_pin, so they cannot win here.
            hit = sum(monitor.counts()[check_id] for monitor in usable_pin)
            results[check_id] = RESULT_FAIL if hit else RESULT_PASS
    elif agents:
        codes = [
            finding.check_id for finding in findings if not finding.reset_truncated
        ]
        for check_id in MODEL_PIN_CHECK_IDS:
            model_id = MODEL_DISPOSE_VIA[check_id]
            hit = sum(1 for code in codes if code == model_id)
            via[check_id] = model_id
            results[check_id] = RESULT_FAIL if hit else RESULT_PASS
            counts[check_id] = hit
            # A blocked pin monitor may have stamped these rows earlier; model
            # fallback is the disposition that actually judged them.
            blocked_reasons.pop(check_id, None)

    return findings, results, counts, blocked_reasons, via, tuple(names)


def dispose_run(
    *sources,
    test: str,
    log=None,
    expect_fail=(),
    reset_truncated: str = FORBID,
    repro: str = "",
    quiet: bool = False,
) -> DisposeReport:
    """Dispose every recorded finding from *sources* and assert the outcome.

    *sources* may be :class:`common.bringup.BringUp` bundles, PSRAM devices or
    agents, or any started monitor (including :class:`QspiPinMonitor`).
    ``CHK-PIN-KNOWN`` prefers a live pin monitor
    (``via=pin`` in the disposition log) and falls back to model ``Q-SIO-X``
    when the pin monitor is absent or blocked. Ordinary findings must be empty
    unless declared through *expect_fail* (a string ID, or :func:`expect` with
    a count). ``RESET-TRUNCATED`` findings follow *reset_truncated*:
    :data:`FORBID` (default), :data:`REVIEW`, or :data:`REQUIRE`.

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
    findings, results, counts, blocked_reasons, via, names = collect(*sources)

    ordinary = [finding for finding in findings if not finding.reset_truncated]
    truncated = [finding for finding in findings if finding.reset_truncated]

    for finding in ordinary:
        counts[finding.check_id] = counts.get(finding.check_id, 0) + 1
        results[finding.check_id] = RESULT_FAIL
    for check_id in results:
        counts.setdefault(check_id, 0)

    report = DisposeReport(
        test=test,
        results=results,
        counts=counts,
        blocked_reasons=blocked_reasons,
        via=via,
        ordinary=ordinary,
        reset_truncated=truncated,
        expected=expected,
        sources=names,
    )

    if log is not None and not quiet:
        _log_report(report, log)

    suffix = f" {repro}" if repro else ""
    prefix = f"{test}: " if test else ""

    expected_ids = {entry.check_id for entry in expected}
    for entry in expected:
        observed = counts.get(entry.check_id, 0)
        assert entry.matches(observed), (
            f"{prefix}expected {entry}, observed {observed} occurrence(s). "
            f"Recorded: {format_findings(ordinary) or '<none>'}.{suffix}"
        )

    surprises = [finding for finding in ordinary if finding.check_id not in expected_ids]
    assert not surprises, (
        f"{prefix}undeclared checker/model failures: "
        f"{format_findings(surprises)}.{suffix}"
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
