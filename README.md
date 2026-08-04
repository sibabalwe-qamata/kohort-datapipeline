# Kohort Technical Interview

Welcome! Please follow the setup below.

## Setup 

Clone this repo, then:

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python3 starter.py
```

If `starter.py` prints the row counts of five data files, you're ready.

## During the session

- Use any editor or notebook you like.
- **AI assistants (Claude etc.) are allowed and encouraged from Part B onward** —
keep the assistant visible on your shared screen; how you work with it is part of
the session. Part A (SQL) is written without an assistant.
- Task details are in `candidate_brief.md` — we'll walk through it together at
the start.
- Part A runs on DuckDB, but you can write SQL in the dialect you know best:
set `DIALECT` at the top of `part_a.py` to e.g. `"postgres"`, `"snowflake"`,
`"bigquery"`, `"mysql"`, or `"tsql"` (default `"duckdb"`).
- At the end, please commit and push your work to a branch named after you:

```bash
git checkout -b <your-first-name>
git add -A && git commit -m "interview session"
git push -u origin <your-first-name>
```

