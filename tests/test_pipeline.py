"""End to end: tiny hand-made datasets through `pipeline.run` and through the command line.

FIXTURE covers the cases the second dataset promises (a missing FX rate, campaign values that map to
nothing, spend with no installs, users with two distinct installs) next to the ordinary ones. Every
expected number below was counted by hand from the rows, not copied from a run.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from astro_recon import pipeline

ROOT = Path(__file__).resolve().parents[1]
CMD = [sys.executable, "-m", "astro_recon"]
OUTPUT_FILES = ["daily_campaign_summary.csv", "weekly_retention.csv", "data_quality_report.json"]
SUMMARY_HEADER = (
    "date,network,campaign_id,mmp_installs,network_reported_installs,"
    "spend_usd,cpi_usd,install_gap_pct,spend_reported_at\n"
)
RETENTION_HEADER = "install_week,cohort_size,d7_retained_users,d7_retention_pct,is_mature\n"

REQUIRED_IDS = [
    "duplicate_install_rows", "install_reinstall_rows", "organic_installs_excluded",
    "campaign_id_normalised", "campaign_unmapped", "spend_restated_rows",
    "spend_fx_converted_rows", "fx_rate_missing", "spend_missing",
]

HEADERS = {
    "mmp_installs": [
        "install_id", "user_id", "install_ts_utc", "platform", "country", "media_source", "campaign",
    ],
    "mmp_events": ["user_id", "event_ts_utc", "event_type", "revenue_usd"],
    "network_spend": [
        "stat_date", "network", "campaign_id", "spend", "currency", "reported_installs", "reported_at",
    ],
    "campaigns": ["campaign_id", "campaign_name", "network"],
    "fx_rates": ["date", "currency", "usd_per_unit"],
}

# All installs fall in the ISO week of Monday 2026-05-04. The as-of tests use D = 2026-05-06.
FIXTURE = {
    "campaigns": [
        ["CAMP-001", "Astro US Broad", "metaads"],
        ["CAMP-002", "Miners UAC Global", "googleuac"],
        ["CAMP-003", "Galaxy EU", "adglow"],
    ],
    "mmp_installs": [
        ["i01", "u01", "2026-05-04T09:00:00Z", "ios", "US", "metaads", "CAMP-001"],
        ["i01", "u01", "2026-05-04T09:00:00Z", "ios", "US", "metaads", "CAMP-001"],  # exact duplicate
        ["i02", "u02", "2026-05-04T10:00:00Z", "android", "US", "metaads", "camp-001 "],  # trim, upper-case
        ["i03", "u03", "2026-05-04T11:00:00Z", "ios", "US", "metaads", "astro us broad"],  # campaign name
        ["i04", "u04", "2026-05-04T12:00:00Z", "ios", "DE", "organic", ""],
        ["i05", "u05", "2026-05-04T13:00:00Z", "ios", "DE", "organic", ""],  # earlier: organic
        ["i06", "u05", "2026-05-07T09:00:00Z", "ios", "DE", "metaads", "CAMP-001"],  # later: paid, after D
        ["i07", "u06", "2026-05-05T08:00:00Z", "ios", "US", "googleuac", "CAMP-002"],
        ["i08", "u06", "2026-05-05T20:00:00Z", "ios", "US", "googleuac", "CAMP-002"],  # reinstall, before D
        ["i09", "u07", "2026-05-05T09:00:00Z", "ios", "US", "googleuac", "CAMP-999"],  # maps to nothing
        ["i10", "u08", "2026-05-05T10:00:00Z", "ios", "US", "googleuac", "Summer Promo"],  # maps to nothing
        ["i11", "u09", "2026-05-05T11:00:00Z", "ios", "FR", "adglow", "CAMP-003"],
        ["i12", "u10", "2026-05-04T14:00:00Z", "ios", "FR", "adglow", "CAMP-003"],
        ["i13", "u11", "2026-05-06T09:00:00Z", "ios", "US", "metaads", "CAMP-001"],
        ["i14", "u12", "2026-05-06T10:00:00Z", "ios", "US", "googleuac", "CAMP-002"],
    ],
    "network_spend": [
        ["2026-05-04", "metaads", "CAMP-001", "6.00", "USD", "3", "2026-05-05T06:00:00Z"],
        ["2026-05-04", "metaads", "CAMP-001", "5.40", "USD", "4", "2026-05-07T06:00:00Z"],  # restated after D
        ["2026-05-04", "adglow", "CAMP-003", "2.00", "EUR", "1", "2026-05-05T06:00:00Z"],
        ["2026-05-05", "adglow", "CAMP-003", "3.00", "EUR", "1", "2026-05-06T06:00:00Z"],  # no rate that day
        ["2026-05-05", "googleuac", "CAMP-002", "4.00", "USD", "2", "2026-05-06T06:00:00Z"],
        ["2026-05-05", "metaads", "CAMP-001", "1.50", "USD", "1", "2026-05-06T06:00:00Z"],  # no installs
        ["2026-05-06", "metaads", "CAMP-001", "2.00", "USD", "1", "2026-05-07T06:00:00Z"],  # arrives after D
        # 2026-05-06 googleuac CAMP-002 has an install and never gets a report.
    ],
    "fx_rates": [["2026-05-04", "EUR", "1.10"], ["2026-05-06", "EUR", "1.10"]],
    "mmp_events": [
        ["u01", "2026-05-11T09:00:00Z", "session", ""],  # exactly install + 7 days: retained
        ["u02", "2026-05-11T10:30:00Z", "purchase", "4.99"],  # a purchase is not retention
        ["u05", "2026-05-11T13:30:00Z", "session", ""],  # day 7 of the earlier, organic install: retained
        ["u10", "2026-05-20T00:00:00Z", "session", ""],  # the latest event: the default as_of
    ],
}
CLEANED_INSTALLS = 12  # 15 rows - 1 exact duplicate - 2 reinstall rows (u05, u06)
D = date(2026, 5, 6)


def write_dataset(folder: Path, **tables: list[list[str]]) -> Path:
    """Write the five CSV files into `folder`. A table that is not given is written header-only."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, header in HEADERS.items():
        with open(folder / f"{name}.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(tables.get(name, []))
    return folder


def run(tmp_path: Path, as_of: date | None = None, **tables: list[list[str]]) -> Path:
    """Write a dataset, run the pipeline in this process, and return the output folder."""
    out = tmp_path / "out"
    pipeline.run(write_dataset(tmp_path / "data", **tables), out, as_of)
    return out


def checks(out: Path) -> dict[str, dict]:
    report = json.loads((out / "data_quality_report.json").read_text(encoding="utf-8"))
    return {check["id"]: check for check in report["checks"]}


def counts(out: Path) -> dict[str, int]:
    return {id: check["rows_affected"] for id, check in checks(out).items()}


def rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def summary(out: Path) -> dict[tuple[str, str, str], dict[str, str]]:
    table = rows(out / "daily_campaign_summary.csv")
    return {(row["date"], row["network"], row["campaign_id"]): row for row in table}


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(CMD + list(args), cwd=ROOT, capture_output=True, text=True, timeout=120)


# ------------------------------------------------------------------ default run on the fixture
def test_the_nine_required_counts(tmp_path):
    out = run(tmp_path, **FIXTURE)
    got = counts(out)
    assert {id: got[id] for id in REQUIRED_IDS} == {
        "duplicate_install_rows": 1,     # u01 twice
        "install_reinstall_rows": 2,     # the later rows of u05 and u06
        "organic_installs_excluded": 2,  # u04, and u05 whose earliest install is the organic one
        "campaign_id_normalised": 2,     # u02 by id, u03 by name
        "campaign_unmapped": 2,          # CAMP-999 and Summer Promo
        "spend_restated_rows": 1,
        "spend_fx_converted_rows": 1,    # adglow 2026-05-04
        "fx_rate_missing": 1,            # adglow 2026-05-05
        "spend_missing": 1,              # googleuac 2026-05-06
    }
    required = checks(out)
    assert json.loads((out / "data_quality_report.json").read_text())["as_of"] == "2026-05-20"
    assert required["duplicate_install_rows"]["metrics"] == {"pct_of_raw_rows": 6.67}  # 1 of 15
    assert required["install_reinstall_rows"]["metrics"] == {"users": 2, "users_changed_campaign": 1}
    assert required["campaign_id_normalised"]["metrics"] == {
        "matched_by_id_after_trim_or_case": 1, "matched_by_name": 1,
    }
    assert required["campaign_unmapped"]["metrics"]["raw_values"] == ["CAMP-999", "Summer Promo"]
    assert required["fx_rate_missing"]["metrics"] == {"keys": ["2026-05-05 EUR"]}
    assert required["spend_missing"]["severity"] == "error"
    assert required["spend_missing"]["metrics"] == {
        "by_network": {"googleuac": {"from": "2026-05-06", "rows": 1, "to": "2026-05-06"}},
        "installs_without_cost": 1, "overdue": 1, "pending": 0,
    }


def test_summary_rows_for_restated_missing_fx_spend_only_and_missing_spend(tmp_path):
    table = summary(run(tmp_path, **FIXTURE))
    assert len(table) == 7
    # The restated report is the one used, as a whole row: its spend, its installs, its timestamp.
    assert table[("2026-05-04", "metaads", "CAMP-001")] == {
        "date": "2026-05-04", "network": "metaads", "campaign_id": "CAMP-001", "mmp_installs": "3",
        "network_reported_installs": "4", "spend_usd": "5.40", "cpi_usd": "1.8000",
        "install_gap_pct": "33.33", "spend_reported_at": "2026-05-07T06:00:00Z",
    }
    assert table[("2026-05-04", "adglow", "CAMP-003")]["spend_usd"] == "2.20"  # 2.00 EUR x 1.10
    # No FX rate: the money columns are empty, everything that does not need the rate is still there.
    no_rate = table[("2026-05-05", "adglow", "CAMP-003")]
    assert (no_rate["spend_usd"], no_rate["cpi_usd"]) == ("", "")
    assert (no_rate["mmp_installs"], no_rate["network_reported_installs"]) == ("1", "1")
    assert (no_rate["install_gap_pct"], no_rate["spend_reported_at"]) == ("0.00", "2026-05-06T06:00:00Z")
    # Spend with no installs: a row with 0 installs and nothing to divide by.
    spend_only = table[("2026-05-05", "metaads", "CAMP-001")]
    assert (spend_only["mmp_installs"], spend_only["spend_usd"]) == ("0", "1.50")
    assert (spend_only["cpi_usd"], spend_only["install_gap_pct"]) == ("", "")
    # Installs with no report: missing, never zero.
    no_report = table[("2026-05-06", "googleuac", "CAMP-002")]
    assert no_report["mmp_installs"] == "1"
    assert [no_report[name] for name in ("network_reported_installs", "spend_usd", "cpi_usd")] == ["", "", ""]
    assert (no_report["install_gap_pct"], no_report["spend_reported_at"]) == ("", "")


def test_sql_cohorts_and_polars_cleaning_agree_on_the_number_of_cleaned_installs(tmp_path):
    """Rules 1-2 are written twice, in SQL for retention and in Polars for the summary. They must agree."""
    out = run(tmp_path, **FIXTURE)
    retention = rows(out / "weekly_retention.csv")
    assert retention == [
        {"install_week": "2026-05-04", "cohort_size": "12", "d7_retained_users": "2",
         "d7_retention_pct": "16.67", "is_mature": "true"},
    ]
    got = counts(out)
    mapped = sum(int(row["mmp_installs"]) for row in summary(out).values())
    polars_cleaned = got["organic_installs_excluded"] + got["campaign_unmapped"] + mapped
    assert polars_cleaned == CLEANED_INSTALLS
    assert sum(int(row["cohort_size"]) for row in retention) == polars_cleaned


# ------------------------------------------------------------------ --as-of
def test_as_of_does_not_see_the_restatement_and_uses_the_original_report(tmp_path):
    out = run(tmp_path, D, **FIXTURE)
    assert counts(out)["spend_restated_rows"] == 0
    row = summary(out)[("2026-05-04", "metaads", "CAMP-001")]
    assert (row["network_reported_installs"], row["spend_usd"], row["cpi_usd"]) == ("3", "6.00", "2.0000")
    assert (row["install_gap_pct"], row["spend_reported_at"]) == ("0.00", "2026-05-05T06:00:00Z")


def test_as_of_does_not_count_a_reinstall_that_happens_later(tmp_path):
    got = counts(run(tmp_path, D, **FIXTURE))
    assert got["install_reinstall_rows"] == 1  # u06 only: the second install of u05 is on 2026-05-07
    assert got["duplicate_install_rows"] == 1 and got["organic_installs_excluded"] == 2


def test_as_of_reports_the_newest_days_missing_spend_as_pending_not_overdue(tmp_path):
    out = run(tmp_path, D, **FIXTURE)
    missing = checks(out)["spend_missing"]
    assert missing["rows_affected"] == 2  # both campaigns with installs on D: no report can exist yet
    assert (missing["metrics"]["pending"], missing["metrics"]["overdue"]) == (2, 0)
    assert missing["severity"] == "warning"
    assert rows(out / "weekly_retention.csv") == [
        {"install_week": "2026-05-04", "cohort_size": "12", "d7_retained_users": "0",
         "d7_retention_pct": "0.00", "is_mature": "false"},
    ]
    assert checks(out)["immature_cohorts"]["metrics"] == {"weeks": ["2026-05-04"]}


def test_the_day_after_the_report_was_due_the_same_gap_is_overdue(tmp_path):
    missing = checks(run(tmp_path, date(2026, 5, 7), **FIXTURE))["spend_missing"]
    assert (missing["rows_affected"], missing["severity"]) == (1, "error")
    assert (missing["metrics"]["pending"], missing["metrics"]["overdue"]) == (0, 1)


# ------------------------------------------------------------------ report shape, with and without data
@pytest.mark.parametrize(
    ("tables", "as_of"),
    [(FIXTURE, None), (FIXTURE, D), ({}, D)],
    ids=["fixture", "fixture as-of", "header-only files"],
)
def test_report_always_has_the_same_valid_shape(tmp_path, tables, as_of):
    out = run(tmp_path, as_of, **tables)
    report = json.loads((out / "data_quality_report.json").read_text(encoding="utf-8"))
    ids = [check["id"] for check in report["checks"]]
    assert set(REQUIRED_IDS) <= set(ids)
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    assert ids == sorted(checks(run(tmp_path / "other", date(2026, 5, 20), **FIXTURE)))  # same ids every time
    for check in report["checks"]:
        assert len(check["summary"]) <= 300, check["id"]
        assert list(check) == ["id", "severity", "rows_affected", "action", "summary", "metrics"]
        if check["rows_affected"] == 0:
            assert check["severity"] == "info", check["id"]


def test_header_only_files_give_header_only_outputs_and_every_check_at_zero(tmp_path):
    out = run(tmp_path, D)
    assert (out / "daily_campaign_summary.csv").read_text(encoding="utf-8") == SUMMARY_HEADER
    assert (out / "weekly_retention.csv").read_text(encoding="utf-8") == RETENTION_HEADER
    assert set(counts(out).values()) == {0}


def test_as_of_before_any_data_is_a_valid_empty_report(tmp_path):
    out = run(tmp_path, date(2026, 1, 1), **FIXTURE)
    assert (out / "daily_campaign_summary.csv").read_text(encoding="utf-8") == SUMMARY_HEADER
    assert (out / "weekly_retention.csv").read_text(encoding="utf-8") == RETENTION_HEADER
    assert set(counts(out).values()) == {0}


# ------------------------------------------------------------------ the extra checks
def test_extra_checks_count_what_they_say_on_a_dirty_dataset(tmp_path):
    campaigns = [
        ["CAMP-001", "Astro US Broad", "metaads"],
        ["CAMP-002", "Miners Global", "googleuac"],
        ["CAMP-003", "Twin Name", "adglow"],
        ["CAMP-004", "TWIN NAME", "adglow"],  # the same name as CAMP-003 once case is ignored
        ["CAMP-005", "No Network", ""],  # unusable reference row
    ]
    installs = [
        ["a1", "u1", "2026-05-04T09:00:00Z", "ios", "US", "metaads", "CAMP-001"],
        ["a2", "u2", "2026-05-04T10:00:00Z", "ios", "US", "Organic", "CAMP-001"],  # not the exact word
        ["a3", "u3", "2026-05-04T11:00:00Z", "ios", "US", "googleuac", "CAMP-001"],  # CAMP-001 is metaads
        ["a4", "u4", "2026-05-04T12:00:00Z", "ios", "US", "adglow", "Twin Name"],  # ambiguous name
        ["a5", "u5", "2026-05-04T13:00:00Z", "ios", "US", "metaads", "CAMP-001"],  # two rows,
        ["a6", "u5", "2026-05-04T13:00:00Z", "ios", "US", "googleuac", "CAMP-002"],  # one timestamp
        ["a1", "u6", "2026-05-04T14:00:00Z", "ios", "US", "metaads", "CAMP-001"],  # install_id of u1
        ["a7", "u7", "not-a-time", "ios", "US", "metaads", "CAMP-001"],
    ]
    spend = [
        ["2026-05-04", "metaads", "CAMP-001", "10.00", "USD", "5", "2026-05-05T06:00:00Z"],
        ["2026-05-04", "metaads", "CAMP-001", "10.00", "USD", "5", "2026-05-05T06:00:00Z"],  # delivered twice
        ["2026-05-05", "metaads", "CAMP-001", "-2.00", "USD", "0", "2026-05-06T06:00:00Z"],  # negative
        ["2026-05-06", "metaads", "CAMP-001", "0", "EUR", "0", "2026-05-07T06:00:00Z"],  # zero, new currency
        ["2026-05-04", "metaads", "CAMP-777", "3.00", "USD", "1", "2026-05-05T06:00:00Z"],  # unknown id
        ["2026-05-04", "googleuac", "CAMP-003", "", "USD", "1", "2026-05-05T06:00:00Z"],  # adglow's campaign
        ["2026-05-07", "metaads", "CAMP-001", "abc", "USD", "1", "2026-05-08T06:00:00Z"],
    ]
    fx = [["2026-05-06", "EUR", "1.10"], ["2026-05-07", "EUR", "0"], ["2026-05-08", "EUR", "abc"]]
    events = [
        ["u1", "2026-05-04T08:30:00Z", "session", ""],  # 30 minutes before the install
        ["u1", "2026-05-11T09:00:00Z", "session", ""],
        ["u1", "2026-05-11T09:00:00Z", "session", ""],  # exact copy
        ["u1", "2026-05-12T10:00:00Z", "purchase", "2500.00"],  # implausible
        ["u1", "2026-05-12T11:00:00Z", "purchase", "-4.99"],
        ["u2", "2026-05-12T12:00:00Z", "purchase", "-0.02"],
        ["ghost", "2026-05-12T13:00:00Z", "purchase", "9.99"],  # no install for this user
        ["u2", "2026-05-12T14:00:00Z", "Session", ""],  # not the exact word
        ["u3", "yesterday", "session", ""],
        ["u3", "2026-05-20T00:00:00Z", "session", ""],
    ]
    tables = {
        "mmp_installs": installs, "mmp_events": events, "network_spend": spend,
        "campaigns": campaigns, "fx_rates": fx,
    }
    out = run(tmp_path, **tables)
    got, report = counts(out), checks(out)
    expected = {
        "unparseable_rows": 4,               # one each in installs, events, spend and campaigns
        "fx_rate_invalid": 2,
        "reinstall_same_timestamp": 1,       # u5
        "install_reinstall_rows": 1,
        "install_id_reused": 2,              # both rows that carry a1
        "media_source_unrecognised": 1,      # Organic
        "media_source_network_mismatch": 2,  # u2 and u3
        "campaign_reference_ambiguous": 2,
        "campaign_unmapped": 1,              # u4: the ambiguous name is not guessed
        "duplicate_spend_rows": 1,
        "spend_restated_rows": 0,            # a copy is not a restatement
        "spend_currency_changed": 1,
        "spend_without_installs": 4,
        "spend_non_positive": 3,             # one empty, one zero, one negative
        "spend_unknown_campaign": 2,
        "events_without_install": 1,
        "events_before_install": 1,
        "duplicate_event_rows": 1,
        "event_type_unrecognised": 1,
        "implausible_purchase_amount": 1,
        "negative_revenue_events": 2,
    }
    assert {id: got[id] for id in expected} == expected
    assert report["unparseable_rows"]["metrics"] == {
        "campaigns": 1, "mmp_events": 1, "mmp_installs": 1, "network_spend": 1,
    }
    assert report["media_source_unrecognised"]["metrics"] == {"values": ["Organic"]}
    assert report["spend_currency_changed"]["metrics"] == {
        "CAMP-001": {"first_date": "2026-05-06", "from": "USD", "to": "EUR"},
    }
    assert report["spend_non_positive"]["metrics"] == {"empty": 1, "negative": 1, "zero": 1}
    assert report["events_without_install"]["metrics"] == {
        "revenue_usd": 9.99, "users": ["ghost"], "users_count": 1,
    }
    assert report["events_before_install"]["metrics"] == {
        "earliest_offset_minutes": -30.0, "users_affected": 1,
    }
    assert report["negative_revenue_events"]["metrics"] == {"total_usd": -5.01}
    assert report["implausible_purchase_amount"]["metrics"] == {"max_revenue_usd": 2500.0, "users": ["u1"]}
    # u5's two rows tie on the timestamp; the lower install_id (a5, CAMP-001) wins, so metaads has 5 installs.
    assert summary(out)[("2026-05-04", "metaads", "CAMP-001")]["mmp_installs"] == "5"


def network_days(network: str, campaign_id: str, reported: list[int]) -> tuple[list, list]:
    """Ten installs a day for one campaign from 2026-05-04, and a report per day with `reported` installs."""
    installs, spend = [], []
    for day, count in enumerate(reported, start=4):
        stat_date, next_day = f"2026-05-{day:02d}", f"2026-05-{day + 1:02d}"
        for n in range(10):
            user = f"{network}-{day}-{n}"
            installs.append([f"i-{user}", user, f"{stat_date}T10:00:00Z", "ios", "US", network, campaign_id])
        spend.append([stat_date, network, campaign_id, "10.00", "USD", str(count), f"{next_day}T06:00:00Z"])
    return installs, spend


def test_day_bucketing_flags_only_the_network_whose_daily_gap_swings(tmp_path):
    """All three networks report 10% more installs in total; only `shifted` moves them between days."""
    campaigns = [["CAMP-001", "A", "steady"], ["CAMP-002", "B", "calm"], ["CAMP-003", "C", "shifted"]]
    installs, spend = [], []
    plans = [("steady", "CAMP-001", [11, 11, 11, 11]), ("calm", "CAMP-002", [12, 10, 12, 10]),
             ("shifted", "CAMP-003", [16, 6, 16, 6])]
    for network, campaign_id, reported in plans:
        more_installs, more_spend = network_days(network, campaign_id, reported)
        installs += more_installs
        spend += more_spend
    out = run(tmp_path, D.replace(day=20), mmp_installs=installs, network_spend=spend, campaigns=campaigns)
    bucketing = checks(out)["network_day_bucketing_suspected"]
    assert bucketing["metrics"]["flagged_networks"] == ["shifted"]
    assert (bucketing["rows_affected"], bucketing["severity"]) == (4, "warning")  # its four summary rows
    assert "NOT shifted" in bucketing["summary"]
    assert bucketing["metrics"]["per_network"] == {
        "calm": {"daily_gap_std": 11.55, "total_gap_pct": 10.0},
        "shifted": {"daily_gap_std": 57.74, "total_gap_pct": 10.0},
        "steady": {"daily_gap_std": 0.0, "total_gap_pct": 10.0},
    }
    # The summary itself is untouched: the days are reported as the network sent them.
    assert summary(out)[("2026-05-05", "shifted", "CAMP-003")]["install_gap_pct"] == "-40.00"
    over = checks(out)["network_overreports_installs"]
    assert over["rows_affected"] == 8  # steady 4, calm 2, shifted 2
    assert over["metrics"]["total_gap_pct"] == {"calm": 10.0, "shifted": 10.0, "steady": 10.0}


@pytest.mark.parametrize(("in_hour_23", "flagged"), [(31, 31), (30, 0)], ids=["above 1.5x", "exactly 1.5x"])
def test_install_pile_up_in_the_last_utc_hour(tmp_path, in_hour_23, flagged):
    installs = [
        [f"i{hour}-{n}", f"u{hour}-{n}", f"2026-05-04T{hour}:{n:02d}:00Z", "ios", "US", "organic", ""]
        for hour, count in ((22, 20), (23, in_hour_23))
        for n in range(count)
    ]
    pileup = checks(run(tmp_path, D, mmp_installs=installs))["install_hour_pileup"]
    assert pileup["rows_affected"] == flagged
    assert pileup["metrics"] == {"hour_22": 20, "hour_23": in_hour_23}
    assert pileup["severity"] == "info"  # low confidence: a question for the MMP, never a warning


# ------------------------------------------------------------------ determinism and bytes
def test_two_command_line_runs_are_byte_identical_and_use_only_line_feeds(tmp_path):
    data = write_dataset(tmp_path / "data", **FIXTURE)
    first, second = tmp_path / "first", tmp_path / "second"
    for out in (first, second):
        result = cli("--data", str(data), "--out", str(out))
        assert result.returncode == 0, result.stderr
    for name in OUTPUT_FILES:
        content = (first / name).read_bytes()
        assert hashlib.sha256(content).digest() == hashlib.sha256((second / name).read_bytes()).digest(), name
        assert b"\r" not in content and content.endswith(b"\n"), name
    assert sorted(path.name for path in first.iterdir()) == sorted(OUTPUT_FILES)  # no temporary file left


# ------------------------------------------------------------------ command line errors
def test_cli_names_the_missing_file_and_writes_nothing(tmp_path):
    data = write_dataset(tmp_path / "data", **FIXTURE)
    (data / "fx_rates.csv").unlink()
    out = tmp_path / "out"
    result = cli("--data", str(data), "--out", str(out))
    assert result.returncode != 0
    assert "fx_rates.csv" in result.stderr and "Traceback" not in result.stderr
    assert not out.exists()


def test_cli_names_the_file_and_the_missing_column(tmp_path):
    data = write_dataset(tmp_path / "data", **FIXTURE)
    without_column = "stat_date,network,campaign_id,spend,currency,reported_installs\n"
    (data / "network_spend.csv").write_bytes(without_column.encode("utf-8"))
    result = cli("--data", str(data), "--out", str(tmp_path / "out"))
    assert result.returncode != 0
    assert "network_spend.csv" in result.stderr and "reported_at" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_creates_a_nested_output_folder(tmp_path):
    data = write_dataset(tmp_path / "data", **FIXTURE)
    out = tmp_path / "does" / "not" / "exist" / "yet"
    result = cli("--data", str(data), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in out.iterdir()) == sorted(OUTPUT_FILES)


def test_cli_overwrites_the_three_files_and_leaves_other_files_alone(tmp_path):
    data = write_dataset(tmp_path / "data", **FIXTURE)
    out = tmp_path / "out"
    out.mkdir()
    (out / "daily_campaign_summary.csv").write_text("stale\n")
    (out / "NOTES.txt").write_text("mine\n")
    assert cli("--data", str(data), "--out", str(out)).returncode == 0
    assert (out / "daily_campaign_summary.csv").read_text(encoding="utf-8").startswith(SUMMARY_HEADER)
    assert (out / "NOTES.txt").read_text() == "mine\n"


def test_cli_with_header_only_files_exits_zero_when_as_of_is_given(tmp_path):
    data = write_dataset(tmp_path / "data")
    out = tmp_path / "out"
    result = cli("--data", str(data), "--out", str(out), "--as-of", "2026-05-06")
    assert result.returncode == 0, result.stderr
    assert (out / "daily_campaign_summary.csv").read_text(encoding="utf-8") == SUMMARY_HEADER
    assert json.loads((out / "data_quality_report.json").read_text())["as_of"] == "2026-05-06"


def test_cli_with_no_events_and_no_as_of_asks_for_the_flag(tmp_path):
    """The default as_of is the date of the latest event. With no events there is no honest default."""
    data = write_dataset(tmp_path / "data")
    result = cli("--data", str(data), "--out", str(tmp_path / "out"))
    assert result.returncode != 0
    assert "--as-of" in result.stderr and "Traceback" not in result.stderr


def test_cli_rejects_the_last_calendar_date_with_a_message_not_a_traceback(tmp_path):
    data = write_dataset(tmp_path / "data", **FIXTURE)
    result = cli("--data", str(data), "--out", str(tmp_path / "out"), "--as-of", "9999-12-31")
    assert result.returncode != 0
    assert "too late" in result.stderr and "Traceback" not in result.stderr
