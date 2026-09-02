# Take-home: Astro Miners reconciliation pipeline

Read this whole document before you write code. Setup, rules and submission are in `README.md`.

## Scenario

You are a data engineer at Kohort. A mobile game studio, **Nebula Games**, has just onboarded their
game **Astro Miners**. We receive their data from two places:

1. **MMP (mobile measurement partner)** — event-level data attributed to marketing campaigns:
   installs and in-app events.
2. **Ad networks** — daily spend reports per campaign, as delivered by each network's API.

The studio's UA team says our dashboard numbers "look off" and does not trust them yet. We receive
this kind of data daily for about 50 studios, so we need a **pipeline**, not a one-off analysis:
something that runs on a schedule, produces the same answer twice, and tells us what it did to the data.

## Data (`data/`)

| File | Grain | Columns |
|---|---|---|
| `mmp_installs.csv` | one row per attributed install | `install_id, user_id, install_ts_utc, platform, country, media_source, campaign` |
| `mmp_events.csv` | one row per in-app event | `user_id, event_ts_utc, event_type (session \| purchase), revenue_usd` |
| `network_spend.csv` | one report per network campaign-day | `stat_date, network, campaign_id, spend, currency, reported_installs, reported_at` |
| `campaigns.csv` | campaign reference | `campaign_id, campaign_name, network` |
| `fx_rates.csv` | daily FX rates to USD | `date, currency, usd_per_unit` |

Installs cover **2026-05-01 to 2026-06-30**; events run to **2026-07-20** so that June cohorts have a
full week to retain. All timestamps are UTC. Like all third-party data, it should not be assumed clean.

`data_mini/` is a tiny hand-made dataset with the same schema, and `data_mini/expected/` holds the
exact outputs your pipeline must produce for it (a default run and a `--as-of 2026-05-10` run). Use it
to check your understanding of every rule below; `tests/test_contract.py` does exactly that.

## Deliverable

A Python package runnable as:

```bash
python -m astro_recon --data <dir> --out <dir> [--as-of YYYY-MM-DD]
```

- Runs from the repo root after `pip install -r requirements.txt` (and `pip install -e .` if you ship a
  `pyproject.toml` — say so in your README). Put any extra dependency in `requirements.txt`.
- Creates `--out` if needed and overwrites the three files below. Exit code 0 on success. A missing input
  file, a missing required column or an unparseable `--as-of` must exit non-zero with a message on stderr.
- No network access at runtime. Under two minutes on `data/`.
- The module name is fixed only so our grader can invoke it. Everything inside is yours. Polars, Pandas or
  DuckDB — library choice is not graded.

## Output 1 — `output/daily_campaign_summary.csv`

One row per `(date, network, campaign_id)`: the union of every campaign-day that has at least one
cleaned paid install and every campaign-day that has at least one visible spend report. Sorted by
`date, network, campaign_id`. Columns, in this order:

| column | type | rule |
|---|---|---|
| `date` | `YYYY-MM-DD` | MMP install date (UTC) and network `stat_date`, joined as-is. **No time-zone or day-bucket correction for any network.** |
| `network` | text | from `campaigns.csv` by `campaign_id` |
| `campaign_id` | text | canonical id from `campaigns.csv` |
| `mmp_installs` | integer, never empty | count of cleaned paid installs; `0` when the row exists only because a spend report exists |
| `network_reported_installs` | integer or empty | `reported_installs` from the report used |
| `spend_usd` | 2 dp or empty | `spend × usd_per_unit` at (`stat_date`, `currency`); empty if there is no report or no rate |
| `cpi_usd` | 4 dp or empty | `spend_usd / mmp_installs`; empty if `spend_usd` is empty or `mmp_installs` is 0 |
| `install_gap_pct` | 2 dp or empty | `(network_reported_installs − mmp_installs) / mmp_installs × 100`; empty if either input is empty or `mmp_installs` is 0 |
| `spend_reported_at` | `YYYY-MM-DDTHH:MM:SSZ` or empty | `reported_at` of the report used |

**Install cleaning, in this order**

