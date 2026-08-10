"""Shared pending-item lifecycle policy for simulation participants."""

from __future__ import annotations

from dataclasses import dataclass


SEV_FAIL = "fail"
SEV_DIAGNOSTIC = "diagnostic"
SEV_IGNORE = "ignore"

REASON_DISPOSE = "dispose"
REASON_CLEAR = "window-clear"
REASON_STOP = "monitor-stop"
REASON_SCOPE = "scope-close"
REASON_RESET = "reset"


@dataclass
class _PendingItem:
    check_id: str
    severity: str
    detail: str
    scope: object
    opened_ns: float
    resolved: bool = False
    audited: bool = False


class PendingLedger:
    """Central pending-item ledger for end-of-window audit.

    ``record(check_id, detail, *, reset_truncated: bool)`` must append into the
    owner's ordinary finding path (events / violations). ``SEV_DIAGNOSTIC`` may
    log/record without failing semantics if owner has a diagnostic channel;
    otherwise record with a detail prefix ``incomplete-window `` and let dispose
    policy treat unknown IDs carefully - prefer owner-local diagnostic list if
    one exists. ``SEV_IGNORE`` never records.
    """

    def __init__(self, *, owner: str, record, in_reset, now_ns) -> None:
        self.owner = owner
        self._record = record
        self._in_reset = in_reset
        self._now_ns = now_ns
        self._items: list[_PendingItem] = []
        self._carryover: list = []

    def open(
        self,
        check_id: str,
        *,
        severity: str,
        detail: str,
        scope=None,
    ):
        """Return an opaque token. detail may be a format template; store as-is."""
        if severity not in (SEV_FAIL, SEV_DIAGNOSTIC, SEV_IGNORE):
            raise ValueError(f"{self.owner}: unknown pending severity {severity!r}")
        item = _PendingItem(
            check_id=check_id,
            severity=severity,
            detail=detail,
            scope=scope,
            opened_ns=float(self._now_ns()),
        )
        self._items.append(item)
        return item

    def resolve(self, token) -> None:
        """Mark a previously opened item as normally completed."""
        if isinstance(token, _PendingItem):
            token.resolved = True

    def close_scope(self, scope, *, reason: str) -> None:
        """Audit unresolved items with matching scope, then drop them from open set."""
        for item in self._items:
            if item.scope == scope:
                self._audit_item(item, reason=reason)
        self._items = [item for item in self._items if item.scope != scope]

    def audit(self, *, reason: str) -> None:
        """Audit all unresolved open items once (idempotent)."""
        for item in self._items:
            self._audit_item(item, reason=reason)

    def _audit_item(self, item: _PendingItem, *, reason: str) -> None:
        if item.resolved or item.audited:
            return
        item.audited = True
        if item.severity == SEV_IGNORE:
            return

        reset_truncated = bool(self._in_reset())
        detail = f"{item.detail} reason={reason}"
        if item.severity == SEV_DIAGNOSTIC:
            detail = f"incomplete-window {detail}"
        outcome = self._record(
            item.check_id, detail, reset_truncated=reset_truncated
        )
        if outcome is not None:
            self._carryover.append(outcome)

    @property
    def carryover(self) -> list:
        """Findings/events produced by audits that survive owner.clear()."""
        return self._carryover

    def clear(self) -> None:
        """Drop resolved/audited open items; KEEP carryover."""
        self._items = [
            item for item in self._items if not item.resolved and not item.audited
        ]


def finalize_all(participants, *, reason: str) -> None:
    """Finalize each participant's ledger and stop owned child tasks when needed."""
    for participant in participants:
        pending = getattr(participant, "pending", None)
        audit = getattr(pending, "audit", None)
        if callable(audit):
            audit(reason=reason)
        if reason == REASON_STOP:
            cancel_tasks = getattr(participant, "cancel_tasks", None)
            if callable(cancel_tasks):
                cancel_tasks()
