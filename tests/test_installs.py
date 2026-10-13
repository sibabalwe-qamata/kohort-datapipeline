"""Unit tests for install cleaning, one rule of ASSIGNMENT.md "Install cleaning" per section."""

from __future__ import annotations

import polars as pl
import pytest

from astro_recon import installs

DEFAULT_ROW = {
    "install_id": "i1",
    "user_id": "u1",
    "install_ts_utc": "2026-05-04T09:00:00Z",
    "platform": "android",
    "country": "US",
    "media_source": "metaads",
    "campaign": "CAMP-001",
}


def make_installs(*rows: dict) -> pl.DataFrame:
    """Installs frame as the loader delivers it: seven raw strings plus the parsed `install_ts`.

    Each row only names the fields the test cares about; the rest come from DEFAULT_ROW.
    """
    full_rows = [{**DEFAULT_ROW, **row} for row in rows]
    frame = pl.DataFrame(full_rows, schema={column: pl.String for column in installs.RAW_COLUMNS})
    return frame.with_columns(
        install_ts=pl.col("install_ts_utc").str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_unit="us")
    )


def make_campaigns(*rows: tuple[str, str, str]) -> pl.DataFrame:
    """Campaign reference: (campaign_id, campaign_name, network)."""
    return pl.DataFrame(
        list(rows),
        schema={"campaign_id": pl.String, "campaign_name": pl.String, "network": pl.String},
        orient="row",
    )


CAMPAIGNS = make_campaigns(
    ("CAMP-001", "Astro US Broad", "metaads"),
    ("CAMP-004", "Galaxy Blast US", "googleuac"),
)


# --- Rule 1: identical rows count once -------------------------------------------------------------


def test_exact_duplicate_collapses_to_one_row():
    frame = make_installs({"user_id": "u1"}, {"user_id": "u1"}, {"user_id": "u2", "install_id": "i2"})
    deduped, removed = installs.dedupe_exact(frame)
    assert removed == 1
    assert deduped["user_id"].to_list() == ["u1", "u2"]


def test_three_copies_remove_two():
    deduped, removed = installs.dedupe_exact(make_installs({}, {}, {}))
    assert removed == 2
    assert deduped.height == 1


def test_trailing_space_is_not_an_exact_duplicate():
    # Rule 1 compares the raw text; trimming only happens in rule 4.
    frame = make_installs({"campaign": "CAMP-001"}, {"campaign": "CAMP-001 "})
    deduped, removed = installs.dedupe_exact(frame)
    assert removed == 0
    assert deduped.height == 2


def test_duplicates_with_null_fields_collapse():
    organic = {"media_source": "organic", "campaign": None, "country": None}
    deduped, removed = installs.dedupe_exact(make_installs(organic, organic))
    assert removed == 1
    assert deduped.height == 1


# --- Rule 2: earliest install per user -------------------------------------------------------------

EARLY = {"install_id": "i_early", "install_ts_utc": "2026-05-04T09:00:00Z"}
LATE = {"install_id": "i_late", "install_ts_utc": "2026-05-09T09:00:00Z"}


@pytest.mark.parametrize("rows", [(EARLY, LATE), (LATE, EARLY)])
def test_reinstall_keeps_earliest_whatever_the_input_order(rows):
    kept, removed = installs.keep_earliest_per_user(make_installs(*rows))
    assert removed == 1
    assert kept["install_id"].to_list() == ["i_early"]


def test_three_distinct_installs_of_one_user_remove_two():
    middle = {"install_id": "i_mid", "install_ts_utc": "2026-05-06T09:00:00Z"}
    other_user = {"user_id": "u2", "install_id": "i_other"}
    kept, removed = installs.keep_earliest_per_user(make_installs(LATE, middle, EARLY, other_user))
    assert removed == 2
    assert kept.sort("user_id")["install_id"].to_list() == ["i_early", "i_other"]


def test_same_timestamp_tie_does_not_depend_on_input_order():
    first = {"install_id": "i_a", "campaign": "CAMP-004"}
    second = {"install_id": "i_b", "campaign": "CAMP-001"}
    kept_ab, removed_ab = installs.keep_earliest_per_user(make_installs(first, second))
    kept_ba, removed_ba = installs.keep_earliest_per_user(make_installs(second, first))
    assert removed_ab == removed_ba == 1
    assert kept_ab["install_id"].to_list() == ["i_a"]  # lowest install_id wins the tie
    assert kept_ab.equals(kept_ba)


def test_same_timestamp_and_install_id_falls_back_to_raw_campaign():
    plain = {"campaign": "CAMP-007"}
    padded = {"campaign": "CAMP-007 "}
    kept_a, _ = installs.keep_earliest_per_user(make_installs(plain, padded))
    kept_b, _ = installs.keep_earliest_per_user(make_installs(padded, plain))
    assert kept_a["campaign"].to_list() == kept_b["campaign"].to_list() == ["CAMP-007"]


# --- Rule 3: organic split (after rule 2) ----------------------------------------------------------


def test_user_whose_earliest_install_is_organic_is_organic():
    organic_first = {**EARLY, "media_source": "organic", "campaign": None}
    paid_later = {**LATE, "media_source": "metaads", "campaign": "CAMP-001"}
    earliest, _ = installs.keep_earliest_per_user(make_installs(paid_later, organic_first))
    paid, organic = installs.split_organic(earliest)
    assert organic["user_id"].to_list() == ["u1"]
    assert paid.height == 0


