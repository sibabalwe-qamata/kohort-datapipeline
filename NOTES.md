# NOTES

Numbers are from the default run on `data/` (`as_of` 2026-07-20, 610 summary rows, 10 cohort weeks, 1.8 s).
Every count is also in `output/data_quality_report.json`, which carries 31 checks: the nine required plus 22 of mine.
A check that finds nothing is still emitted with `rows_affected: 0` and severity `info`.

## Decisions and numbers

| Rule or anomaly | What I did | Count |
|---|---|---|
| Exact duplicate install rows | Collapsed on the seven raw columns, before any trimming | 1,287 (1.48%) |
| Users with several installs | Earliest kept. Tie on timestamp (spec silent): lowest `install_id` wins, and is flagged | 0 |
| Organic | Excluded from the summary, kept in cohorts. Matched exactly as `organic`; variants are flagged, not folded in | 13,574 |
| Campaign value not the canonical id | Trim + upper-case to an id, else case-insensitive name match | 3,272 (2,504 by id, 768 by name) |
| Unmapped campaign | Excluded, never guessed. A name shared by two ids counts as unmapped | 0 |
| Restated spend | Latest `reported_at` wins, whole row. Summing the file as delivered overstates spend by 27% | 163 |
| Non-USD spend | Converted at the rate of its own `stat_date` | 60 |
| Missing FX rate | Row kept, `spend_usd` and `cpi_usd` empty. No carry-forward of another day's rate | 0 |
| Installs with no spend report | Shown as missing, never zero. Split into `pending` (stat day on or after `as_of`) and `overdue` | 6, all overdue: unityvid 2026-06-10..12, 728 installs |
| adglow currency | USD until 2026-05-31, EUR from 2026-06-01. Converted as reported; CPI stays continuous, so it looks real | 60 rows, CAMP-007 and CAMP-008 |
| tikboom daily gap | Daily gap std 17.8 against 3.4 to 4.0 elsewhere, while its total gap (8.3%) matches. Looks like a UTC+8 reporting day. **Not shifted**, flagged | 122 campaign-days |
| Networks over-report | Networks report 8.3 to 9.5% more installs than the MMP. Not corrected | 576 of 604 comparable days |
| Events before install | Kept, flagged. 1 to 120 minutes early; cannot fall in a D7 window | 1,006 events, 1,000 users |
| Events with no install | Ignored by retention, flagged | 1: `u_whale00000001`, 49,900 USD |
| Purchase above 1,000 USD | Flagged, not capped. Next largest purchase is 68.44 | 1 (the same event) |
| Negative revenue | Kept as refunds | 40, total −200.31 |
| Install hour pile-up | 23:00 UTC holds 6,918 installs against 2,979 at 22:00. Low confidence, flagged as a question | 6,918 |
| Rows that will not parse | Every column is read as text and cast explicitly; a failing row is dropped and counted | 0 |

Interpretations where the spec was open:

- **`cpi_usd`** divides the 2-decimal `spend_usd`. The mini reference needs this: `2.05 / 2 = 1.025`, not `2.052 / 2`.
- **Summary join** uses the spend file's own `network` and `campaign_id` as delivered. A spend row for an unknown
  campaign, or under a network that disagrees with `campaigns.csv`, is kept as its own row and flagged as an error.
- **`--as-of`** default is the date of the latest event in the unfiltered file. With no events and no `--as-of`
  the run exits 2 and asks for the flag. At `--as-of 2026-06-15`: 460 rows, 16 missing-spend days (6 overdue, 10
  pending for 2026-06-15), week 2026-06-01 mature, 2026-06-08 and 2026-06-15 not.
- **Retention** is defined entirely in `astro_recon/sql/weekly_retention.sql`, including the as-of rule and
  cleaning rules 1 and 2. The Polars cleaning is a second implementation; a test asserts both give the same users.
- **Time zones.** The machine this was built on runs DuckDB in `Africa/Johannesburg`. Timestamps are passed
  zone-less and the session is set to UTC; a test with zone-aware input on Tokyo and Los Angeles sessions pins it.
- **Thresholds are constants in `checks.py`**: purchase above 1,000 USD; day-bucketing when a network's daily gap
  std is above 5 points and above 3 times the median network; pile-up when hour 23 is above 1.5 times hour 22.
- **The 2026-06-04..07 tikboom CAMP-005 burst** (2.5 to 3 times normal volume) is left alone: spend rises with it
  and D7 retention of those cohorts is normal.

## Questions I would ask the studio

1. Did adglow's billing currency change on 2026-06-01, or only the label in the API response?
2. Is tikboom's reporting day UTC+8, and can the report be requested in UTC?
3. Where are unityvid's reports for 2026-06-10 to 2026-06-12?
4. Is `u_whale00000001` a test account, and is 49,900 cents entered as dollars?
5. Are negative purchases refunds, and should dashboard revenue be net of them?
6. Which attribution windows explain the steady 9% gap between network and MMP installs?
7. Does the MMP clamp late-arriving installs into the last hour of the UTC day?

## Known gaps / next steps

- **No plausibility window on timestamps.** A far-future event would move the default `as_of`, and a two-digit
  year parses as year 0026. In production `as_of` comes from the scheduler.
- **Rejected rows are counted, not written out.** A quarantine file per input, with the reason, is the next step.
- **Tested on one stack only:** Python 3.14, polars 2.0.0, duckdb 1.5.6, Windows. `pip install -e .`, the CI
  workflow, ruff and mypy have not been run.
- **Thresholds are fixed constants**; they should be per-studio baselines.
- **Revenue is in no output**, so duplicate, negative and implausible purchases are only flagged.
- **Check summaries name counts, not entities**, to stay under 300 characters; the names are in `metrics`.

## How I used AI

Claude Code profiled the five files, proposed the plan and edge-case list, and wrote the code and tests through
sub-agents working to that plan. Every reference number was derived twice, once in standalone SQL and once by the
pipeline, and compared.
