"""Output 3: the shape of one quality check, and of the report that holds them.

Severity convention, used by every check in `checks.py`:
  error    a number in the summary is missing or cannot be trusted
  warning  rows were changed, removed, or look wrong
  info     observed, nothing changed

A check that found nothing (`rows_affected == 0`) is always emitted, with severity `info`. So the report
has the same list of ids on every dataset, and filtering on warning/error shows only things that happened.

Nothing here reads a clock: the report is a graded file and must be byte-identical between two runs.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

SEVERITIES = ("info", "warning", "error")
ACTIONS = ("removed", "corrected", "excluded", "flagged", "none")
SUMMARY_MAX_CHARS = 300
LIST_MAX_ITEMS = 10  # a metric list is a sample for a human reader, not an export of the rows
METRIC_DECIMALS = 2

# The contract test rejects any key that looks like a wall-clock value, wherever it sits in the file.
CLOCK_KEY = re.compile(r"generated_at|timestamp|run_at|created_at", re.IGNORECASE)


@dataclass(frozen=True)
class Check:
    """One entry of the report. Invalid values raise here, where the check is built, not in the grader."""

    id: str
    severity: str
    rows_affected: int
    action: str
    summary: str
    metrics: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.id}: severity {self.severity!r} is not one of {SEVERITIES}")
        if self.action not in ACTIONS:
            raise ValueError(f"{self.id}: action {self.action!r} is not one of {ACTIONS}")
        # bool is a subclass of int in Python, so True would otherwise pass as a count of 1.
        if not isinstance(self.rows_affected, int) or isinstance(self.rows_affected, bool):
            raise ValueError(f"{self.id}: rows_affected must be an integer, got {self.rows_affected!r}")
        if self.rows_affected < 0:
            raise ValueError(f"{self.id}: rows_affected must not be negative, got {self.rows_affected}")
        if len(self.summary) > SUMMARY_MAX_CHARS:
            length = len(self.summary)
            raise ValueError(f"{self.id}: summary is {length} characters, limit {SUMMARY_MAX_CHARS}")
        _validate_metrics(self.id, self.metrics)


def make_check(
    id: str, severity: str, rows_affected: int, action: str, summary: str, metrics: dict | None = None
) -> Check:
    """Build a Check. `severity` is the level used when something was found; nothing found is `info`."""
    found = severity if rows_affected > 0 else "info"
    return Check(id, found, rows_affected, action, summary, metrics or {})


def build_report(as_of: date, checks: Iterable[Check]) -> dict:
    """The JSON payload: checks sorted by id, keys in a fixed order, so the same checks give the same text."""
    ordered = sorted(checks, key=lambda check: check.id)
    ids = [check.id for check in ordered]
    repeated = sorted({id for id in ids if ids.count(id) > 1})
    if repeated:
        raise ValueError(f"check ids must be unique; repeated: {', '.join(repeated)}")
    return {"as_of": as_of.isoformat(), "checks": [_as_dict(check) for check in ordered]}


def _as_dict(check: Check) -> dict:
    """Dicts keep insertion order and `write_json` does not re-sort, so this is the key order on disk."""
    return {
        "id": check.id,
        "severity": check.severity,
        "rows_affected": check.rows_affected,
        "action": check.action,
        "summary": check.summary,
        "metrics": _tidy(check.metrics),
    }


def _tidy(value: object) -> object:
    """Sort dict keys and round floats, at every depth. Lists keep the order their check gave them."""
    if isinstance(value, dict):
        return {key: _tidy(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_tidy(item) for item in value]
    if isinstance(value, float):
        return round(value, METRIC_DECIMALS)
    return value


def _validate_metrics(check_id: str, value: object) -> None:
    """Walk the metrics: no clock-like key names, and no list long enough to be a data dump."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{check_id}: metric key {key!r} is not text")
            if CLOCK_KEY.search(key):
                raise ValueError(f"{check_id}: metric key {key!r} looks like a wall-clock value")
            _validate_metrics(check_id, inner)
    elif isinstance(value, list):
        if len(value) > LIST_MAX_ITEMS:
            raise ValueError(f"{check_id}: a metric list has {len(value)} items, limit {LIST_MAX_ITEMS}")
        for inner in value:
            _validate_metrics(check_id, inner)
