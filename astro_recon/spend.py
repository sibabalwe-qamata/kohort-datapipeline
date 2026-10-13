"""Spend rules of ASSIGNMENT.md: pick the latest report per campaign-day, then convert it to USD.

Input contract: `date` (Date), `network`, `campaign_id`, `spend_amount` (Float64, nullable), `currency`,
`installs_reported` (Int64, nullable), `reported_ts` (naive UTC Datetime).
"""

from __future__ import annotations

import polars as pl

KEY = ["date", "network", "campaign_id"]
USD = "USD"


def drop_duplicate_reports(spend: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Collapse rows that are equal in every column. Returns (frame, rows removed).

    The same report delivered twice is not a restatement, and it is the only way two rows of one key
    could tie on `reported_ts`, so it is removed before `latest_reports` runs.
    """
    deduped = spend.unique(keep="first", maintain_order=True)
    return deduped, spend.height - deduped.height


def latest_reports(spend: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Keep the report with the greatest `reported_ts` per key. Returns (kept, superseded row count).

    After the sort, the last row of each key is its latest report. `unique` keeps that whole row, so spend,
    currency, installs and timestamp always come from the same report and are never mixed.
    """
    ordered = spend.sort(KEY + ["reported_ts"])
    kept = ordered.unique(subset=KEY, keep="last", maintain_order=True)
    return kept, spend.height - kept.height


def convert_fx(kept: pl.DataFrame, fx: pl.DataFrame) -> pl.DataFrame:
    """Add `rate` and `spend_usd` (2 decimals). Both are null when the row cannot be converted.

    USD needs no lookup: USD rates are dropped from the lookup table, so a USD row has a null `rate` and
    `spend_usd = spend_amount` whatever fx_rates says. A non-USD row with no rate for its own date keeps
    its row with a null `spend_usd`; we never borrow another day's rate.
    """
    rates = fx.filter(pl.col("currency") != USD).select("date", "currency", "rate")
    # The loader promises one rate per (date, currency). `validate` makes the join fail loudly if that is
    # ever broken: a second rate would duplicate the spend row, which means counting the same money twice.
    joined = kept.join(rates, on=["date", "currency"], how="left", validate="m:1")
    converted = pl.col("spend_amount") * pl.col("rate")
    in_usd = pl.when(_is_usd()).then(pl.col("spend_amount")).otherwise(converted)
    return joined.with_columns(spend_usd=in_usd.round(2)).sort(KEY)


def _is_usd() -> pl.Expr:
    """True for USD rows. A null currency is not USD (it cannot be converted either)."""
    return (pl.col("currency") == USD).fill_null(False)


def is_fx_converted() -> pl.Expr:
    """Filter for the output of `convert_fx`: non-USD rows that found a rate (`spend_fx_converted_rows`)."""
    return ~_is_usd() & pl.col("rate").is_not_null()


def is_fx_missing() -> pl.Expr:
    """Filter for the output of `convert_fx`: non-USD rows with no rate for their date (`fx_rate_missing`)."""
    return ~_is_usd() & pl.col("rate").is_null()
