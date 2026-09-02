# Kohort Technical Interview

Welcome! Please follow the setup below.

## Setup 

Clone this repo, then follow the steps for your platform.

**macOS / Linux:**

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt
python3 starter.py
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1   # optional but recommended
pip install -r requirements.txt
python starter.py
```

**Windows (Command Prompt):**

```bat
python -m venv .venv
.venv\Scripts\activate.bat   # optional but recommended
pip install -r requirements.txt
python starter.py
```

Windows notes:

- If PowerShell blocks the activate script ("execution of scripts is disabled"),
run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once,
or use Command Prompt instead.
- If `python` opens the Microsoft Store, install Python from
[python.org](https://www.python.org/downloads/) (tick "Add Python to PATH"), or
use the `py` launcher (`py -m venv .venv`).
- In Git Bash, use `source .venv/Scripts/activate`.

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
- At the end, please email your working files — `part_a.py`, your Part B
script/notebook, and your issues log — to **michelle@kohort.io**. Please don't
push branches to this repo.

