"""Unit tests for the daily campaign summary (Output 1): the outer join and the derived columns."""

from __future__ import annotations

from datetime import date, datetime

import polars as pl

from astro_recon import spend
from astro_recon.summary import SUMMARY_COLUMNS, build_summary

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_REPORT = {
    "date": "2026-05-04",
    "network": "adglow",
    "campaign_id": "CAMP-007",
    "spend_amount": 10.0,
    "currency": "USD",
    "installs_reported": 2,
    "reported_ts": "2026-05-05T06:30:00Z",
}
# EUR has a rate on 2026-05-04 only, so an EUR report on any other day has a missing rate.
FX = pl.DataFrame({"date": [date(2026, 5, 4)], "currency": ["EUR"], "rate": [1.08]})


def make_mapped(*rows: tuple[str, str, str]) -> pl.DataFrame:
    """Mapped paid installs, one per (install_ts_utc, network, campaign_id): the columns the summary reads."""
    schema = {"install_ts_utc": pl.String, "network": pl.String, "campaign_id": pl.String}
    frame = pl.DataFrame(list(rows), schema=schema, orient="row")
    return frame.with_columns(install_ts=pl.col("install_ts_utc").str.to_datetime(TS_FORMAT, time_unit="us"))


def make_kept_spend(*rows: dict) -> pl.DataFrame:
    """Kept spend reports, built like the pipeline does: typed frame, then `spend.convert_fx`."""
    full_rows = [{**DEFAULT_REPORT, **row} for row in rows]
    schema = {"date": pl.String, "network": pl.String, "campaign_id": pl.String, "spend_amount": pl.Float64,
              "currency": pl.String, "installs_reported": pl.Int64, "reported_ts": pl.String}
    typed = pl.DataFrame(full_rows, schema=schema).with_columns(
        pl.col("date").str.to_date(),
        pl.col("reported_ts").str.to_datetime(TS_FORMAT, time_unit="us"),
    )
    return spend.convert_fx(typed, FX)


def only_row(frame: pl.DataFrame) -> dict:
    assert frame.height == 1
    return frame.to_dicts()[0]


def test_spend_only_campaign_day_has_zero_installs_and_keeps_the_spend():
    reports = make_kept_spend({"spend_amount": 1.1, "installs_reported": 1})
    row = only_row(build_summary(make_mapped(), reports))
    assert row["mmp_installs"] == 0  # zero, never null
    assert row["spend_usd"] == 1.1
    assert row["network_reported_installs"] == 1
    assert row["cpi_usd"] is None
    assert row["install_gap_pct"] is None


def test_install_only_campaign_day_has_nulls_not_zeros_in_the_spend_columns():
    mapped = make_mapped(
        ("2026-05-06T09:31:00Z", "googleuac", "CAMP-004"),
        ("2026-05-06T10:38:00Z", "googleuac", "CAMP-004"),
    )
    row = only_row(build_summary(mapped, make_kept_spend()))
    assert row["mmp_installs"] == 2
    for column in SUMMARY_COLUMNS[4:]:  # every column that comes from the spend report
        assert row[column] is None, column


def test_missing_fx_rate_blanks_the_money_but_not_the_install_gap():
    mapped = make_mapped(("2026-05-06T11:45:00Z", "adglow", "CAMP-007"))
    report = {"date": "2026-05-06", "currency": "EUR", "installs_reported": 3,
              "reported_ts": "2026-05-07T06:30:00Z"}
    row = only_row(build_summary(mapped, make_kept_spend(report)))
    assert row["spend_usd"] is None
    assert row["cpi_usd"] is None
    assert row["install_gap_pct"] == 200.0  # (3 - 1) / 1 * 100
    assert row["spend_reported_at"] == datetime(2026, 5, 7, 6, 30, 0)


def test_cpi_divides_the_two_decimal_spend():
    # 1.9 EUR * 1.08 = 2.052 -> 2.05 USD. cpi is 2.05 / 2 = 1.025, not 2.052 / 2 = 1.026.
    mapped = make_mapped(
        ("2026-05-04T14:42:00Z", "adglow", "CAMP-007"),
        ("2026-05-04T15:49:00Z", "adglow", "CAMP-007"),
    )
    row = only_row(build_summary(mapped, make_kept_spend({"spend_amount": 1.9, "currency": "EUR"})))
    assert row["spend_usd"] == 2.05
    assert row["cpi_usd"] == 1.025


