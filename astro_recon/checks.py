"""The quality checks: one small function per check, each reading frames the pipeline already built.

A check never re-implements a rule: it counts what a cleaning step returned, or looks at a stage for
something the spec does not ask to be fixed. Every count describes the data visible at `as_of`
(`unparseable_rows` is the one stated exception).

A summary interpolates numbers only. Names and examples go into `metrics`, so a summary cannot grow
past its 300 characters on an unusual dataset. Severities follow the convention in `quality.py`.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

import polars as pl

from astro_recon.installs import count_normalised
from astro_recon.io import Inputs
from astro_recon.quality import LIST_MAX_ITEMS, Check, make_check
from astro_recon.spend import KEY, is_fx_converted, is_fx_missing

# A network's report for day D arrives during day D + 1, so at the end of day D it cannot be there yet.
REPORT_LAG_DAYS = 1

# Day-bucketing detection: a network is flagged when the spread of its daily install gap is large in
# absolute terms and large next to the other networks. Both, so five equally noisy networks flag nobody.
BUCKETING_MIN_STD = 5.0
BUCKETING_PEER_RATIO = 3.0
BUCKETING_MIN_NETWORKS = 3  # a median of fewer networks is not a peer group

IMPLAUSIBLE_PURCHASE_USD = 1000.0

# Install pile-up in the last UTC hour: hour 23 against hour 22, only when hour 22 is big enough to compare.
PILEUP_RATIO = 1.5
PILEUP_MIN_INSTALLS = 20

EVENT_TYPES = ["session", "purchase"]


@dataclass(frozen=True)
class Stages:
    """Every intermediate result of one run. The checks read it; the pipeline is the only writer."""

    as_of: date
    inputs: Inputs  # the whole files, typed
    visible: Inputs  # after the --as-of filter
    deduped: pl.DataFrame  # visible installs after rule 1
    cleaned: pl.DataFrame  # after rule 2: one row per user
    organic: pl.DataFrame
    paid: pl.DataFrame
    mapped: pl.DataFrame
    unmapped: pl.DataFrame
    reports: pl.DataFrame  # visible spend after exact duplicates were removed
    kept: pl.DataFrame  # the latest report per key, with `rate` and `spend_usd`
    summary: pl.DataFrame
    retention: pl.DataFrame
    duplicate_installs: int  # returned by installs.dedupe_exact
    reinstall_rows: int  # returned by installs.keep_earliest_per_user
    duplicate_reports: int  # returned by spend.drop_duplicate_reports
    restated_rows: int  # returned by spend.latest_reports


def all_checks(stages: Stages) -> list[Check]:
    """Every check, found something or not. `quality.build_report` sorts them by id."""
    return [
        # the nine required ids
        duplicate_install_rows(stages),
        install_reinstall_rows(stages),
        organic_installs_excluded(stages),
        campaign_id_normalised(stages),
        campaign_unmapped(stages),
        spend_restated_rows(stages),
        spend_fx_converted_rows(stages),
        fx_rate_missing(stages),
        spend_missing(stages),
        # files
        unparseable_rows(stages),
        fx_rate_invalid(stages),
        # installs
        reinstall_same_timestamp(stages),
        install_id_reused(stages),
        media_source_unrecognised(stages),
        media_source_network_mismatch(stages),
        campaign_reference_ambiguous(stages),
        install_hour_pileup(stages),
        # spend
        duplicate_spend_rows(stages),
        spend_currency_changed(stages),
        spend_without_installs(stages),
        spend_non_positive(stages),
        spend_unknown_campaign(stages),
        network_overreports_installs(stages),
        network_day_bucketing_suspected(stages),
        # events and retention
        events_without_install(stages),
        events_before_install(stages),
        duplicate_event_rows(stages),
        event_type_unrecognised(stages),
        implausible_purchase_amount(stages),
        negative_revenue_events(stages),
        immature_cohorts(stages),
    ]


# ------------------------------------------------------------------ helpers
def _pct(part: int, whole: int) -> float:
    """A percentage to 2 decimals; 0.0 when there is nothing to divide by."""
    return round(part / whole * 100, 2) if whole else 0.0


def _cents(frame: pl.DataFrame, column: str) -> int:
    """A money column added up as whole cents.

    A float sum can differ in its last digits with the order the rows are added in, and Polars adds in
    parallel. Integers add up to the same total in any order, so the report stays byte-identical.
    """
    return frame.select((pl.col(column) * 100).round().cast(pl.Int64).sum()).item()


def _sample(values: pl.Series) -> list[str]:
    """Up to ten distinct values in sorted order, a missing value shown as empty text."""
    return values.fill_null("").unique().sort().head(LIST_MAX_ITEMS).to_list()


def _comparable(summary: pl.DataFrame) -> pl.DataFrame:
    """Summary rows where the two install counts can be compared: both present, and installs to divide by."""
    return summary.filter((pl.col("mmp_installs") > 0) & pl.col("network_reported_installs").is_not_null())


def _total_gap_pct(comparable: pl.DataFrame) -> dict[str, float]:
    """Per network: (sum of reported installs - sum of MMP installs) / sum of MMP installs * 100."""
    totals = (
        comparable.group_by("network")
        .agg(reported=pl.col("network_reported_installs").sum(), mmp=pl.col("mmp_installs").sum())
        .sort("network")
    )
    return {
        row["network"]: _pct(row["reported"] - row["mmp"], row["mmp"]) for row in totals.iter_rows(named=True)
    }


# ------------------------------------------------------------------ the nine required checks
def duplicate_install_rows(stages: Stages) -> Check:
    removed = stages.duplicate_installs
    pct = _pct(removed, stages.visible.installs.height)
    return make_check(
        "duplicate_install_rows", "warning", removed, "removed",
        f"{removed} exact duplicate install rows ({pct}% of the visible file) were collapsed before "
        "counting, so each install counts once.",
        {"pct_of_raw_rows": pct},
    )


def install_reinstall_rows(stages: Stages) -> Check:
    removed = stages.reinstall_rows
    # n_unique counts a missing campaign as a value, so organic first and paid later is a change.
    per_user = stages.deduped.group_by("user_id").agg(rows=pl.len(), campaigns=pl.col("campaign").n_unique())
    repeat = per_user.filter(pl.col("rows") > 1)
    changed = repeat.filter(pl.col("campaigns") > 1).height
    return make_check(
        "install_reinstall_rows", "warning", removed, "removed",
        f"{removed} further rows removed because the user already had an earlier install ({repeat.height} "
        f"users). The earliest install is kept, so for the {changed} users who changed campaign the first "
        "one gets the credit.",
        {"users": repeat.height, "users_changed_campaign": changed},
    )


def organic_installs_excluded(stages: Stages) -> Check:
    organic = stages.organic.height
    return make_check(
        "organic_installs_excluded", "info", organic, "excluded",
        f"{organic} cleaned installs have media_source = organic. They are excluded from the campaign "
        "summary (there is no spend to compare them with) and kept in the retention cohorts.",
    )


def campaign_id_normalised(stages: Stages) -> Check:
    corrected = count_normalised(stages.mapped)
    changed = stages.mapped.filter(pl.col("campaign") != pl.col("campaign_id"))
    as_id = pl.col("campaign").str.strip_chars().str.to_uppercase()
    by_id = changed.filter(as_id == pl.col("campaign_id")).height
    by_name = changed.height - by_id
    return make_check(
        "campaign_id_normalised", "warning", corrected, "corrected",
        f"{corrected} paid installs carried a campaign value that was not the canonical id: {by_id} matched "
        f"after trimming or upper-casing, {by_name} by campaign name. An exact-match join would have "
        "dropped them.",
        {"matched_by_id_after_trim_or_case": by_id, "matched_by_name": by_name},
    )


def campaign_unmapped(stages: Stages) -> Check:
    unmapped = stages.unmapped
    # Most frequent first, ties by value, so the sample is the same on every run.
    counts = (
        unmapped.select(pl.col("campaign").fill_null(""))
        .group_by("campaign")
        .agg(rows=pl.len())
        .sort(["rows", "campaign"], descending=[True, False])
    )
    return make_check(
        "campaign_unmapped", "error", unmapped.height, "excluded",
        f"{unmapped.height} paid installs carry a campaign value that matches no campaign id or name "
        f"({counts.height} distinct values, see raw_values). They are excluded from the summary and counted "
        "here; nothing is guessed or fuzzy-matched.",
        {"distinct_values": counts.height, "raw_values": counts["campaign"].head(LIST_MAX_ITEMS).to_list()},
    )


def spend_restated_rows(stages: Stages) -> Check:
    restated = stages.restated_rows
    keys = stages.reports.group_by(KEY).agg(rows=pl.len()).filter(pl.col("rows") > 1).height
    return make_check(
        "spend_restated_rows", "warning", restated, "removed",
        f"{restated} spend rows were superseded by a later report for the same (stat_date, network, "
        f"campaign_id), on {keys} keys. The latest reported_at wins; summing the file as delivered would "
        "count that spend twice.",
        {"keys_restated": keys},
    )


def spend_fx_converted_rows(stages: Stages) -> Check:
    converted = stages.kept.filter(is_fx_converted())
    return make_check(
        "spend_fx_converted_rows", "info", converted.height, "corrected",
        f"{converted.height} kept spend rows are in a non-USD currency and were converted to USD with the "
        "rate of their own stat_date.",
        {"currencies": converted["currency"].unique().sort().to_list()[:LIST_MAX_ITEMS]},
    )


def fx_rate_missing(stages: Stages) -> Check:
    missing = stages.kept.filter(is_fx_missing())
    keys = missing.select("date", "currency").unique().sort(["date", "currency"]).head(LIST_MAX_ITEMS)
    return make_check(
        "fx_rate_missing", "error", missing.height, "flagged",
        f"{missing.height} kept non-USD spend rows have no FX rate for their stat_date. spend_usd and "
        "cpi_usd are left empty, not zero, and another day's rate is not borrowed.",
        {"keys": [f"{day.isoformat()} {currency}" for day, currency in keys.iter_rows()]},
    )


def spend_missing(stages: Stages) -> Check:
    missing = stages.summary.filter((pl.col("mmp_installs") > 0) & pl.col("spend_reported_at").is_null())
    # The report for day D is due by the end of day D + REPORT_LAG_DAYS. Before that it is only pending.
    is_overdue = pl.col("date") + pl.duration(days=REPORT_LAG_DAYS) <= stages.as_of
    overdue = missing.filter(is_overdue).height
    pending = missing.height - overdue
    installs = missing["mmp_installs"].sum()
    ranges = (
        missing.group_by("network")
        .agg(rows=pl.len(), first=pl.col("date").min(), last=pl.col("date").max())
        .sort("network")
    )
    by_network = {
        row["network"]: {"rows": row["rows"], "from": row["first"].isoformat(), "to": row["last"].isoformat()}
        for row in ranges.iter_rows(named=True)
    }
    return make_check(
        "spend_missing", "error" if overdue else "warning", missing.height, "flagged",
        f"{missing.height} campaign-days have MMP installs and no spend report: {overdue} overdue, {pending} "
        f"pending (a day's report arrives during the next day). {installs} installs have no cost. Shown as "
        "missing, never as zero spend.",
        {"pending": pending, "overdue": overdue, "installs_without_cost": installs, "by_network": by_network},
    )


# ------------------------------------------------------------------ files
def unparseable_rows(stages: Stages) -> Check:
    files = ["mmp_installs", "mmp_events", "network_spend", "campaigns"]
    per_file = {name: stages.inputs.rejected_rows.get(name, 0) for name in files}
    total = sum(per_file.values())
    return make_check(
        "unparseable_rows", "error", total, "excluded",
        f"{total} rows have an unreadable timestamp, date or number, or lack a required value, and are "
        "excluded. Counted on whole files: a row with no readable time cannot be placed before or after "
        "as_of. Every column is read as text and cast explicitly.",
        per_file,
    )


def fx_rate_invalid(stages: Stages) -> Check:
    invalid = stages.inputs.rejected_rows.get("fx_rates", 0)
    return make_check(
        "fx_rate_invalid", "warning", invalid, "excluded",
        f"{invalid} rows of fx_rates.csv cannot be used: unreadable, zero or negative, or two different "
        "rates for one date and currency. They are excluded; a spend row that needed one is counted in "
        "fx_rate_missing.",
    )


# ------------------------------------------------------------------ installs
def reinstall_same_timestamp(stages: Stages) -> Check:
    is_earliest = pl.col("install_ts") == pl.col("install_ts").min().over("user_id")
    at_earliest = stages.deduped.filter(is_earliest).group_by("user_id").agg(rows=pl.len())
    tied = at_earliest.filter(pl.col("rows") > 1)
    return make_check(
        "reinstall_same_timestamp", "warning", tied.height, "flagged",
        f"{tied.height} users have several different install rows at their earliest timestamp. The spec does "
        "not say which one wins, so one is kept by a fixed tie-break (install_id, then campaign). Flagged "
        "because the campaign credited for these users is a choice.",
        {"tied_rows": tied["rows"].sum()},
    )


def install_id_reused(stages: Stages) -> Check:
    with_id = stages.deduped.filter(pl.col("install_id").is_not_null())
    reused = with_id.filter(pl.col("user_id").n_unique().over("install_id") > 1)
    ids = reused["install_id"].n_unique()
    return make_check(
        "install_id_reused", "warning", reused.height, "flagged",
        f"{reused.height} install rows use an install_id that also appears under another user_id ({ids} "
        "ids). Nothing is merged or removed: cleaning keys on user_id, as the spec says. Question for the "
        "MMP: is install_id meant to be unique?",
        {"install_ids": ids},
    )


def media_source_unrecognised(stages: Stages) -> Check:
    networks = stages.visible.campaigns["network"].unique().to_list()
    is_known = pl.col("media_source").is_in(networks).fill_null(False)  # a missing media_source is not known
    unknown = stages.paid.filter(~is_known)
    return make_check(
        "media_source_unrecognised", "warning", unknown.height, "flagged",
        f"{unknown.height} cleaned non-organic installs have a media_source that is not a network in "
        "campaigns.csv (see values). They stay on the paid side: only the exact value organic is organic, "
        "and a variant such as Organic is not reinterpreted.",
        {"values": _sample(unknown["media_source"])},
    )


def media_source_network_mismatch(stages: Stages) -> Check:
    differs = (pl.col("media_source") != pl.col("network")).fill_null(True)
    mismatched = stages.mapped.filter(differs)
    pair = pl.format("{} -> {}", pl.col("media_source").fill_null(""), pl.col("network"))
    pairs = mismatched.select(pair=pair)
    return make_check(
        "media_source_network_mismatch", "warning", mismatched.height, "flagged",
        f"{mismatched.height} mapped installs have a media_source that differs from the network of their "
        "campaign in campaigns.csv. The summary uses the campaign's network, as the spec says; the rows are "
        "kept and flagged (see pairs: media_source -> network).",
        {"pairs": _sample(pairs["pair"])},
    )


def campaign_reference_ambiguous(stages: Stages) -> Check:
    # Same comparison as installs.map_campaigns: case-insensitive, reference text otherwise as-is.
    name = pl.col("campaign_name").str.to_lowercase()
    named = stages.visible.campaigns.with_columns(name=name).filter(pl.col("name").is_not_null())
    shared = named.filter(pl.col("name").is_duplicated())
    return make_check(
        "campaign_reference_ambiguous", "error", shared.height, "flagged",
        f"{shared.height} rows of campaigns.csv share their campaign name with another id (compared "
        "case-insensitively). Such a name cannot point to one id, so an install carrying only the name is not "
        "matched by guessing.",
        {"names": _sample(shared["name"])},
    )


def install_hour_pileup(stages: Stages) -> Check:
    hours = stages.cleaned.select(hour=pl.col("install_ts").dt.hour())
    hour_22 = hours.filter(pl.col("hour") == 22).height
    hour_23 = hours.filter(pl.col("hour") == 23).height
    piled_up = hour_22 >= PILEUP_MIN_INSTALLS and hour_23 > PILEUP_RATIO * hour_22
    if piled_up:
        summary = (
            f"UTC hour 23 holds {hour_23} cleaned installs against {hour_22} in hour 22. Low confidence: a "
            "question for the MMP, not a finding (are late installs clamped to the end of the day?). Nothing "
            "is changed."
        )
    else:
        summary = (
            f"No pile-up at the end of the UTC day: {hour_23} cleaned installs in hour 23, {hour_22} in "
            "hour 22."
        )
    return make_check(
        "install_hour_pileup", "info", hour_23 if piled_up else 0, "flagged", summary,
        {"hour_22": hour_22, "hour_23": hour_23},
    )


# ------------------------------------------------------------------ spend
def duplicate_spend_rows(stages: Stages) -> Check:
    removed = stages.duplicate_reports
    return make_check(
        "duplicate_spend_rows", "warning", removed, "removed",
        f"{removed} spend rows are exact copies of another row (one report delivered twice). They are "
        "removed before the latest report is chosen and are not counted as restatements.",
    )


def spend_currency_changed(stages: Stages) -> Check:
    # After this sort the first row of a campaign is its earliest-dated report.
    ordered = stages.kept.sort(["campaign_id", "date", "network"])
    ordered = ordered.with_columns(first_currency=pl.col("currency").first().over("campaign_id"))
    changed = ordered.filter(pl.col("currency") != pl.col("first_currency"))
    switches = changed.unique(subset=["campaign_id"], keep="first", maintain_order=True)
    per_campaign = {
        row["campaign_id"]: {
            "from": row["first_currency"], "to": row["currency"], "first_date": row["date"].isoformat(),
        }
        for row in switches.iter_rows(named=True)
    }
    return make_check(
        "spend_currency_changed", "warning", changed.height, "flagged",
        f"{changed.height} kept spend rows are in another currency than their campaign's first report "
        f"({switches.height} campaigns, see metrics). Converted as reported, not corrected. Ask the studio "
        "whether billing really changed or only the label did.",
        per_campaign,
    )


def spend_without_installs(stages: Stages) -> Check:
    has_report = pl.col("spend_reported_at").is_not_null()
    spend_only = stages.summary.filter(has_report & (pl.col("mmp_installs") == 0))
    usd = _cents(spend_only, "spend_usd") / 100
    return make_check(
        "spend_without_installs", "warning", spend_only.height, "flagged",
        f"{spend_only.height} campaign-days have a spend report and no cleaned MMP install ({usd:.2f} USD). "
        "The row is kept with mmp_installs 0 and an empty cpi_usd: the money was spent, so it is not "
        "dropped.",
        {"spend_usd": usd},
    )


def spend_non_positive(stages: Stages) -> Check:
    amount = pl.col("spend_amount")
    empty = stages.kept.filter(amount.is_null()).height
    zero = stages.kept.filter(amount == 0).height
    negative = stages.kept.filter(amount < 0).height
    total = empty + zero + negative
    return make_check(
        "spend_non_positive", "warning", total, "flagged",
        f"{total} kept spend rows have no positive spend: {empty} empty, {zero} zero, {negative} negative. "
        "Kept as reported: zero is a real value, empty stays empty, a negative is not clipped. Ask the "
        "network what a negative day means.",
        {"empty": empty, "zero": zero, "negative": negative},
    )


def spend_unknown_campaign(stages: Stages) -> Check:
    reference = stages.visible.campaigns.select("campaign_id", campaign_network="network")
    joined = stages.kept.join(reference, on="campaign_id", how="left")
    unknown = joined.filter(pl.col("campaign_network").is_null())
    differs = joined.filter(pl.col("campaign_network") != pl.col("network"))
    total = unknown.height + differs.height
    return make_check(
        "spend_unknown_campaign", "error", total, "flagged",
        f"{total} kept spend rows cannot be tied to campaigns.csv: {unknown.height} name an unknown "
        f"campaign_id, {differs.height} a network other than the campaign's. They stay in the summary under "
        "the spend file's network, so the money is not dropped.",
        {
            "unknown_campaign_id": unknown.height,
            "network_differs": differs.height,
            "campaign_ids": _sample(pl.concat([unknown["campaign_id"], differs["campaign_id"]])),
        },
    )


def network_overreports_installs(stages: Stages) -> Check:
    comparable = _comparable(stages.summary)
    over = comparable.filter(pl.col("network_reported_installs") > pl.col("mmp_installs"))
    return make_check(
        "network_overreports_installs", "info", over.height, "none",
        f"On {over.height} of {comparable.height} comparable campaign-days the network reports more installs "
        "than the MMP (total gap per network in metrics). Nothing is changed: the two count under different "
        "attribution rules. Ask which windows apply.",
        {"comparable_rows": comparable.height, "total_gap_pct": _total_gap_pct(comparable)},
    )


def network_day_bucketing_suspected(stages: Stages) -> Check:
    comparable = _comparable(stages.summary)
    totals = _total_gap_pct(comparable)
    # The standard deviation is taken in plain Python over rows in the summary's sorted order, so the
    # float result does not depend on the order a parallel engine would add them up in.
    spread: dict[str, float] = {}
    for network in totals:
        gaps = comparable.filter(pl.col("network") == network)["install_gap_pct"].to_list()
        if len(gaps) >= 2:  # a sample standard deviation needs two values
            spread[network] = statistics.stdev(gaps)

    flagged: list[str] = []
    if len(spread) >= BUCKETING_MIN_NETWORKS:
        typical = statistics.median(spread.values())
        flagged = [
            network for network, value in spread.items()
            if value > BUCKETING_MIN_STD and value > BUCKETING_PEER_RATIO * typical
        ]
    rows = stages.summary.filter(pl.col("network").is_in(flagged)).height
    if flagged:
        summary = (
            f"{len(flagged)} network(s) (see flagged_networks) have a daily install gap that swings widely "
            "while their total gap agrees with the other networks. That points to a network closing its day "
            "in another time zone. Days are NOT shifted: the spec forbids it."
        )
    else:
        summary = (
            "No network's daily install gap swings far more than its peers'. No day is shifted for any "
            "network in any case: the spec forbids it."
        )
    per_network = {
        network: {"daily_gap_std": value, "total_gap_pct": totals[network]}
        for network, value in spread.items()
    }
    return make_check(
        "network_day_bucketing_suspected", "warning", rows, "flagged", summary,
        {"flagged_networks": flagged[:LIST_MAX_ITEMS], "per_network": per_network},
    )


# ------------------------------------------------------------------ events and retention
def events_without_install(stages: Stages) -> Check:
    orphans = stages.visible.events.join(stages.cleaned.select("user_id"), on="user_id", how="anti")
    users = orphans["user_id"].n_unique()
    usd = _cents(orphans, "revenue") / 100
    return make_check(
        "events_without_install", "warning", orphans.height, "flagged",
        f"{orphans.height} events belong to {users} users with no visible install row ({usd:.2f} USD of "
        "revenue). They cannot join a cohort, so retention ignores them; they are left in place and flagged "
        "for the studio (test accounts?).",
        {"users_count": users, "users": _sample(orphans["user_id"]), "revenue_usd": usd},
    )


def events_before_install(stages: Stages) -> Check:
    first_install = stages.cleaned.select("user_id", "install_ts")
    early = stages.visible.events.join(first_install, on="user_id", how="inner").filter(
        pl.col("event_ts") < pl.col("install_ts")
    )
    users = early["user_id"].n_unique()
    seconds = early.select((pl.col("event_ts") - pl.col("install_ts")).dt.total_seconds().min()).item()
    minutes = round(seconds / 60, 2) if seconds is not None else 0.0
    return make_check(
        "events_before_install", "warning", early.height, "flagged",
        f"{early.height} events from {users} users are timestamped before the user's install (earliest "
        f"{minutes} minutes). Kept and flagged: it looks like device clock skew, and such an event can never "
        "fall in a D7 window, so retention is unaffected.",
        {"users_affected": users, "earliest_offset_minutes": minutes},
    )


def duplicate_event_rows(stages: Stages) -> Check:
    events = stages.visible.events
    distinct = events.unique()
    copies = events.height - distinct.height
    usd = (_cents(events, "revenue") - _cents(distinct, "revenue")) / 100
    return make_check(
        "duplicate_event_rows", "warning", copies, "flagged",
        f"{copies} event rows are exact copies of another row. Not removed: retention counts distinct users, "
        f"so it is unaffected, but revenue summed from this file would count {usd:.2f} USD twice.",
        {"revenue_usd_in_copies": usd},
    )


def event_type_unrecognised(stages: Stages) -> Check:
    is_known = pl.col("event_type").is_in(EVENT_TYPES).fill_null(False)
    unknown = stages.visible.events.filter(~is_known)
    return make_check(
        "event_type_unrecognised", "warning", unknown.height, "flagged",
        f"{unknown.height} events have an event_type other than session or purchase (see values). They are "
        "not counted as sessions, so they cannot make a user retained; a case variant is not reinterpreted.",
        {"values": _sample(unknown["event_type"])},
    )


def implausible_purchase_amount(stages: Stages) -> Check:
    is_purchase = pl.col("event_type") == "purchase"
    large = stages.visible.events.filter(is_purchase & (pl.col("revenue") > IMPLAUSIBLE_PURCHASE_USD))
    largest = large["revenue"].max() or 0.0
    return make_check(
        "implausible_purchase_amount", "warning", large.height, "flagged",
        f"{large.height} purchases are above {IMPLAUSIBLE_PURCHASE_USD:.0f} USD (largest {largest:.2f}). Not "
        "capped, not deleted: revenue is in no graded output. Ask the studio whether it is a test account or "
        "cents entered as dollars.",
        {"max_revenue_usd": largest, "users": _sample(large["user_id"])},
    )


def negative_revenue_events(stages: Stages) -> Check:
    negative = stages.visible.events.filter(pl.col("revenue") < 0)
    usd = _cents(negative, "revenue") / 100
    return make_check(
        "negative_revenue_events", "info", negative.height, "none",
        f"{negative.height} events have negative revenue (total {usd:.2f} USD). They look like refunds and "
        "are kept as they are, so revenue summed from the file is net of them. Ask the studio to confirm.",
        {"total_usd": usd},
    )


def immature_cohorts(stages: Stages) -> Check:
    # At most two weeks can be immature (a week matures 13 days after its Monday), so the list is short.
    weeks = stages.retention.filter(~pl.col("is_mature"))["install_week"].sort().to_list()
    return make_check(
        "immature_cohorts", "info", len(weeks), "flagged",
        f"{len(weeks)} install weeks are not mature at as_of: some of their users have not had a full D7 "
        "window yet. They are written with is_mature = false; their retention will still rise and must not "
        "be read as churn.",
        {"weeks": [week.isoformat() for week in weeks]},
    )
