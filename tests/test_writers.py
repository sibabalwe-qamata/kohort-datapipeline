"""Output formatting: the exact text of every kind of value, and the exact bytes on disk."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

import polars as pl
import pytest

from astro_recon import writers
from astro_recon.retention import RETENTION_COLUMNS
from astro_recon.summary import SUMMARY_COLUMNS
from astro_recon.writers import retention_rows, summary_rows, write_csv, write_json


def summary_frame(**overrides) -> pl.DataFrame:
    """One fully populated summary row; a test overrides the column it is about."""
    row = {
        "date": date(2026, 5, 1),
        "network": "metaads",
        "campaign_id": "CAMP-001",
        "mmp_installs": 2,
        "network_reported_installs": 3,
        "spend_usd": 2.05,
        "cpi_usd": 1.025,
        "install_gap_pct": 50.0,
        "spend_reported_at": datetime(2026, 5, 2, 6, 10, 0),
    }
    row.update(overrides)
    floats = {name: pl.Float64 for name in ("spend_usd", "cpi_usd", "install_gap_pct")}
    return pl.DataFrame([row], schema_overrides=floats)


def summary_cell(column: str, value) -> str:
    return summary_rows(summary_frame(**{column: value}))[0][SUMMARY_COLUMNS.index(column)]


# ------------------------------------------------------------------ column lists
def test_the_writer_keeps_no_column_list_of_its_own():
    """The header comes from summary.py and retention.py; a second copy here could drift from them."""
    assert not hasattr(writers, "SUMMARY_COLUMNS") and not hasattr(writers, "RETENTION_COLUMNS")
    assert len(summary_rows(summary_frame())[0]) == len(SUMMARY_COLUMNS)


# ------------------------------------------------------------------ summary_rows
def test_summary_row_is_in_header_order_with_fixed_formats():
    assert summary_rows(summary_frame()) == [
        ["2026-05-01", "metaads", "CAMP-001", "2", "3", "2.05", "1.0250", "50.00", "2026-05-02T06:10:00Z"]
    ]


def test_summary_columns_are_found_by_name_not_by_position():
    shuffled = summary_frame().select(list(reversed(SUMMARY_COLUMNS)))
    assert summary_rows(shuffled) == summary_rows(summary_frame())


def test_nulls_become_empty_strings():
    frame = pl.DataFrame(
        {
            "date": [date(2026, 5, 1)], "network": ["metaads"], "campaign_id": ["CAMP-001"],
            "mmp_installs": [4], "network_reported_installs": [None],
            "spend_usd": [None], "cpi_usd": [None], "install_gap_pct": [None], "spend_reported_at": [None],
        },
        schema_overrides={
            "network_reported_installs": pl.Int64, "spend_usd": pl.Float64, "cpi_usd": pl.Float64,
            "install_gap_pct": pl.Float64, "spend_reported_at": pl.Datetime("us"),
        },
    )
    assert summary_rows(frame) == [["2026-05-01", "metaads", "CAMP-001", "4", "", "", "", "", ""]]


def test_integer_column_that_arrives_as_float_is_not_written_with_a_decimal_point():
    """A nullable integer turns into a float in many joins; `2.0` must still be written `2`."""
    frame = summary_frame().with_columns(
        pl.col("mmp_installs").cast(pl.Float64), pl.col("network_reported_installs").cast(pl.Float64)
    )
    row = summary_rows(frame)[0]
    assert (row[3], row[4]) == ("2", "3")


def test_a_count_with_a_fraction_is_an_error_not_a_silent_truncation():
    with pytest.raises(ValueError, match="whole number"):
        summary_rows(summary_frame(mmp_installs=2.5))


@pytest.mark.parametrize(
    ("column", "value", "text"),
    [
        ("spend_usd", 1234.5, "1234.50"),      # no thousands separator, two decimals
        ("spend_usd", 0.0, "0.00"),
        ("spend_usd", -0.0, "0.00"),
        ("spend_usd", -0.004, "0.00"),         # rounds to zero: still no minus sign
        ("spend_usd", -3.5, "-3.50"),          # a real negative keeps its sign
        ("cpi_usd", 0.86, "0.8600"),
        ("cpi_usd", 1e-7, "0.0000"),           # never scientific notation
        ("install_gap_pct", -100.0, "-100.00"),
        ("install_gap_pct", 9.126, "9.13"),
    ],
)
def test_decimals_are_fixed(column, value, text):
    assert summary_cell(column, value) == text


def test_nan_reaching_the_writer_is_an_error():
    with pytest.raises(ValueError, match="finite"):
        summary_rows(summary_frame(cpi_usd=float("nan")))


def test_spend_reported_at_drops_fractions_and_ends_in_z():
    with_fraction = datetime(2026, 7, 3, 15, 21, 0, 250000)
    assert summary_cell("spend_reported_at", with_fraction) == "2026-07-03T15:21:00Z"


def test_rows_keep_the_order_they_were_given():
    frame = pl.concat([summary_frame(campaign_id="CAMP-002"), summary_frame(campaign_id="CAMP-001")])
    assert [row[2] for row in summary_rows(frame)] == ["CAMP-002", "CAMP-001"]


# ------------------------------------------------------------------ retention_rows
def test_retention_rows_format_and_lower_case_booleans():
    frame = pl.DataFrame(
        {
            "install_week": [date(2026, 4, 27), date(2026, 5, 4)],
            "cohort_size": [3, 7],
            "d7_retained_users": [0, 2],
            "d7_retention_pct": [0.0, 200 / 7],
            "is_mature": [True, False],
        }
    )
    assert retention_rows(frame) == [
        ["2026-04-27", "3", "0", "0.00", "true"],
        ["2026-05-04", "7", "2", "28.57", "false"],
    ]
    assert len(retention_rows(frame)[0]) == len(RETENTION_COLUMNS)


# ------------------------------------------------------------------ write_csv
def test_csv_bytes_are_utf8_without_bom_and_use_only_line_feeds(tmp_path):
    path = tmp_path / "out.csv"
    write_csv(path, ["name", "n"], [["Zoë", "1"], ["", "2"]])
    assert path.read_bytes() == "name,n\nZoë,1\n,2\n".encode("utf-8")


def test_csv_value_with_a_comma_or_quote_is_quoted_and_reads_back(tmp_path):
    path = tmp_path / "out.csv"
    rows = [["Astro US, Broad", "1"], ['the "big" one', "2"]]
    write_csv(path, ["name", "n"], rows)
    assert b'"Astro US, Broad",1\n' in path.read_bytes()
    assert list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))[1:] == rows


def test_summary_round_trip_puts_every_value_under_its_own_header(tmp_path):
    path = tmp_path / "daily_campaign_summary.csv"
    write_csv(path, SUMMARY_COLUMNS, summary_rows(summary_frame()))
    with open(path, newline="", encoding="utf-8") as handle:
        (row,) = list(csv.DictReader(handle))
    assert row == {
        "date": "2026-05-01", "network": "metaads", "campaign_id": "CAMP-001", "mmp_installs": "2",
        "network_reported_installs": "3", "spend_usd": "2.05", "cpi_usd": "1.0250",
        "install_gap_pct": "50.00", "spend_reported_at": "2026-05-02T06:10:00Z",
    }


def test_writing_replaces_an_existing_file_and_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "out.csv"
    path.write_text("old contents that are longer than the new ones\n")
    write_csv(path, ["a"], [["1"]])
    assert path.read_bytes() == b"a\n1\n"
    assert [p.name for p in tmp_path.iterdir()] == ["out.csv"]


# ------------------------------------------------------------------ write_json
def test_json_is_byte_identical_across_calls_keeps_key_order_and_ends_with_a_newline(tmp_path):
    check = {"id": "b", "rows_affected": 2, "metrics": {"z": 1.5, "a": [1, 2]}}
    payload = {"as_of": "2026-05-30", "checks": [check]}
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_json(first, payload)
    write_json(second, payload)
    data = first.read_bytes()
    assert data == second.read_bytes()
    assert data.endswith(b"}\n") and b"\r" not in data and not data.startswith(b"\xef\xbb\xbf")
    assert data.index(b'"as_of"') < data.index(b'"checks"') and data.index(b'"z"') < data.index(b'"a"')


def test_json_refuses_nan_instead_of_writing_invalid_json(tmp_path):
    with pytest.raises(ValueError):
        write_json(tmp_path / "report.json", {"metrics": {"ratio": float("nan")}})
    assert not (tmp_path / "report.json").exists()
