"""Output 2: weekly install-cohort D7 session retention.

The metric itself lives in ``sql/weekly_retention.sql`` so an analyst can read it in one place.
This module only hands the two frames to DuckDB, binds ``as_of`` and tidies the result types.
"""

from __future__ import annotations

from datetime import date
from importlib import resources

import duckdb
import polars as pl

RETENTION_COLUMNS = ["install_week", "cohort_size", "d7_retained_users", "d7_retention_pct", "is_mature"]

# Output dtypes, in column order. DuckDB already returns these; the cast pins the contract.
RETENTION_SCHEMA: dict[str, pl.DataType] = {
    "install_week": pl.Date(),
    "cohort_size": pl.Int64(),
    "d7_retained_users": pl.Int64(),
    "d7_retention_pct": pl.Float64(),
    "is_mature": pl.Boolean(),
}

SQL_PACKAGE = "astro_recon"
SQL_PATH = "sql/weekly_retention.sql"


def load_retention_sql() -> str:
    """Read the metric definition shipped inside the package (source tree or installed)."""
    return resources.files(SQL_PACKAGE).joinpath(SQL_PATH).read_text(encoding="utf-8")


def weekly_retention(
    installs: pl.DataFrame,
    events: pl.DataFrame,
    as_of: date,
    con: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Return one row per install week with cohort size, D7 retained users, rate and maturity.

    ``installs`` and ``events`` are the typed frames before any as-of filtering: the SQL applies
    the cut-off, the two cleaning rules and the retention window itself.
    ``con`` is optional so a test can pass a connection set to another time zone.
    """
    own_connection = con is None
    if con is None:
        con = duckdb.connect(":memory:")
    try:
        # Weeks are cut in UTC whatever the machine's local zone is.
        con.execute("SET TimeZone = 'UTC'")
        # Only the columns the SQL reads, as Arrow tables (no copy through pandas).
        con.register("installs", installs.select("user_id", "install_ts").to_arrow())
        con.register("events", events.select("user_id", "event_ts", "event_type").to_arrow())
        # as_of is bound as a named parameter, never pasted into the SQL text.
        result = con.execute(load_retention_sql(), {"as_of": as_of}).pl()
    finally:
        if own_connection:
            con.close()
    return result.select(RETENTION_COLUMNS).cast(RETENTION_SCHEMA).sort("install_week")
