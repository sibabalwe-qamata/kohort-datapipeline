# Technical Interview — Senior Software Developer (Data Engineering)

Welcome! This is a collaborative working session, not a quiz. We'll work through a
realistic version of the problems our data team solves every day. Think out loud,
ask questions, and use whatever workflow you'd use on the job — **AI assistants
(Claude etc.) are allowed and encouraged**. We're interested in how you work, not
just the end result.

## Scenario

You're a data engineer at Kohort. A mobile game studio, **Nebula Games**, has just
onboarded their game **Astro Miners**. We receive their data from two places:

1. **MMP (mobile measurement partner)** — event-level data attributed to marketing
   campaigns: installs and in-app events.
2. **Ad networks** — daily spend reports per campaign, as reported by each network's
   API.

The studio's UA team says our dashboard numbers "look off" and doesn't trust them
yet. Your job: get the data into shape and figure out what's going on.

## Files (`data/`)

| File | Grain | Notes |
|---|---|---|
| `mmp_installs.csv` | one row per attributed install | install_id, user_id, install_ts_utc, platform, country, media_source, campaign |
| `mmp_events.csv` | one row per in-app event | sessions and purchases (revenue_usd) |
| `network_spend.csv` | daily spend per network campaign | as delivered by network APIs |
| `campaigns.csv` | campaign reference | id, name, network |
| `fx_rates.csv` | daily FX rates | currency → USD |

Data covers 2026-05-01 to 2026-06-30. Like all real third-party data, it should
not be assumed clean.

## Part A — SQL (~15 min)

Using SQL (DuckDB is set up; any dialect thinking is fine):

> **Compute weekly install-cohort D7 session retention.** For each install week:
> cohort size and the % of users with at least one session on day 7 after install.

We'll discuss your approach and anything you'd watch out for.

## Part B — Build a reconciliation summary (~40 min)

In Python (Polars preferred; Pandas acceptable):

> Produce a **daily campaign performance summary**: one row per
> `(date, network, campaign)` with: MMP installs, network-reported installs,
> spend (USD), and CPI (spend / MMP installs).
>
> The UA team wants to know: **do the two sources agree? Where they don't,
> why not?** Keep a running list of every data issue you find and the decision
> you made about it.

You are not expected to "finish" — depth and judgment matter more than coverage.

## Part C — Discussion (~20 min)

No coding. We'll talk about how you'd run this in production for 50 studios,
every day.
