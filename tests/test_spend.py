"""Unit tests for the spend rules: latest report per key, then FX conversion."""

from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from astro_recon import spend

DEFAULT_ROW = {
    "date": "2026-05-04",
    "network": "adglow",
    "campaign_id": "CAMP-007",
    "spend_amount": 10.0,
    "currency": "USD",
    "installs_reported": 5,
    "reported_ts": "2026-05-05T06:30:00Z",
}
FIRST = {"spend_amount": 10.0, "reported_ts": "2026-05-05T06:30:00Z"}
RESTATED = {"spend_amount": 9.0, "reported_ts": "2026-05-07T09:00:00Z"}


def make_spend(*rows: dict) -> pl.DataFrame:
    """Spend frame as the loader delivers it. Each row only names the fields the test cares about."""
    full_rows = [{**DEFAULT_ROW, **row} for row in rows]
    schema = {"date": pl.String, "network": pl.String, "campaign_id": pl.String, "spend_amount": pl.Float64,
              "currency": pl.String, "installs_reported": pl.Int64, "reported_ts": pl.String}
    return pl.DataFrame(full_rows, schema=schema).with_columns(
        pl.col("date").str.to_date(),
        pl.col("reported_ts").str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_unit="us"),
    )


def make_fx(*rows: tuple[str, str, float]) -> pl.DataFrame:
    """FX frame: (date, currency, rate)."""
    schema = {"date": pl.String, "currency": pl.String, "rate": pl.Float64}
    return pl.DataFrame(list(rows), schema=schema, orient="row").with_columns(pl.col("date").str.to_date())


# --- latest report per (date, network, campaign_id) ------------------------------------------------


@pytest.mark.parametrize("rows", [(FIRST, RESTATED), (RESTATED, FIRST)])
def test_latest_report_wins_whatever_the_row_order(rows):
    kept, restated = spend.latest_reports(make_spend(*rows))
    assert restated == 1
    assert kept["spend_amount"].to_list() == [9.0]
    assert kept["reported_ts"].to_list() == [datetime(2026, 5, 7, 9, 0, 0)]


def test_the_whole_latest_row_is_taken_not_a_mix_of_columns():
    first = {**FIRST, "currency": "USD", "installs_reported": 5}
    latest = {**RESTATED, "currency": "EUR", "installs_reported": 7}
    kept, _ = spend.latest_reports(make_spend(first, latest))
    assert kept.to_dicts() == make_spend(latest).to_dicts()


def test_restated_count_is_rows_minus_keys():
    third = {"spend_amount": 8.0, "reported_ts": "2026-05-09T09:00:00Z"}
    other_day = {"date": "2026-05-05", "reported_ts": "2026-05-06T06:30:00Z"}
    kept, restated = spend.latest_reports(make_spend(FIRST, RESTATED, third, other_day))
    assert restated == 2
    assert kept.height == 2


def test_network_is_part_of_the_key():
    # Same day and campaign id under two networks are two reports, not a restatement.
    kept, restated = spend.latest_reports(make_spend({"network": "adglow"}, {"network": "tikboom"}))
    assert restated == 0
    assert kept.height == 2


def test_identical_reports_collapse_and_are_not_counted_as_restated():
    deduped, removed = spend.drop_duplicate_reports(make_spend(FIRST, FIRST, RESTATED))
    assert removed == 1
    kept, restated = spend.latest_reports(deduped)
    assert restated == 1
    assert kept["spend_amount"].to_list() == [9.0]


# --- FX conversion ---------------------------------------------------------------------------------


def test_eur_with_a_rate_is_converted_and_rounded_to_two_decimals():
    converted = spend.convert_fx(
        make_spend({"spend_amount": 1.9, "currency": "EUR"}), make_fx(("2026-05-04", "EUR", 1.08))
    )
    assert converted["rate"].to_list() == [1.08]
    assert converted["spend_usd"].to_list() == [2.05]  # 1.9 * 1.08 = 2.052


