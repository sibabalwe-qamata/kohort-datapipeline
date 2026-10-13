"""Tests for Output 2, weekly D7 session retention (astro_recon/retention.py + the .sql file).

Each test builds the smallest frames that exercise one rule, so it fails when that rule is broken.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from astro_recon import retention
from astro_recon.retention import RETENTION_COLUMNS, weekly_retention

# 2026-05-04 is a Monday. Late enough that every fixture below is visible unless a test says otherwise.
MONDAY = "2026-05-04"
LATE_AS_OF = date(2026, 6, 30)

INSTALL_SCHEMA = {
    "install_id": pl.String,
    "user_id": pl.String,
    "install_ts_utc": pl.String,
    "platform": pl.String,
    "country": pl.String,
    "media_source": pl.String,
    "campaign": pl.String,
    "install_ts": pl.Datetime("us"),
}
EVENT_SCHEMA = {
    "user_id": pl.String,
    "event_ts": pl.Datetime("us"),
    "event_type": pl.String,
    "revenue": pl.Float64,
}


# ---------------------------------------------------------------- frame builders


def ts(text: str) -> datetime:
    """'2026-05-04T09:00:00' -> naive datetime (UTC wall clock)."""
    return datetime.fromisoformat(text)


def make_installs(*rows: tuple[str, str] | tuple[str, str, str]) -> pl.DataFrame:
    """Typed installs frame from (user_id, install_ts[, media_source]) tuples.

    install_id is derived from the user and timestamp, so repeating a tuple gives an exact duplicate row.
    """
    records = []
    for row in rows:
        user_id, install_ts = row[0], row[1]
        media_source = row[2] if len(row) == 3 else "metaads"
        records.append(
            {
                "install_id": "i_" + user_id + "_" + install_ts,
                "user_id": user_id,
                "install_ts_utc": install_ts + "Z",
                "platform": "android",
                "country": "US",
                "media_source": media_source,
                "campaign": "CAMP-001",
                "install_ts": ts(install_ts),
            }
        )
    return pl.DataFrame(records, schema=INSTALL_SCHEMA)


def make_events(*rows: tuple[str, str | datetime, str]) -> pl.DataFrame:
    """Typed events frame from (user_id, event_ts, event_type) tuples."""
    records = []
    for user_id, event_ts, event_type in rows:
        records.append(
            {
                "user_id": user_id,
                "event_ts": ts(event_ts) if isinstance(event_ts, str) else event_ts,
                "event_type": event_type,
                "revenue": 1.99 if event_type == "purchase" else None,
            }
        )
    return pl.DataFrame(records, schema=EVENT_SCHEMA)


def only_row(frame: pl.DataFrame) -> dict:
    """The single result row as a dict; fails if there is not exactly one week."""
    assert frame.height == 1, frame
    return frame.row(0, named=True)


# ---------------------------------------------------------------- output contract


def test_output_columns_dtypes_and_sort_order():
    installs = make_installs(("u2", "2026-05-12T10:00:00"), ("u1", "2026-05-04T10:00:00"))
    out = weekly_retention(installs, make_events(), LATE_AS_OF)

    assert out.columns == RETENTION_COLUMNS
    assert out.schema["install_week"] == pl.Date
    assert out.schema["cohort_size"].is_integer()
    assert out.schema["d7_retained_users"].is_integer()
    assert out.schema["d7_retention_pct"] == pl.Float64
    assert out.schema["is_mature"] == pl.Boolean
    assert out["install_week"].to_list() == [date(2026, 5, 4), date(2026, 5, 11)]


def test_empty_installs_gives_empty_frame_with_contract_columns():
    out = weekly_retention(make_installs(), make_events(("ghost", "2026-05-11T10:00:00", "session")), LATE_AS_OF)

    assert out.height == 0
    assert out.columns == RETENTION_COLUMNS
    assert out.schema["install_week"] == pl.Date
    assert out.schema["cohort_size"].is_integer()
    assert out.schema["d7_retained_users"].is_integer()
    assert out.schema["d7_retention_pct"] == pl.Float64
    assert out.schema["is_mature"] == pl.Boolean


def test_week_with_no_retained_users_shows_zero_not_null():
    out = weekly_retention(make_installs(("u1", "2026-05-04T10:00:00")), make_events(), LATE_AS_OF)

    row = only_row(out)
    assert row["cohort_size"] == 1
    assert row["d7_retained_users"] == 0
    assert row["d7_retention_pct"] == 0.0
    assert out.null_count().row(0) == (0, 0, 0, 0, 0)


def test_retention_pct_is_rounded_to_two_decimals():
    installs = make_installs(
        ("u1", "2026-05-04T10:00:00"), ("u2", "2026-05-04T10:00:00"), ("u3", "2026-05-04T10:00:00")
    )
    one_retained = make_events(("u1", "2026-05-11T12:00:00", "session"))
    two_retained = make_events(("u1", "2026-05-11T12:00:00", "session"), ("u2", "2026-05-11T12:00:00", "session"))

    assert only_row(weekly_retention(installs, one_retained, LATE_AS_OF))["d7_retention_pct"] == 33.33
    assert only_row(weekly_retention(installs, two_retained, LATE_AS_OF))["d7_retention_pct"] == 66.67


# ---------------------------------------------------------------- D7 window


@pytest.mark.parametrize(
    ("offset", "expected_retained"),
    [
        (timedelta(days=7) - timedelta(seconds=1), 0),  # one second too early
        (timedelta(days=7), 1),  # lower bound is inclusive
        (timedelta(days=8) - timedelta(seconds=1), 1),  # last second of day 7
        (timedelta(days=8), 0),  # upper bound is exclusive
    ],
)
def test_d7_window_is_half_open(offset: timedelta, expected_retained: int):
    install_ts = ts("2026-05-04T09:07:00")
    installs = make_installs(("u1", "2026-05-04T09:07:00"))
    events = make_events(("u1", install_ts + offset, "session"))

    assert only_row(weekly_retention(installs, events, LATE_AS_OF))["d7_retained_users"] == expected_retained


@pytest.mark.parametrize(("event_type", "expected_retained"), [("session", 1), ("purchase", 0)])
def test_only_sessions_retain_never_purchases(event_type: str, expected_retained: int):
    installs = make_installs(("u1", "2026-05-04T10:00:00"))
    events = make_events(("u1", "2026-05-11T12:00:00", event_type))

    assert only_row(weekly_retention(installs, events, LATE_AS_OF))["d7_retained_users"] == expected_retained


def test_several_sessions_in_the_window_count_the_user_once():
    installs = make_installs(("u1", "2026-05-04T10:00:00"))
    events = make_events(("u1", "2026-05-11T12:00:00", "session"), ("u1", "2026-05-11T13:00:00", "session"))

    row = only_row(weekly_retention(installs, events, LATE_AS_OF))
    assert (row["cohort_size"], row["d7_retained_users"], row["d7_retention_pct"]) == (1, 1, 100.0)


# ---------------------------------------------------------------- week boundary and time zones

# Sunday 23:30 UTC and Monday 00:30 UTC: one hour apart, two different ISO weeks.
BOUNDARY_INSTALLS = (("u_sunday", "2026-05-03T23:30:00"), ("u_monday", "2026-05-04T00:30:00"))
BOUNDARY_WEEKS = [date(2026, 4, 27), date(2026, 5, 4)]


def connection_in_zone(zone: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("SET TimeZone = '" + zone + "'")
    return con


def test_week_boundary_default_connection():
    out = weekly_retention(make_installs(*BOUNDARY_INSTALLS), make_events(), LATE_AS_OF)

    assert out["install_week"].to_list() == BOUNDARY_WEEKS
    assert out["cohort_size"].to_list() == [1, 1]


# Tokyo is ahead of UTC (would push Sunday 23:30 into Monday); Los Angeles is behind (would pull
# Monday 00:30 back into Sunday). Either way the answer must not move.
@pytest.mark.parametrize("zone", ["Asia/Tokyo", "America/Los_Angeles"])
def test_week_boundary_on_a_connection_set_to_another_zone(zone: str):
    con = connection_in_zone(zone)
    try:
        out = weekly_retention(make_installs(*BOUNDARY_INSTALLS), make_events(), LATE_AS_OF, con=con)
    finally:
        con.close()

    assert out["install_week"].to_list() == BOUNDARY_WEEKS
    assert out["cohort_size"].to_list() == [1, 1]


@pytest.mark.parametrize("zone", ["Asia/Tokyo", "America/Los_Angeles"])
def test_week_boundary_even_if_timestamps_arrive_zone_aware(zone: str):
    """Second line of defence: zone-aware input is only right because the function sets UTC itself."""
    installs = make_installs(*BOUNDARY_INSTALLS).with_columns(pl.col("install_ts").dt.replace_time_zone("UTC"))
    events = make_events().with_columns(pl.col("event_ts").dt.replace_time_zone("UTC"))
    con = connection_in_zone(zone)
    try:
        out = weekly_retention(installs, events, LATE_AS_OF, con=con)
    finally:
        con.close()

    assert out["install_week"].to_list() == BOUNDARY_WEEKS


# ---------------------------------------------------------------- cleaning rules 1 and 2


def test_exact_duplicate_install_rows_count_the_user_once():
    installs = make_installs(("u1", "2026-05-04T10:00:00"), ("u1", "2026-05-04T10:00:00"))
    assert installs.is_duplicated().all()  # the fixture really is two identical rows

    assert only_row(weekly_retention(installs, make_events(), LATE_AS_OF))["cohort_size"] == 1


# The later install is listed first on purpose: "earliest" must come from the timestamp, not row order.
REINSTALLS = (("u1", "2026-05-12T10:00:00"), ("u1", "2026-05-04T10:00:00"))


def test_reinstalled_user_is_one_cohort_member_anchored_on_earliest_install():
    # 2026-05-11T12:00 is day 7 of the 05-04 install and not of the 05-12 install.
    events = make_events(("u1", "2026-05-11T12:00:00", "session"))

    row = only_row(weekly_retention(make_installs(*REINSTALLS), events, LATE_AS_OF))
    assert row["install_week"] == date(2026, 5, 4)
    assert row["cohort_size"] == 1
    assert row["d7_retained_users"] == 1


def test_session_on_day_7_of_the_later_install_does_not_count():
    # 2026-05-19T12:00 is day 7 of the 05-12 install only (day 15 of the 05-04 install).
    events = make_events(("u1", "2026-05-19T12:00:00", "session"))

    row = only_row(weekly_retention(make_installs(*REINSTALLS), events, LATE_AS_OF))
    assert row["install_week"] == date(2026, 5, 4)
    assert row["cohort_size"] == 1
    assert row["d7_retained_users"] == 0


def test_organic_users_are_in_the_cohort():
    installs = make_installs(("u_paid", "2026-05-04T10:00:00"), ("u_organic", "2026-05-04T11:00:00", "organic"))
    events = make_events(("u_organic", "2026-05-11T12:00:00", "session"))

    row = only_row(weekly_retention(installs, events, LATE_AS_OF))
    assert row["cohort_size"] == 2
    assert row["d7_retained_users"] == 1


# ---------------------------------------------------------------- events that must never match


def test_events_of_a_user_with_no_install_are_ignored():
    installs = make_installs(("u1", "2026-05-04T10:00:00"))
    events = make_events(("u_ghost", "2026-05-11T12:00:00", "session"))

    row = only_row(weekly_retention(installs, events, LATE_AS_OF))
    assert row["cohort_size"] == 1
    assert row["d7_retained_users"] == 0


def test_events_before_install_are_ignored():
    installs = make_installs(("u1", "2026-05-12T10:00:00"))
    # Exactly 7 days BEFORE install, and one hour before install: neither is day 7.
    events = make_events(("u1", "2026-05-05T10:00:00", "session"), ("u1", "2026-05-12T09:00:00", "session"))

    row = only_row(weekly_retention(installs, events, LATE_AS_OF))
    assert row["cohort_size"] == 1
    assert row["d7_retained_users"] == 0


# ---------------------------------------------------------------- as-of


def test_install_visibility_cuts_at_the_end_of_the_as_of_day():
    installs = make_installs(("u_in", "2026-05-10T23:59:59"), ("u_out", "2026-05-11T00:00:00"))

    at_cutoff = weekly_retention(installs, make_events(), date(2026, 5, 10))
    assert at_cutoff["install_week"].to_list() == [date(2026, 5, 4)]  # the 05-11 week does not exist yet
    assert at_cutoff["cohort_size"].to_list() == [1]

    next_day = weekly_retention(installs, make_events(), date(2026, 5, 11))
    assert next_day["install_week"].to_list() == [date(2026, 5, 4), date(2026, 5, 11)]


def test_install_after_cutoff_does_not_replace_or_join_the_cohort():
    # Same user: only the reinstall is after the cut-off, so the user is still one member of week 05-04.
    installs = make_installs(("u1", "2026-05-04T10:00:00"), ("u1", "2026-05-11T00:00:00"))

    row = only_row(weekly_retention(installs, make_events(), date(2026, 5, 10)))
    assert (row["install_week"], row["cohort_size"]) == (date(2026, 5, 4), 1)


def test_d7_session_after_the_cutoff_does_not_count_yet():
    # Install at Monday 00:00:00, so the D7 window opens at exactly 2026-05-11T00:00:00,
    # which is the first instant NOT visible at as_of 2026-05-10.
    installs = make_installs(("u1", "2026-05-04T00:00:00"))
    events = make_events(("u1", "2026-05-11T00:00:00", "session"))

    assert only_row(weekly_retention(installs, events, date(2026, 5, 10)))["d7_retained_users"] == 0
    assert only_row(weekly_retention(installs, events, date(2026, 5, 11)))["d7_retained_users"] == 1


def test_session_in_the_last_second_of_the_as_of_day_counts():
    installs = make_installs(("u1", "2026-05-04T00:00:00"))
    events = make_events(("u1", "2026-05-11T23:59:59", "session"))

    assert only_row(weekly_retention(installs, events, date(2026, 5, 11)))["d7_retained_users"] == 1


def test_is_mature_flips_exactly_at_install_week_plus_13_days():
    installs = make_installs(("u1", "2026-05-06T10:00:00"))  # a Wednesday in the week of Monday 05-04

    day_before = only_row(weekly_retention(installs, make_events(), date(2026, 5, 16)))
    on_the_day = only_row(weekly_retention(installs, make_events(), date(2026, 5, 17)))

    assert day_before["install_week"] == date(2026, 5, 4)
    assert day_before["is_mature"] is False
    assert on_the_day["is_mature"] is True


# ---------------------------------------------------------------- how the query is built


def test_as_of_is_a_bound_parameter_not_formatted_into_the_sql():
    sql = retention.load_retention_sql()
    assert "$as_of" in sql

    module_source = Path(retention.__file__).read_text(encoding="utf-8")
    # An f-string prefix is a standalone f (or rf / fr) right before a quote; "as_of" must not match.
    assert re.search(r"\b[rR]?[fF][rR]?[\"']", module_source) is None
    for forbidden in (".format(", "% as_of", "+ str(as_of", ".replace("):
        assert forbidden not in module_source, forbidden
    assert '{"as_of": as_of}' in module_source
