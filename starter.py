"""
Environment check + convenience loaders for the interview exercise.
Run: python3 starter.py
"""
from pathlib import Path

import duckdb
import polars as pl

DATA = Path(__file__).parent / "data"

FILES = {
    "installs":  DATA / "mmp_installs.csv",
    "events":    DATA / "mmp_events.csv",
    "spend":     DATA / "network_spend.csv",
    "campaigns": DATA / "campaigns.csv",
    "fx":        DATA / "fx_rates.csv",
}


def load_polars() -> dict[str, pl.DataFrame]:
    """All five files as Polars DataFrames."""
    return {name: pl.read_csv(path) for name, path in FILES.items()}


def duckdb_connection() -> duckdb.DuckDBPyConnection:
    """DuckDB connection with each file registered as a view."""
    con = duckdb.connect()
    for name, path in FILES.items():
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_csv_auto('{path}')")
    return con


if __name__ == "__main__":
    print("polars", pl.__version__, "| duckdb", duckdb.__version__)
    for name, df in load_polars().items():
        print(f"{name:<10} {df.height:>8,} rows  {df.width} cols")
    print("\nSetup OK - see candidate_brief.md for the exercise.")
