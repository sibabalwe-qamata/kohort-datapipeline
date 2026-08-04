"""
Part A — SQL. Edit the SQL string below, then run: python3 part_a.py

Tables (views over the CSVs in data/):
    installs, events, spend, campaigns, fx

The engine is DuckDB. If you're more at home in another dialect, set DIALECT
below and write in that — it's transpiled to DuckDB before running.
"""
from starter import duckdb_connection

SQL = """
SELECT * FROM installs LIMIT 5
"""

# Optional: write in the dialect you know best; it's transpiled to DuckDB.
# e.g. "postgres", "snowflake", "bigquery", "mysql", "tsql", "spark"
DIALECT = "duckdb"

if __name__ == "__main__":
    sql = SQL
    if DIALECT != "duckdb":
        import sqlglot

        sql = sqlglot.transpile(SQL, read=DIALECT, write="duckdb")[0]
        print(f"-- transpiled {DIALECT} -> duckdb:\n{sql}\n")

    con = duckdb_connection()
    con.execute("SET timezone = 'UTC'")  # timestamps are UTC; keep day boundaries in UTC
    con.sql(sql).show(max_rows=50)
