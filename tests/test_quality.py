"""The report contract: what a Check refuses, and what makes two reports the same text."""
from __future__ import annotations

import json
from datetime import date

import pytest

from astro_recon.quality import SUMMARY_MAX_CHARS, Check, build_report, make_check

AS_OF = date(2026, 5, 30)


def check(**overrides) -> Check:
    """One valid check; a test overrides the field it is about."""
    fields = {
        "id": "duplicate_install_rows", "severity": "warning", "rows_affected": 2, "action": "removed",
        "summary": "2 rows removed.", "metrics": {"pct_of_raw_rows": 5.0},
    }
    fields.update(overrides)
    return Check(**fields)


# ------------------------------------------------------------------ Check validation
def test_a_valid_check_is_accepted_and_metrics_default_to_empty():
    assert check().rows_affected == 2
    assert Check("x", "info", 0, "none", "nothing found").metrics == {}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("severity", "critical", "severity"),
        ("severity", "Warning", "severity"),           # the grader compares the exact lower-case words
        ("action", "deleted", "action"),
        ("rows_affected", -1, "negative"),
        ("rows_affected", 2.0, "integer"),             # the grader checks isinstance(..., int)
        ("rows_affected", True, "integer"),            # bool is an int in Python; it is not a count
        ("summary", "x" * (SUMMARY_MAX_CHARS + 1), "301 characters"),
    ],
)
def test_check_rejects_values_the_grader_would_reject(field, value, message):
    with pytest.raises(ValueError, match=message):
        check(**{field: value})


def test_summary_of_exactly_the_limit_is_accepted():
    assert len(check(summary="x" * SUMMARY_MAX_CHARS).summary) == 300


@pytest.mark.parametrize(
    "metrics",
    [
        {"last_event_timestamp": "2026-07-20"},
        {"Generated_At": 1},                                       # the grader's pattern ignores case
        {"per_network": {"tikboom": {"created_at": "x"}}},         # nested in a dict
        {"rows": [{"run_at": "x"}]},                               # nested in a list
    ],
)
def test_check_rejects_a_clock_like_metric_key_at_any_depth(metrics):
    with pytest.raises(ValueError, match="wall-clock"):
        check(metrics=metrics)


def test_check_rejects_a_metric_list_long_enough_to_be_a_data_dump():
    assert check(metrics={"users": [f"u{i}" for i in range(10)]})
    with pytest.raises(ValueError, match="11 items"):
        check(metrics={"users": [f"u{i}" for i in range(11)]})


def test_a_check_that_found_nothing_is_always_info():
    """So a reader who filters on warning/error only sees things that happened."""
    assert make_check("x", "error", 0, "flagged", "0 rows").severity == "info"
    assert make_check("x", "error", 1, "flagged", "1 row").severity == "error"
    assert make_check("x", "info", 5, "none", "5 rows").severity == "info"


# ------------------------------------------------------------------ build_report
def test_report_holds_the_as_of_date_and_the_checks_sorted_by_id():
    report = build_report(AS_OF, [check(id="spend_missing"), check(id="campaign_unmapped"), check(id="b")])
    assert report["as_of"] == "2026-05-30"
    assert [entry["id"] for entry in report["checks"]] == ["b", "campaign_unmapped", "spend_missing"]


def test_report_rejects_a_repeated_id():
    with pytest.raises(ValueError, match="spend_missing"):
        build_report(AS_OF, [check(id="spend_missing"), check(id="a"), check(id="spend_missing")])


def test_check_keys_are_written_in_the_documented_order():
    (entry,) = build_report(AS_OF, [check()])["checks"]
    assert list(entry) == ["id", "severity", "rows_affected", "action", "summary", "metrics"]


def test_metric_keys_are_sorted_at_every_depth_and_lists_keep_their_order():
    metrics = {"zeta": 1, "alpha": {"to": "b", "from": "a"}, "values": ["most frequent", "a later one"]}
    (entry,) = build_report(AS_OF, [check(metrics=metrics)])["checks"]
    assert list(entry["metrics"]) == ["alpha", "values", "zeta"]
    assert list(entry["metrics"]["alpha"]) == ["from", "to"]
    assert entry["metrics"]["values"] == ["most frequent", "a later one"]


def test_metric_floats_are_rounded_to_two_decimals_and_integers_stay_integers():
    metrics = {"pct": 1.4768, "nested": {"std": 17.7649}, "count": 3}
    (entry,) = build_report(AS_OF, [check(metrics=metrics)])["checks"]
    assert entry["metrics"] == {"count": 3, "nested": {"std": 17.76}, "pct": 1.48}
    assert isinstance(entry["metrics"]["count"], int)


def test_two_builds_give_identical_json_text_whatever_order_the_inputs_came_in():
    first = [check(id="b", metrics={"x": 1, "a": [2, 1]}), check(id="a", metrics={"k": {"z": 1.2, "y": 2}})]
    second = [check(id="a", metrics={"k": {"y": 2, "z": 1.2}}), check(id="b", metrics={"a": [2, 1], "x": 1})]
    assert json.dumps(build_report(AS_OF, first)) == json.dumps(build_report(AS_OF, second))