def test_install_gap_is_a_percentage_of_mmp_installs_rounded_to_two_decimals():
    mapped = make_mapped(*[("2026-05-04T10:00:00Z", "adglow", "CAMP-007")] * 3)
    row = only_row(build_summary(mapped, make_kept_spend({"installs_reported": 4})))
    assert row["install_gap_pct"] == 33.33  # (4 - 3) / 3 * 100
    assert row["cpi_usd"] == 3.3333  # 10.0 / 3, four decimals


def test_zero_spend_is_a_real_value():
    mapped = make_mapped(("2026-05-04T10:00:00Z", "adglow", "CAMP-007"))
    row = only_row(build_summary(mapped, make_kept_spend({"spend_amount": 0.0})))
    assert row["spend_usd"] == 0.0
    assert row["cpi_usd"] == 0.0


def test_installs_either_side_of_midnight_utc_land_on_different_dates():
    mapped = make_mapped(
        ("2026-05-04T23:59:59Z", "adglow", "CAMP-007"),
        ("2026-05-05T00:00:00Z", "adglow", "CAMP-007"),
    )
    summary = build_summary(mapped, make_kept_spend())
    assert summary["date"].to_list() == [date(2026, 5, 4), date(2026, 5, 5)]
    assert summary["mmp_installs"].to_list() == [1, 1]


def test_networks_are_joined_as_reported_without_correction():
    # Installs say metaads (from campaigns.csv); the report for the same campaign id says tikboom.
    mapped = make_mapped(("2026-05-04T10:00:00Z", "metaads", "CAMP-001"))
    summary = build_summary(mapped, make_kept_spend({"network": "tikboom", "campaign_id": "CAMP-001"}))
    assert summary["network"].to_list() == ["metaads", "tikboom"]
    assert summary["mmp_installs"].to_list() == [1, 0]
    assert summary["spend_usd"].to_list() == [None, 10.0]


def test_output_has_exactly_the_summary_columns_sorted_by_key():
    mapped = make_mapped(
        ("2026-05-05T10:00:00Z", "tikboom", "CAMP-005"),
        ("2026-05-04T10:00:00Z", "metaads", "CAMP-002"),
        ("2026-05-04T10:00:00Z", "metaads", "CAMP-001"),
    )
    reports = make_kept_spend(
        {"date": "2026-05-05", "network": "adglow", "campaign_id": "CAMP-007"},
        {"date": "2026-05-04", "network": "adglow", "campaign_id": "CAMP-007"},
    )
    summary = build_summary(mapped, reports)
    assert summary.columns == SUMMARY_COLUMNS
    keys = [(row["date"], row["network"], row["campaign_id"]) for row in summary.to_dicts()]
    assert keys == [
        (date(2026, 5, 4), "adglow", "CAMP-007"),
        (date(2026, 5, 4), "metaads", "CAMP-001"),
        (date(2026, 5, 4), "metaads", "CAMP-002"),
        (date(2026, 5, 5), "adglow", "CAMP-007"),
        (date(2026, 5, 5), "tikboom", "CAMP-005"),
    ]


def test_column_types_are_stable_even_with_nulls():
    # One install-only row and one spend-only row: every nullable column holds a null somewhere.
    mapped = make_mapped(("2026-05-04T10:00:00Z", "metaads", "CAMP-001"))
    summary = build_summary(mapped, make_kept_spend({"installs_reported": None}))
    assert summary.schema["date"] == pl.Date
    assert summary.schema["mmp_installs"] == pl.Int64
    assert summary.schema["network_reported_installs"] == pl.Int64
    assert summary.schema["spend_reported_at"] == pl.Datetime("us")
    assert summary["mmp_installs"].null_count() == 0


def test_empty_inputs_give_an_empty_summary_with_the_right_columns():
    summary = build_summary(make_mapped(), make_kept_spend())
    assert summary.height == 0
    assert summary.columns == SUMMARY_COLUMNS
