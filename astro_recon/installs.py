"""Install cleaning: the five rules of ASSIGNMENT.md "Install cleaning", applied in the spec's order.

Every function is pure: it takes a frame and returns new frames (plus the row count the quality report
needs), so the number in the report is the same number the unit tests assert on.

Input contract: the seven raw string columns exactly as they were in the file, plus `install_ts`, the
parsed UTC timestamp (naive Datetime).
"""

from __future__ import annotations

import polars as pl

RAW_COLUMNS = ["install_id", "user_id", "install_ts_utc", "platform", "country", "media_source", "campaign"]
ORGANIC = "organic"

# Rule 2 tie-break. The spec does not say which row wins when one user has two installs with the same
# timestamp, so we pick one deterministically: install_id, then raw campaign, then every other raw column,
# which makes the order total (rule 1 already removed rows where all seven columns are equal).
TIE_BREAK = ["install_id", "campaign", "media_source", "platform", "country", "install_ts_utc"]


def dedupe_exact(installs: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Rule 1: identical rows count once. Returns (deduplicated frame, rows removed).

    "Identical" means all seven raw strings are equal, compared before any trimming, so `CAMP-001` and
    `CAMP-001 ` are two different rows here. Polars treats null as equal to null in `unique`.
    """
    deduped = installs.unique(subset=RAW_COLUMNS, keep="first", maintain_order=True)
    return deduped, installs.height - deduped.height


def keep_earliest_per_user(installs: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Rule 2: one row per user, the earliest install. Returns (frame, rows removed).

    Sorting first makes "the first row of each user" mean "the earliest", and makes the result independent
    of the order of the input file. `nulls_last` means a missing timestamp never beats a real one.
    """
    ordered = installs.sort(["user_id", "install_ts"] + TIE_BREAK, nulls_last=True)
    earliest = ordered.unique(subset=["user_id"], keep="first", maintain_order=True)
    return earliest, installs.height - earliest.height


def split_organic(installs: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Rule 3: returns (paid, organic).

    The match is exact, as the spec writes it: `Organic` or a null media_source is paid. A null comparison
    is null in Polars, so it is turned into False to keep every row on exactly one side.
    """
    is_organic = (pl.col("media_source") == ORGANIC).fill_null(False)
    return installs.filter(~is_organic), installs.filter(is_organic)


def map_campaigns(paid: pl.DataFrame, campaigns: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Rules 4-5: returns (mapped, unmapped).

    `mapped` is the paid frame plus `campaign_id` and `network`; `unmapped` is the paid columns only.
    The raw `campaign` column is left untouched in both.

    The reference table is small, so it is turned into plain lookups instead of joins. A lookup returns at
    most one value per row, so a repeated campaign name can never duplicate an install row.
    """
    known_ids = campaigns["campaign_id"].to_list()
    network_of = dict(zip(campaigns["campaign_id"], campaigns["network"]))

    # Names are compared case-insensitively. A name used by two ids is ambiguous: we do not guess which
    # campaign was meant, so that name is left out of the lookup and its rows end up unmapped.
    names = campaigns.select(name=pl.col("campaign_name").str.to_lowercase(), campaign_id="campaign_id")
    names = names.filter(pl.col("name").is_not_null() & pl.col("name").is_unique())
    id_of_name = dict(zip(names["name"], names["campaign_id"]))

    trimmed = pl.col("campaign").str.strip_chars()
    as_id = trimmed.str.to_uppercase()
    by_id = pl.when(as_id.is_in(known_ids)).then(as_id)
    by_name = trimmed.str.to_lowercase().replace_strict(id_of_name, default=None, return_dtype=pl.String)

    # coalesce takes the first non-null value: the id match has priority over the name match.
    resolved = paid.with_columns(campaign_id=pl.coalesce(by_id, by_name))
    # Rule 5: the network is the campaign's network in campaigns.csv, not the row's media_source.
    resolved = resolved.with_columns(
        network=pl.col("campaign_id").replace_strict(network_of, default=None, return_dtype=pl.String)
    )

    mapped = resolved.filter(pl.col("campaign_id").is_not_null())
    unmapped = resolved.filter(pl.col("campaign_id").is_null()).select(paid.columns)
    return mapped, unmapped


def count_normalised(mapped: pl.DataFrame) -> int:
    """Mapped rows whose raw `campaign` text is not already the final id (`campaign_id_normalised`)."""
    return mapped.filter(pl.col("campaign") != pl.col("campaign_id")).height
