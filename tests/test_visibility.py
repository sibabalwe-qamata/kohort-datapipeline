"""`--as-of`: parsing the argument, the default date, and the visibility boundary per source."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import polars as pl
import pytest

from astro_recon.io import InputError, Inputs
from astro_recon.visibility import cutoff, default_as_of, filter_visible, parse_as_of

AS_OF = date(2026, 5, 10)
LAST_VISIBLE = datetime(2026, 5, 10, 23, 59, 59)
FIRST_HIDDEN = datetime(2026, 5, 11, 0, 0, 0)


def make_inputs(
    install_ts: Sequence[datetime] = (),
    event_ts: Sequence[datetime] = (),
    spend: Sequence[tuple[date, datetime]] = (),
) -> Inputs:
    """Frames holding only the columns the filter reads; each row is labelled by its position."""
    return Inputs(
        installs=pl.DataFrame(
            {"user_id": [f"u{i}" for i in range(len(install_ts))], "install_ts": install_ts},
            schema={"user_id": pl.String, "install_ts": pl.Datetime("us")},
        ),
        events=pl.DataFrame(
            {"user_id": [f"u{i}" for i in range(len(event_ts))], "event_ts": event_ts},
            schema={"user_id": pl.String, "event_ts": pl.Datetime("us")},
        ),
        spend=pl.DataFrame(
            {"date": [d for d, _ in spend], "reported_ts": [ts for _, ts in spend]},
            schema={"date": pl.Date, "reported_ts": pl.Datetime("us")},
        ),
        campaigns=pl.DataFrame({"campaign_id": ["CAMP-001"], "campaign_name": ["A"], "network": ["metaads"]}),
        fx=pl.DataFrame({"date": [date(2026, 6, 1)], "currency": ["EUR"], "rate": [1.08]}),
        rejected_rows={"mmp_installs": 2},
    )


# ------------------------------------------------------------------ parse_as_of
def test_parse_as_of_accepts_an_iso_date():
    assert parse_as_of("2026-05-10") == date(2026, 5, 10)


@pytest.mark.parametrize(
    "text",
    ["yesterday", "2026-13-01", "2026-02-30", "20260510", "2026-5-1", "2026-05-10 ", "2026-05-10T00:00Z", ""],
)
def test_parse_as_of_rejects_anything_else(text):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_as_of(text)


def test_parse_as_of_rejects_the_last_date_because_its_cutoff_cannot_be_computed():
    """9999-12-31 is a real date, but `cutoff` needs the day after it: a message, not an OverflowError."""
    with pytest.raises(ValueError, match="too late"):
        parse_as_of("9999-12-31")
    assert cutoff(parse_as_of("9999-12-30")) == datetime(9999, 12, 31, 0, 0, 0)


# ------------------------------------------------------------------ default_as_of
def test_default_as_of_is_the_date_of_the_latest_event_wherever_it_sits_in_the_file():
    events = make_inputs(
        event_ts=[datetime(2026, 5, 3, 9, 0), datetime(2026, 7, 20, 0, 0, 0), datetime(2026, 7, 19, 23, 59)]
    ).events
    assert default_as_of(events) == date(2026, 7, 20)


def test_default_as_of_with_no_events_asks_for_the_flag():
    with pytest.raises(InputError, match="--as-of"):
        default_as_of(make_inputs().events)


# ------------------------------------------------------------------ filter_visible
def test_cutoff_is_midnight_at_the_start_of_the_next_day():
    assert cutoff(AS_OF) == FIRST_HIDDEN


def test_install_boundary():
    visible = filter_visible(make_inputs(install_ts=[LAST_VISIBLE, FIRST_HIDDEN]), AS_OF)
    assert visible.installs["install_ts"].to_list() == [LAST_VISIBLE]


def test_event_boundary():
    visible = filter_visible(make_inputs(event_ts=[FIRST_HIDDEN, LAST_VISIBLE]), AS_OF)
    assert visible.events["event_ts"].to_list() == [LAST_VISIBLE]


def test_spend_boundary_is_on_reported_ts():
    stat_date = date(2026, 5, 9)
    visible = filter_visible(make_inputs(spend=[(stat_date, LAST_VISIBLE), (stat_date, FIRST_HIDDEN)]), AS_OF)
    assert visible.spend["reported_ts"].to_list() == [LAST_VISIBLE]


def test_spend_is_filtered_by_arrival_time_not_by_stat_date():
    old_day_late_report = (date(2026, 5, 1), datetime(2026, 5, 12, 6, 0, 0))   # e.g. a restatement
    same_day_early_report = (AS_OF, datetime(2026, 5, 10, 22, 0, 0))
    future_day_early_report = (date(2026, 5, 20), datetime(2026, 5, 10, 8, 0, 0))
    inputs = make_inputs(spend=[old_day_late_report, same_day_early_report, future_day_early_report])
    assert filter_visible(inputs, AS_OF).spend["date"].to_list() == [AS_OF, date(2026, 5, 20)]


def test_each_source_is_filtered_on_its_own_timestamp():
    """A hidden row in one source must not hide rows in another."""
    inputs = make_inputs(install_ts=[LAST_VISIBLE], event_ts=[FIRST_HIDDEN], spend=[(AS_OF, LAST_VISIBLE)])
    visible = filter_visible(inputs, AS_OF)
    assert (visible.installs.height, visible.events.height, visible.spend.height) == (1, 0, 1)


def test_reference_files_and_reject_counts_are_untouched_and_the_original_is_not_changed():
    inputs = make_inputs(install_ts=[LAST_VISIBLE, FIRST_HIDDEN])
    visible = filter_visible(inputs, date(2026, 1, 1))   # before every row, and before the fx date
    assert visible.installs.height == 0
    assert visible.campaigns.equals(inputs.campaigns) and visible.fx.equals(inputs.fx)
    assert visible.rejected_rows == {"mmp_installs": 2}
    assert inputs.installs.height == 2
