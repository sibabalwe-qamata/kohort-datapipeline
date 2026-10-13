"""Write the graded files so that the bytes are the same on every run and every platform.

Each value is turned into text here by a fixed rule and written with the standard library, so the
output does not depend on how a dataframe library prints a float, a null or a line ending.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path

import polars as pl

def write_csv(path: Path, columns: list[str], rows: Iterable[Sequence[str]]) -> None:
    """UTF-8, no BOM, a header row, and "\\n" line endings on every platform."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")  # the csv module's default is "\r\n"
    writer.writerow(columns)
    writer.writerows(rows)
    _replace_file(path, buffer.getvalue().encode("utf-8"))


def write_json(path: Path, payload: dict) -> None:
    """Keys stay in the order the caller built them. allow_nan=False because NaN is not valid JSON."""
    text = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    _replace_file(path, text.encode("utf-8"))


def summary_rows(df: pl.DataFrame) -> list[list[str]]:
    """One text row per frame row, in `summary.SUMMARY_COLUMNS` order. Rows are not reordered."""
    return [
        [
            _date(row["date"]),
            row["network"],
            row["campaign_id"],
            _integer(row["mmp_installs"]),
            _integer(row["network_reported_installs"]),
            _decimal(row["spend_usd"], 2),
            _decimal(row["cpi_usd"], 4),
            _decimal(row["install_gap_pct"], 2),
            _timestamp(row["spend_reported_at"]),
        ]
        for row in df.iter_rows(named=True)
    ]


def retention_rows(df: pl.DataFrame) -> list[list[str]]:
    """One text row per frame row, in `retention.RETENTION_COLUMNS` order. Rows are not reordered."""
    return [
        [
            _date(row["install_week"]),
            _integer(row["cohort_size"]),
            _integer(row["d7_retained_users"]),
            _decimal(row["d7_retention_pct"], 2),
            "true" if row["is_mature"] else "false",
        ]
        for row in df.iter_rows(named=True)
    ]


def _replace_file(path: Path, data: bytes) -> None:
    """Write beside the target and then swap it in, so the target is never left half-written.

    Bytes, not text: in text mode Windows would turn every "\\n" into "\\r\\n".
    """
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _date(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def _timestamp(value: datetime | None) -> str:
    """A zone-less datetime holding UTC, written with the Z the input files use."""
    return "" if value is None else value.isoformat(timespec="seconds") + "Z"


def _integer(value: float | None) -> str:
    """A whole number without a decimal point, even when the column arrived as a float (2.0 -> 2)."""
    if value is None:
        return ""
    if value != int(value):
        raise ValueError(f"expected a whole number, got {value!r}")
    return str(int(value))


def _decimal(value: float | None, places: int) -> str:
    """A fixed number of decimals. NaN or infinity here means a bug upstream, so it fails loudly."""
    if value is None:
        return ""
    if not math.isfinite(value):
        raise ValueError(f"expected a finite number, got {value!r}")
    text = f"{value:.{places}f}"
    return text.lstrip("-") if float(text) == 0 else text  # never "-0.00"