1. Identical rows count once.
2. If a `user_id` still has more than one row, keep the earliest `install_ts_utc`.
3. `media_source = organic` installs are excluded from this output (there is no spend to compare) and counted.
4. Campaign: trim and upper-case the raw `campaign` value; if that equals a `campaign_id`, use it. Otherwise, if the
   trimmed raw value equals a `campaign_name` case-insensitively, use that campaign's id. Otherwise the row is
   **unmapped**: excluded and counted, never guessed.
5. `network` is looked up from `campaigns.csv`.

**Spend rules**

- Key is `(stat_date, network, campaign_id)`. When several visible rows share a key, the one with the greatest
  `reported_at` wins (there are never ties). The others are *restated* rows.
- Convert with `fx_rates` at (`stat_date`, `currency`). USD needs no lookup. A non-USD row with no rate for its
  date keeps its row and gets an empty `spend_usd`.
- The two sources are not symmetric: MMP is an event stream, so absence means zero installs. A network report is a
  delivery, so absence means **missing**, never zero.

## Output 2 — `output/weekly_retention.csv`

Weekly install-cohort D7 session retention. **This metric must be defined in SQL and executed with DuckDB**
from your pipeline (a `.sql` file or a string constant is fine). Metric definitions at Kohort are read by analysts,
so they live in SQL. Bind `as_of` as a query parameter; do not format it into the string.

| column | rule |
|---|---|
| `install_week` | `YYYY-MM-DD`, the Monday of the ISO week containing `install_ts_utc` (UTC) |
| `cohort_size` | distinct users with a cleaned install that week (cleaning rules 1–2 only; **organic included**) |
| `d7_retained_users` | distinct cohort users with at least one `session` where `install_ts + 7 days ≤ event_ts < install_ts + 8 days` |
| `d7_retention_pct` | `d7_retained_users / cohort_size × 100`, 2 dp |
| `is_mature` | `true` / `false`: `install_week + 13 days ≤ as_of` (every user in the week has had a full D7 window) |

Only weeks with at least one install appear; partial edge weeks are included. Sorted by `install_week`.
Purchases never count as retention. Be careful that your engine treats the timestamps as UTC: a DuckDB
session running in the machine's local zone will move installs between weeks.

## Output 3 — `output/data_quality_report.json`

```json
{
  "as_of": "2026-05-30",
  "checks": [
    {
      "id": "duplicate_install_rows",
      "severity": "warning",
      "rows_affected": 2,
      "action": "removed",
      "summary": "2 exact duplicate install rows (5.0% of the file) collapsed before counting.",
      "metrics": {"pct_of_raw_rows": 5.0}
    }
  ]
}
```

- `severity` ∈ `info | warning | error`. `action` ∈ `removed | corrected | excluded | flagged | none`.
  `summary` ≤ 300 characters. `metrics` is an optional object of numbers or short lists.
- `checks` sorted by `id`. Ids unique. **No wall-clock values anywhere in this file** (see Determinism).
- These nine ids are required and `rows_affected` must be exact (we check them):

| id | `rows_affected` |
|---|---|
| `duplicate_install_rows` | install rows removed as exact duplicates |
| `install_reinstall_rows` | further rows removed because the user already had an earlier install |
| `organic_installs_excluded` | cleaned installs with `media_source = organic` |
| `campaign_id_normalised` | cleaned paid installs whose raw `campaign` differs from the final `campaign_id` |
| `campaign_unmapped` | cleaned paid installs whose `campaign` maps to nothing (excluded) |
| `spend_restated_rows` | visible spend rows superseded by a later report for the same key |
| `spend_fx_converted_rows` | kept spend rows with a non-USD currency and a rate |
| `fx_rate_missing` | kept non-USD spend rows with no rate for their date |
| `spend_missing` | summary rows with `mmp_installs > 0` and no spend report at all |

All counts describe the data visible at the run's `as_of`.

**Add a check for every other issue you find.** Ids and wording are yours. This is where we look for judgment:
what you noticed, what you did about it, what you deliberately did *not* fix, and what you would ask the studio.

## `--as-of YYYY-MM-DD`

"The report as it could have been produced at the end of UTC day D." Only this data is visible:

- `mmp_installs`: `install_ts_utc < D + 1 day`
- `mmp_events`: `event_ts_utc < D + 1 day`
- `network_spend`: `reported_at < D + 1 day` (arrival time, not `stat_date`)
- `campaigns`, `fx_rates`: not filtered

