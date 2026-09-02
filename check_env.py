"""
Environment check for the take-home. Run: python3 check_env.py

Prints library versions and the size of each data file. It deliberately
contains no loading helpers: how you read the data is part of the task.
"""
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
FILES = ("mmp_installs", "mmp_events", "network_spend", "campaigns", "fx_rates")


def main() -> int:
    print(f"python   {sys.version.split()[0]}")

    versions = {}
    for pkg in ("polars", "duckdb", "pyarrow"):
        try:
            versions[pkg] = __import__(pkg).__version__
        except ImportError:
            versions[pkg] = "not installed"
        print(f"{pkg:<8} {versions[pkg]}")
    print()

    if versions["polars"] == "not installed":
        print("polars is required to read the data: pip install -r requirements.txt", file=sys.stderr)
        return 1
    import polars as pl

    missing = [f"{name}.csv" for name in FILES if not (DATA / f"{name}.csv").is_file()]
    if missing:
        print(f"Missing data files in {DATA}: {', '.join(missing)}", file=sys.stderr)
        print("Did you clone the whole repository? The data/ folder should contain five CSVs.", file=sys.stderr)
        return 1

    for name in FILES:
        # Explicit all-string read: no type inference, so this prints the same
        # counts everywhere. Choosing real types is your job in the pipeline.
        df = pl.read_csv(DATA / f"{name}.csv", infer_schema_length=0)
        print(f"{name:<14} {df.height:>8,} rows  {df.width} cols")

    print("\nSetup OK - read ASSIGNMENT.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