def test_user_whose_earliest_install_is_paid_is_paid():
    paid_first = {**EARLY, "media_source": "metaads", "campaign": "CAMP-001"}
    organic_later = {**LATE, "media_source": "organic", "campaign": None}
    earliest, _ = installs.keep_earliest_per_user(make_installs(organic_later, paid_first))
    paid, organic = installs.split_organic(earliest)
    assert paid["user_id"].to_list() == ["u1"]
    assert organic.height == 0


def test_organic_row_with_a_campaign_value_is_still_organic():
    paid, organic = installs.split_organic(make_installs({"media_source": "organic", "campaign": "CAMP-001"}))
    assert organic.height == 1
    assert paid.height == 0


def test_only_the_exact_word_organic_is_organic():
    frame = make_installs(
        {"user_id": "u1", "media_source": "Organic"},
        {"user_id": "u2", "media_source": " organic"},
        {"user_id": "u3", "media_source": None},
        {"user_id": "u4", "media_source": "organic"},
    )
    paid, organic = installs.split_organic(frame)
    assert paid["user_id"].to_list() == ["u1", "u2", "u3"]
    assert organic["user_id"].to_list() == ["u4"]


# --- Rules 4-5: campaign mapping and network lookup ------------------------------------------------


@pytest.mark.parametrize("raw", ["CAMP-001", "camp-001", "CAMP-001 ", " camp-001 "])
def test_id_variants_map_to_the_canonical_id(raw):
    mapped, unmapped = installs.map_campaigns(make_installs({"campaign": raw}), CAMPAIGNS)
    assert mapped["campaign_id"].to_list() == ["CAMP-001"]
    assert mapped["campaign"].to_list() == [raw]  # the raw value is kept untouched
    assert unmapped.height == 0


def test_campaign_name_in_any_case_with_padding_maps_to_its_id():
    mapped, unmapped = installs.map_campaigns(make_installs({"campaign": "  gALAXY blast us "}), CAMPAIGNS)
    assert mapped["campaign_id"].to_list() == ["CAMP-004"]
    assert mapped["network"].to_list() == ["googleuac"]
    assert unmapped.height == 0


@pytest.mark.parametrize("raw", ["CAMP-011", "CAMP_001", "CAMP-1", "Galaxy  Blast US", "", "   ", None])
def test_near_misses_and_empty_values_are_unmapped(raw):
    paid = make_installs({"campaign": raw})
    mapped, unmapped = installs.map_campaigns(paid, CAMPAIGNS)
    assert mapped.height == 0
    assert unmapped["campaign"].to_list() == [raw]
    assert unmapped.columns == paid.columns


def test_id_match_beats_name_match():
    # CAMP-002 is *named* like CAMP-001's id. The id match is tried first.
    campaigns = make_campaigns(("CAMP-001", "Astro US Broad", "metaads"), ("CAMP-002", "camp-001", "tikboom"))
    mapped, _ = installs.map_campaigns(make_installs({"campaign": "camp-001"}), campaigns)
    assert mapped["campaign_id"].to_list() == ["CAMP-001"]
    assert mapped["network"].to_list() == ["metaads"]


def test_ambiguous_campaign_name_is_unmapped_and_never_duplicates_rows():
    campaigns = make_campaigns(("CAMP-001", "Shared Name", "metaads"), ("CAMP-002", "shared name", "tikboom"))
    paid = make_installs(
        {"user_id": "u1", "campaign": "Shared Name"},
        {"user_id": "u2", "campaign": "CAMP-002"},
    )
    mapped, unmapped = installs.map_campaigns(paid, campaigns)
    assert unmapped["user_id"].to_list() == ["u1"]
    assert mapped["user_id"].to_list() == ["u2"]  # the id still works when the name is ambiguous


def test_network_comes_from_campaigns_not_from_media_source():
    paid = make_installs({"media_source": "tikboom", "campaign": "CAMP-001"})
    mapped, _ = installs.map_campaigns(paid, CAMPAIGNS)
    assert mapped["network"].to_list() == ["metaads"]
    assert mapped["media_source"].to_list() == ["tikboom"]


def test_count_normalised_counts_only_mapped_rows_whose_raw_text_differs():
    paid = make_installs(
        {"user_id": "u1", "campaign": "CAMP-001"},  # already canonical: not counted
        {"user_id": "u2", "campaign": "camp-001"},
        {"user_id": "u3", "campaign": "CAMP-001 "},
        {"user_id": "u4", "campaign": "Astro US Broad"},
        {"user_id": "u5", "campaign": "CAMP-999"},  # unmapped: not counted
    )
    mapped, unmapped = installs.map_campaigns(paid, CAMPAIGNS)
    assert installs.count_normalised(mapped) == 3
    assert unmapped.height == 1


def test_every_paid_row_ends_up_mapped_or_unmapped_exactly_once():
    paid = make_installs(
        {"user_id": "u1", "campaign": "CAMP-001"},
        {"user_id": "u2", "campaign": "galaxy blast us"},
        {"user_id": "u3", "campaign": "CAMP-999"},
        {"user_id": "u4", "campaign": None},
    )
    mapped, unmapped = installs.map_campaigns(paid, CAMPAIGNS)
    assert mapped.height + unmapped.height == paid.height
    assert sorted(mapped["user_id"].to_list() + unmapped["user_id"].to_list()) == ["u1", "u2", "u3", "u4"]
    assert mapped.columns == paid.columns + ["campaign_id", "network"]