Stated assumption: MMP data arrives in real time; network data arrives at `reported_at`. When `--as-of` is
omitted, `D` is the date of the latest `event_ts_utc` in the file. All three outputs, including every count in
the quality report, describe the visible data. Look at `data_mini/expected/asof_2026-05-10/` to see the shape:
the newest days have installs but no spend yet, a restatement that arrives later is not yet visible, and the
latest cohort is immature.

## Determinism and format

- Two runs with identical arguments must produce **byte-identical** graded files. Put run metadata (timestamps,
  versions) in a separate file such as `run_manifest.json` if you want it.
- CSV: UTF-8, header row, comma-separated, empty string for null, `true`/`false` for booleans, no index column.
  We parse numbers, so `5.5` and `5.50` are both fine; column names and order are not negotiable.
- Commit `output/` from a default run on `data/`.
- **Your pipeline will also be run on a second dataset** with the same schema and date range, different values,
  and a few extra edge cases (a missing FX rate, campaign values that map to nothing, a campaign-day with spend
  but no installs, users with two distinct installs). Do not hard-code anything from `data/`.

## Tests

`pytest` must pass from the repo root. `tests/test_contract.py` is ours — keep it green; it runs your CLI on
`data_mini/` and compares against `data_mini/expected/`. Add your own unit tests for at least: exact-duplicate
removal, campaign normalisation, latest-report selection, and the `--as-of` boundary. We read tests for what they
would catch, not how many there are.

## `NOTES.md` (required, one page)

- **Decisions and numbers** — each rule or anomaly, what you did, and the count it affected.
- **Questions I would ask the studio.**
- **Known gaps / next steps** — what you would do with another day.
- **How I used AI** (optional, three lines).

## `DESIGN.md` (required, one page)

This exact pipeline now runs daily for 50 studios. Answer, briefly:

1. Storage layout, partitioning, and how a re-run of one day is idempotent.
2. Restatements and late files: what gets reprocessed, how wide the window is, and how `--as-of` relates.
3. What stops the run versus what only flags.
4. How a studio learns why yesterday's number changed.
5. What you monitor.

Naming a tool is fine; then tell us what it does on Friday when Tuesday's file arrives.

## Required and stretch

| Item | |
|---|---|
| CLI per contract, exit codes, `--out` creation | required |
| `daily_campaign_summary.csv` per spec | required |
| `weekly_retention.csv` in SQL via DuckDB | required |
| `data_quality_report.json` with the nine required ids | required |
| `--as-of` and `is_mature` | required |
| Byte-identical reruns | required |
| Contract test green plus your own unit tests | required |
| `NOTES.md` and `DESIGN.md` | required |
| Installable package (`pyproject.toml`) | required |
| Additional quality checks for issues you find | stretch, graded under judgment if present |
| Your own targeted fixtures per rule | stretch |
| Type hints, ruff/mypy, CI workflow | stretch |

## How we grade

We run our grader before we read a line of code: the sample dataset (default and `--as-of 2026-06-15`), the
second dataset, a second run for determinism, a bad `--data` path, and `pytest`. Then two people read the code
and the notes. Then we talk for an hour.

| Dimension | Weight |
|---|---|
| Software engineering — structure, tests that would catch regressions, error handling, packaging, determinism, commit history | 30% |
| Data correctness — the grader | 20% |
| Data judgment — the quality report and `NOTES.md` | 20% |
| Follow-up session — you explain and change your own code, one short unaided SQL exercise | 20% |
| Production design — `DESIGN.md` and the conversation | 10% |

## FAQ

- **Pandas instead of Polars?** Fine. **A notebook?** For exploration, yes; the deliverable is a package.
- **Can I rename or add columns in the graded files?** No. Write extra files if you want them.
- **Can there be two reports with the same `reported_at` for one key?** No, in any dataset we run.
- **Time zones?** Everything is UTC. Do not shift any network's days, even if you think you know its zone — say so in the report instead.
- **The spec is ambiguous about X.** Pick an interpretation, state it in `NOTES.md`, carry on.
- **My command does not run on your machine.** We send one email and try again once.
- **Another language?** Python only: it is the team's stack and the grader installs from `requirements.txt`.
