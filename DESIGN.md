# DESIGN: this pipeline, daily, for 50 studios

The take-home is already shaped for this: a run is a pure function of `(studio, as_of, files that had arrived by
as_of)`. Everything below follows from keeping that true in production.

## 1. Storage, partitioning, idempotent re-runs

| Layer | Layout | Rule |
|---|---|---|
| Raw | `raw/{studio}/{source}/arrival_date=YYYY-MM-DD/{file}` in object storage | Stored as received, never edited. Partitioned by **arrival** date, with a content hash, so a re-delivered file is a no-op. |
| Typed | `installs` and `events` by `(studio, event_date)`; `spend_reports` append-only by `(studio, arrival_date)`, every version kept with its `reported_at`; `campaigns`, `fx_rates` versioned | Explicit casts; rejected rows go to a quarantine table with the reason. |
| Published | `daily_campaign_summary` by `(studio, date)`; `weekly_retention` by `(studio, install_week)`; each row carries `as_of` and `run_id` | Written by **replacing the partition** (write beside, swap atomically), never by appending. |

Re-running one day recomputes its partitions from raw and swaps them in. Same inputs give the same bytes, which
the repo's determinism test already enforces, so a retry or a double trigger cannot double count.

## 2. Restatements and late files

Two time axes: `stat_date` (what the number is about) and arrival (`reported_at`, when we learned it). `--as-of D`
fixes the second axis. The daily job is simply `--as-of yesterday`; a backfill is a loop over `as_of`.

- **Routine window.** Every run recomputes the last 7 `stat_date`s of the summary. In this data first reports land
  6 to 8 hours after the day ends and restatements 2.0 to 2.4 days later, so 7 days covers both with margin.
  Retention recomputes every cohort that is not yet mature (the last three weeks).
- **Outside the window, arrival drives it.** With Dagster, raw landing is a sensor and the published tables are
  partitioned assets. **On Friday, when Tuesday's file arrives:** the sensor reads the file's `stat_date` range,
  marks Tuesday's summary partition stale, and Friday's run rebuilds only that partition. Latest `reported_at`
  wins, Tuesday's `spend_missing` flag clears, and the old and new values are written to the change log (section 4).
- Because raw is append-only by arrival, `--as-of Wednesday` still reproduces Wednesday's report, with Tuesday
  missing. That is how support answers "what did the studio see on Wednesday?"

## 3. What stops the run, what only flags

**Stops** (nothing is published for that studio; yesterday's numbers stay up; on-call is paged): a missing or
unreadable MMP file, a missing required column, a non-unique `campaign_id`, rejected rows above 1% of a file, a
daily row count below half the trailing median, or a broken output invariant (duplicate summary keys, cohort total
not equal to cleaned installs).

**Flags** (published, recorded in the quality report): duplicates, reinstalls, normalised and unmapped campaigns,
restatements, a missing FX rate, a network report that has not arrived (cells empty, never zero), suspected day
bucketing, outlier purchases. A late network report is `pending` until the day after its stat day, then `overdue`.

One studio stopping never blocks the other 49: each studio is its own task.

## 4. How a studio learns why yesterday's number changed

Each run diffs the partitions it rewrote against the previous version and writes `summary_changes`:
`(studio, date, campaign_id, column, old, new, reason, run_id)`. The reason comes from which input moved:
`spend_restated`, `late_report_arrived`, `fx_rate_added`, `campaign_mapping_changed`, `late_installs`,
`code_version_changed`. The dashboard shows a "changed since yesterday" marker on the cell with the reason and
both values, and a daily digest lists changes above 1% of spend. `spend_reported_at` is already in the output for
the same purpose.

## 5. What I monitor

- **Freshness:** hours from end of stat day to first report, per network; alert when `pending` turns `overdue`.
- **Completeness:** campaign-days with installs and no spend, spend without an FX rate, unmapped install share.
- **Volume:** installs and events per studio per day against a trailing baseline; rejected-row rate.
- **Reconciliation drift:** network-versus-MMP install gap per network, both level (about 9% here) and day-to-day
  spread; a step change means an attribution window or time-zone setting moved.
- **Restatement size:** percentage change of spend on restatement, per network.
- **Quality checks as time series:** a check firing for the first time, or any `error`, alerts.
- **Run health:** duration, failures, retries, stale partitions, and a daily re-run of a past `as_of` compared by
  hash to catch non-determinism.
