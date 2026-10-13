"""Output 1: one row per (date, network, campaign_id), installs on one side and spend on the other."""

from __future__ import annotations

import polars as pl

KEY = ["date", "network", "campaign_id"]
SUMMARY_COLUMNS = ["date", "network", "campaign_id", "mmp_installs", "network_reported_installs",
                   "spend_usd", "cpi_usd", "install_gap_pct", "spend_reported_at"]


def build_summary(mapped: pl.DataFrame, kept_spend: pl.DataFrame) -> pl.DataFrame:
    """Full outer join of daily install counts and the kept spend reports.

    `mapped` is the output of `installs.map_campaigns`; `kept_spend` is the output of `spend.convert_fx`
    (one row per key, with `spend_usd`). The two sides are joined on their keys as they are: no time-zone
    or day-bucket correction.

    The sources are not symmetric. Installs are an event stream, so no rows means zero installs. A spend
    report is a delivery, so no report means unknown: those columns stay null, they never become 0.
    """
    installs = (
        mapped.with_columns(date=pl.col("install_ts").dt.date())
        .group_by(KEY)
        .agg(mmp_installs=pl.len())
    )
    reports = kept_spend.select(
        *KEY,
        network_reported_installs=pl.col("installs_reported"),
        spend_usd=pl.col("spend_usd"),
        spend_reported_at=pl.col("reported_ts"),
    )
    joined = installs.join(reports, on=KEY, how="full", coalesce=True)
    joined = joined.with_columns(
        pl.col("mmp_installs").fill_null(0).cast(pl.Int64),
        pl.col("network_reported_installs").cast(pl.Int64),
    )

    # Both ratios divide by mmp_installs, so they are only defined when there is at least one install.
    # A null spend_usd or a null network_reported_installs makes its ratio null without extra code.
    has_installs = pl.col("mmp_installs") > 0
    # spend_usd is already rounded to 2 decimals: cpi is computed from the number we publish.
    cpi = pl.col("spend_usd") / pl.col("mmp_installs")
    gap = (pl.col("network_reported_installs") - pl.col("mmp_installs")) / pl.col("mmp_installs") * 100
    joined = joined.with_columns(
        cpi_usd=pl.when(has_installs).then(cpi).round(4),
        install_gap_pct=pl.when(has_installs).then(gap).round(2),
    )
    return joined.select(SUMMARY_COLUMNS).sort(KEY)
