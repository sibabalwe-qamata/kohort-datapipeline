"""
Part A — SQL. Edit the SQL string below, then run: python3 part_a.py
"""
from starter import duckdb_connection

#Tables (views over the CSVs in data/, named after the files):
# mmp_installs, mmp_events, network_spend, campaigns, fx_rates

SQL = """
SELECT * FROM mmp_installs LIMIT 5
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
