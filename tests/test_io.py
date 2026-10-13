"""Loading: what stops the run, what drops a row, and what must come through untouched."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from astro_recon.io import InputError, load_inputs

# One small valid folder. Each test replaces only the file it is about.
GOOD = {
    "mmp_installs": (
        "install_id,user_id,install_ts_utc,platform,country,media_source,campaign\n"
        "i1,u1,2026-05-01T10:00:00Z,ios,US,metaads,CAMP-001\n"
    ),
    "mmp_events": (
        "user_id,event_ts_utc,event_type,revenue_usd\n"
        "u1,2026-05-08T10:00:00Z,session,\n"
        "u1,2026-05-08T11:00:00Z,purchase,4.99\n"
    ),
    "network_spend": (
        "stat_date,network,campaign_id,spend,currency,reported_installs,reported_at\n"
        "2026-05-01,metaads,CAMP-001,10.50,USD,3,2026-05-02T06:00:00Z\n"
    ),
    "campaigns": "campaign_id,campaign_name,network\nCAMP-001,Astro US Broad,metaads\n",
    "fx_rates": "date,currency,usd_per_unit\n2026-05-01,EUR,1.08\n",
}

CONTRACT = {
    "installs": {
        "install_id": pl.String, "user_id": pl.String, "install_ts_utc": pl.String, "platform": pl.String,
        "country": pl.String, "media_source": pl.String, "campaign": pl.String,
        "install_ts": pl.Datetime("us"),
    },
    "events": {
        "user_id": pl.String, "event_ts": pl.Datetime("us"), "event_type": pl.String, "revenue": pl.Float64,
    },
    "spend": {
        "date": pl.Date, "network": pl.String, "campaign_id": pl.String, "spend_amount": pl.Float64,
        "currency": pl.String, "installs_reported": pl.Int64, "reported_ts": pl.Datetime("us"),
    },
    "campaigns": {"campaign_id": pl.String, "campaign_name": pl.String, "network": pl.String},
    "fx": {"date": pl.Date, "currency": pl.String, "rate": pl.Float64},
}


def make_data_dir(folder: Path, **files: str | bytes | None) -> Path:
    """Write GOOD into `folder`, with the given files replaced (or left out when None)."""
    for name, content in {**GOOD, **files}.items():
        if content is None:
            continue
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        (folder / f"{name}.csv").write_bytes(data)  # bytes, so Windows does not rewrite the line endings
    return folder


def schemas(inputs) -> dict[str, dict]:
    return {name: dict(getattr(inputs, name).schema) for name in CONTRACT}


# ------------------------------------------------------------------ errors that stop the run
def test_data_path_that_does_not_exist_or_is_a_file_is_an_input_error(tmp_path):
    with pytest.raises(InputError, match="does-not-exist"):
        load_inputs(tmp_path / "does-not-exist")
    a_file = tmp_path / "data.csv"
    a_file.write_text("x\n")
    with pytest.raises(InputError, match="not a directory"):
        load_inputs(a_file)


def test_every_missing_file_is_named_in_one_message(tmp_path):
    data = make_data_dir(tmp_path, mmp_events=None, fx_rates=None)
    with pytest.raises(InputError) as error:
        load_inputs(data)
    assert "mmp_events.csv" in str(error.value) and "fx_rates.csv" in str(error.value)
    assert "campaigns.csv" not in str(error.value)


def test_missing_required_column_names_the_file_and_the_column(tmp_path):
    spend = (
        "stat_date,network,campaign_id,spend,currency,reported_installs\n"
        "2026-05-01,metaads,CAMP-001,1,USD,3\n"
    )
    with pytest.raises(InputError) as error:
        load_inputs(make_data_dir(tmp_path, network_spend=spend))
    assert "network_spend.csv" in str(error.value) and "reported_at" in str(error.value)


def test_zero_byte_file_is_an_input_error_not_a_traceback(tmp_path):
    with pytest.raises(InputError, match="campaigns.csv"):
        load_inputs(make_data_dir(tmp_path, campaigns=b""))


def test_duplicate_campaign_id_stops_the_run(tmp_path):
    campaigns = "campaign_id,campaign_name,network\nCAMP-001,Astro US Broad,metaads\nCAMP-001,Other,adglow\n"
    with pytest.raises(InputError, match="campaign_id is not unique"):
        load_inputs(make_data_dir(tmp_path, campaigns=campaigns))


# ------------------------------------------------------------------ file shapes that must be accepted
def test_dtypes_match_the_frame_contract(tmp_path):
    inputs = load_inputs(make_data_dir(tmp_path))
    assert schemas(inputs) == CONTRACT
    assert inputs.installs["install_ts"].to_list() == [datetime(2026, 5, 1, 10, 0, 0)]
    assert inputs.spend.row(0) == (
        date(2026, 5, 1), "metaads", "CAMP-001", 10.5, "USD", 3, datetime(2026, 5, 2, 6, 0, 0)
    )
    assert inputs.fx.row(0) == (date(2026, 5, 1), "EUR", 1.08)
    assert inputs.rejected_rows == {
        "mmp_installs": 0, "mmp_events": 0, "network_spend": 0, "campaigns": 0, "fx_rates": 0,
    }


def test_header_only_files_give_empty_frames_with_the_same_dtypes(tmp_path):
    headers = {name: text.splitlines()[0] + "\n" for name, text in GOOD.items()}
    inputs = load_inputs(make_data_dir(tmp_path, **headers))
    assert schemas(inputs) == CONTRACT
    assert all(getattr(inputs, name).height == 0 for name in CONTRACT)
    assert sum(inputs.rejected_rows.values()) == 0


def test_extra_and_reordered_columns_are_selected_by_name(tmp_path):
    campaigns = "network,owner,campaign_name,campaign_id\nmetaads,sam,Astro US Broad,CAMP-001\n"
    inputs = load_inputs(make_data_dir(tmp_path, campaigns=campaigns))
    assert inputs.campaigns.columns == ["campaign_id", "campaign_name", "network"]
    assert inputs.campaigns.row(0) == ("CAMP-001", "Astro US Broad", "metaads")


def test_bom_and_padded_header_names_are_tolerated(tmp_path):
    campaigns = b"\xef\xbb\xbfcampaign_id , campaign_name, network \nCAMP-001,Astro US Broad,metaads\n"
    inputs = load_inputs(make_data_dir(tmp_path, campaigns=campaigns))
    assert inputs.campaigns.row(0) == ("CAMP-001", "Astro US Broad", "metaads")


def test_crlf_leaves_no_carriage_return_and_keeps_real_trailing_spaces(tmp_path):
    installs = (
        "install_id,user_id,install_ts_utc,platform,country,media_source,campaign\r\n"
        "i1,u1,2026-05-01T10:00:00Z,ios,US,metaads,CAMP-001\r\n"
        "i2,u2,2026-05-01T11:00:00Z,ios,US,metaads,CAMP-001 \r\n"
    )
    spend = GOOD["network_spend"].replace("\n", "\r\n")
    events = GOOD["mmp_events"].replace("\n", "\r\n")
    data = make_data_dir(tmp_path, mmp_installs=installs, network_spend=spend, mmp_events=events)
    inputs = load_inputs(data)
    assert inputs.installs["campaign"].to_list() == ["CAMP-001", "CAMP-001 "]
    # The last column of the other two files is a timestamp and a number: a stray \r would reject every row.
    assert inputs.spend.height == 1 and inputs.events["revenue"].to_list() == [None, 4.99]
    assert sum(inputs.rejected_rows.values()) == 0


def test_quoted_field_with_a_comma_is_one_value(tmp_path):
    campaigns = 'campaign_id,campaign_name,network\nCAMP-001,"Astro US, Broad",metaads\n'
    inputs = load_inputs(make_data_dir(tmp_path, campaigns=campaigns))
    assert inputs.campaigns["campaign_name"].to_list() == ["Astro US, Broad"]


def test_fully_quoted_file_reads_an_empty_quoted_field_as_null(tmp_path):
    events = (
        '"user_id","event_ts_utc","event_type","revenue_usd"\n'
        '"u1","2026-05-08T10:00:00Z","session",""\n'
    )
    inputs = load_inputs(make_data_dir(tmp_path, mmp_events=events))
    assert inputs.events["revenue"].to_list() == [None]
    assert inputs.rejected_rows["mmp_events"] == 0


def test_blank_lines_are_skipped_and_not_counted_as_rejected(tmp_path):
    events = "user_id,event_ts_utc,event_type,revenue_usd\n\nu1,2026-05-08T10:00:00Z,session,\n\n"
    inputs = load_inputs(make_data_dir(tmp_path, mmp_events=events))
    assert inputs.events.height == 1 and inputs.rejected_rows["mmp_events"] == 0


def test_nothing_is_inferred_from_the_values(tmp_path):
    """NA is Namibia, not a null; an id made of digits keeps its leading zeros."""
    installs = (
        "install_id,user_id,install_ts_utc,platform,country,media_source,campaign\n"
        "0012,007,2026-05-01T10:00:00Z,ios,NA,organic,\n"
    )
    row = load_inputs(make_data_dir(tmp_path, mmp_installs=installs)).installs.row(0, named=True)
    assert (row["install_id"], row["user_id"], row["country"], row["campaign"]) == ("0012", "007", "NA", None)


# ------------------------------------------------------------------ rows that are dropped and counted
def test_unparseable_values_drop_the_row_but_an_empty_optional_value_does_not(tmp_path):
    events = (
        "user_id,event_ts_utc,event_type,revenue_usd\n"
        "u1,2026-05-08T10:00:00Z,session,\n"            # empty revenue on a session: kept as null
        "u2,2026-05-08 10:00:00,session,\n"             # timestamp in another format
        "u3,2026-02-30T10:00:00Z,session,\n"            # not a real date
        "u4,2026-05-08T10:00:00Z,purchase,4.99 USD\n"   # not a number
        ",2026-05-08T10:00:00Z,session,\n"              # no user
        "u6,2026-05-08T12:00:00Z,purchase,-1.5\n"       # a refund is still a number
    )
    inputs = load_inputs(make_data_dir(tmp_path, mmp_events=events))
    assert inputs.events["user_id"].to_list() == ["u1", "u6"]
    assert inputs.events["revenue"].to_list() == [None, -1.5]
    assert inputs.rejected_rows["mmp_events"] == 4
    assert inputs.rejected_rows["mmp_installs"] == 0


def test_spend_that_is_not_a_finite_number_is_rejected_and_empty_spend_is_kept(tmp_path):
    header = "stat_date,network,campaign_id,spend,currency,reported_installs,reported_at\n"
    spend = header + (
        "2026-05-01,metaads,CAMP-001,nan,USD,3,2026-05-02T06:00:00Z\n"
        "2026-05-02,metaads,CAMP-001,inf,USD,3,2026-05-03T06:00:00Z\n"
        "2026-05-03,metaads,CAMP-001,,USD,,2026-05-04T06:00:00Z\n"
        "2026-05-04,metaads,CAMP-001,0,USD,0,2026-05-05T06:00:00Z\n"
        "2026-05-05,metaads,CAMP-001,5,USD,many,2026-05-06T06:00:00Z\n"
        "2026-05-06,metaads,CAMP-001,5,,1,2026-05-07T06:00:00Z\n"
    )
    inputs = load_inputs(make_data_dir(tmp_path, network_spend=spend))
    assert inputs.spend["date"].to_list() == [date(2026, 5, 3), date(2026, 5, 4)]
    assert inputs.spend["spend_amount"].to_list() == [None, 0.0]
    assert inputs.spend["installs_reported"].to_list() == [None, 0]
    assert inputs.rejected_rows["network_spend"] == 4


def test_reported_installs_written_as_a_whole_float_is_kept_and_a_fraction_is_rejected(tmp_path):
    """`59.0` is a count some exports write as a float; dropping the row would lose its spend too."""
    header = "stat_date,network,campaign_id,spend,currency,reported_installs,reported_at\n"
    spend = header + (
        "2026-05-01,metaads,CAMP-001,5,USD,59.0,2026-05-02T06:00:00Z\n"
        "2026-05-02,metaads,CAMP-001,5,USD,59,2026-05-03T06:00:00Z\n"
        "2026-05-03,metaads,CAMP-001,5,USD,59.5,2026-05-04T06:00:00Z\n"
        "2026-05-04,metaads,CAMP-001,5,USD,nan,2026-05-05T06:00:00Z\n"
    )
    inputs = load_inputs(make_data_dir(tmp_path, network_spend=spend))
    assert inputs.spend["date"].to_list() == [date(2026, 5, 1), date(2026, 5, 2)]
    assert inputs.spend["installs_reported"].to_list() == [59, 59]
    assert inputs.spend.schema["installs_reported"] == pl.Int64
    assert inputs.rejected_rows["network_spend"] == 2


def test_campaign_reference_row_without_id_or_network_is_dropped_and_counted(tmp_path):
    campaigns = (
        "campaign_id,campaign_name,network\n"
        "CAMP-001,Astro US Broad,metaads\n"
        ",No Id,metaads\n"
        "CAMP-003,No Network,\n"
    )
    inputs = load_inputs(make_data_dir(tmp_path, campaigns=campaigns))
    assert inputs.campaigns["campaign_id"].to_list() == ["CAMP-001"]
    assert inputs.rejected_rows["campaigns"] == 2


def test_fx_rate_must_be_positive_and_unambiguous(tmp_path):
    fx = (
        "date,currency,usd_per_unit\n"
        "2026-05-01,EUR,1.08\n"
        "2026-05-01,EUR,1.08\n"   # identical copy: collapsed, not counted
        "2026-05-02,EUR,0\n"      # zero: unusable
        "2026-05-03,EUR,-1.1\n"   # negative: unusable
        "2026-05-04,EUR,1.08\n"   # two different rates for one key:
        "2026-05-04,EUR,1.09\n"   # neither is used
        "2026-05-04,GBP,1.25\n"   # same date, other currency: fine
        "2026-05-05,EUR,abc\n"    # not a number
    )
    inputs = load_inputs(make_data_dir(tmp_path, fx_rates=fx))
    assert inputs.fx.rows() == [(date(2026, 5, 1), "EUR", 1.08), (date(2026, 5, 4), "GBP", 1.25)]
    assert inputs.rejected_rows["fx_rates"] == 5
