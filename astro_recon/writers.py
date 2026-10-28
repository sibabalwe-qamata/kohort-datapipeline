"""Write the graded files so that the bytes are the same on every run and every platform.

Each value is turned into text by a fixed rule (`formats.py`, per the spec of each output) and written
with the standard library, so the output does not depend on how a dataframe library prints a float, a
null or a line ending.
"""
from __future__ import annotations

import csv
import io
import json
import os
from collections.abc import Iterable, Sequence
from pathlib import Path

import polars as pl

from astro_recon import retention, summary
from astro_recon.formats import format_rows


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
    return format_rows(df, summary.SUMMARY_FORMAT)


def retention_rows(df: pl.DataFrame) -> list[list[str]]:
    """One text row per frame row, in `retention.RETENTION_COLUMNS` order. Rows are not reordered."""
    return format_rows(df, retention.RETENTION_FORMAT)


def _replace_file(path: Path, data: bytes) -> None:
    """Write beside the target and then swap it in, so the target is never left half-written.

    Bytes, not text: in text mode Windows would turn every "\\n" into "\\r\\n".
    """
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