def test_eur_without_a_rate_for_its_date_keeps_the_row_with_null_spend_usd():
    # A rate exists the day before and the day after: it must not be carried over.
    fx = make_fx(("2026-05-03", "EUR", 1.08), ("2026-05-05", "EUR", 1.08), ("2026-05-04", "GBP", 1.25))
    converted = spend.convert_fx(make_spend({"spend_amount": 1.9, "currency": "EUR"}), fx)
    assert converted.height == 1
    assert converted["spend_usd"].to_list() == [None]
    assert converted["rate"].to_list() == [None]
    assert converted["installs_reported"].to_list() == [5]  # the rest of the report is still there


def test_usd_needs_no_fx_row():
    converted = spend.convert_fx(make_spend({"spend_amount": 5.55, "currency": "USD"}), make_fx())
    assert converted["spend_usd"].to_list() == [5.55]


def test_usd_ignores_a_usd_rate_that_is_not_one():
    converted = spend.convert_fx(
        make_spend({"spend_amount": 5.55, "currency": "USD"}), make_fx(("2026-05-04", "USD", 1.5))
    )
    assert converted["spend_usd"].to_list() == [5.55]


def test_null_spend_stays_null():
    rows = make_spend(
        {"campaign_id": "CAMP-001", "spend_amount": None, "currency": "USD"},
        {"campaign_id": "CAMP-007", "spend_amount": None, "currency": "EUR"},
    )
    converted = spend.convert_fx(rows, make_fx(("2026-05-04", "EUR", 1.08)))
    assert converted["spend_usd"].to_list() == [None, None]


def test_fx_join_never_changes_the_row_count():
    rows = make_spend(
        {"campaign_id": "CAMP-001", "currency": "USD"},
        {"campaign_id": "CAMP-007", "currency": "EUR"},
        {"campaign_id": "CAMP-008", "currency": "EUR"},
        {"campaign_id": "CAMP-009", "currency": "GBP"},
    )
    fx = make_fx(("2026-05-04", "EUR", 1.08), ("2026-05-05", "EUR", 1.09), ("2026-05-04", "USD", 1.0))
    converted = spend.convert_fx(rows, fx)
    assert converted.height == rows.height
    assert converted["campaign_id"].to_list() == ["CAMP-001", "CAMP-007", "CAMP-008", "CAMP-009"]


def test_two_rates_for_one_day_fail_instead_of_duplicating_spend():
    # The loader guarantees one rate per (date, currency). If that ever breaks, a plain join would return
    # the spend row twice and the money would be counted twice, so the function refuses to continue.
    fx = make_fx(("2026-05-04", "EUR", 1.08), ("2026-05-04", "EUR", 1.09))
    with pytest.raises(pl.exceptions.ComputeError):
        spend.convert_fx(make_spend({"currency": "EUR"}), fx)


def test_fx_converted_and_missing_counts():
    rows = make_spend(
        {"campaign_id": "CAMP-001", "currency": "USD"},
        {"campaign_id": "CAMP-007", "currency": "EUR", "date": "2026-05-04"},
        {"campaign_id": "CAMP-007", "currency": "EUR", "date": "2026-05-06"},  # no rate that day
    )
    converted = spend.convert_fx(rows, make_fx(("2026-05-04", "EUR", 1.08), ("2026-05-04", "USD", 1.0)))
    assert converted.filter(spend.is_fx_converted()).height == 1
    assert converted.filter(spend.is_fx_missing()).height == 1
    assert converted.filter(spend.is_fx_missing())["date"].to_list() == [date(2026, 5, 6)]


def test_missing_rate_on_a_superseded_report_is_not_counted():
    # The first report came in GBP (no rate); the restatement is in EUR (rate exists).
    first = {"currency": "GBP", "reported_ts": "2026-05-05T06:30:00Z"}
    latest = {"currency": "EUR", "reported_ts": "2026-05-07T09:00:00Z"}
    kept, _ = spend.latest_reports(make_spend(first, latest))
    converted = spend.convert_fx(kept, make_fx(("2026-05-04", "EUR", 1.08)))
    assert converted.filter(spend.is_fx_missing()).height == 0
    assert converted.filter(spend.is_fx_converted()).height == 1
