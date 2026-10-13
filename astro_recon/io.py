"""Read the five input files as text, check their shape, and cast to real types.

Nothing is inferred. Every column is read as a string and cast here, so ids are never
turned into numbers and a long run of empty values never decides a column's type.
A row whose value will not cast is dropped and counted; it is never kept as a silent null.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DATE_FORMAT = "%Y-%m-%d"

# File stem -> the columns the pipeline needs from it. Extra columns are ignored.
REQUIRED_COLUMNS = {
    "mmp_installs": [
        "install_id", "user_id", "install_ts_utc", "platform", "country", "media_source", "campaign",
    ],
    "mmp_events": ["user_id", "event_ts_utc", "event_type", "revenue_usd"],
    "network_spend": [
        "stat_date", "network", "campaign_id", "spend", "currency", "reported_installs", "reported_at",
    ],
    "campaigns": ["campaign_id", "campaign_name", "network"],
    "fx_rates": ["date", "currency", "usd_per_unit"],
}

# A parser takes a column name and returns an expression that is null where the text will not parse.
Parser = Callable[[str], pl.Expr]


class InputError(Exception):
    """The input cannot be processed. The CLI reports it on stderr and exits non-zero."""


@dataclass(frozen=True)
class Inputs:
    """The five files as typed frames, plus how many rows each file lost to unusable values."""

    installs: pl.DataFrame
    events: pl.DataFrame
    spend: pl.DataFrame
    campaigns: pl.DataFrame
    fx: pl.DataFrame
    rejected_rows: dict[str, int]


def load_inputs(data_dir: Path) -> Inputs:
    """Load and type all five files, or raise InputError naming what is wrong with the folder."""
    if not data_dir.is_dir():
        raise InputError(f"--data {data_dir} is not a directory")
    missing = [f"{name}.csv" for name in REQUIRED_COLUMNS if not (data_dir / f"{name}.csv").is_file()]
    if missing:
        raise InputError(f"missing input file(s) in {data_dir}: {', '.join(missing)}")

    raw = {name: _read_text(data_dir / f"{name}.csv", columns) for name, columns in REQUIRED_COLUMNS.items()}

    installs, bad_installs = _typed(
        raw["mmp_installs"],
        {"install_ts": ("install_ts_utc", _timestamp)},
        required=["user_id", "install_ts_utc"],
    )
    events, bad_events = _typed(
        raw["mmp_events"],
        {"event_ts": ("event_ts_utc", _timestamp), "revenue": ("revenue_usd", _number)},
        required=["user_id", "event_ts_utc"],
    )
    spend, bad_spend = _typed(
        raw["network_spend"],
        {
            "date": ("stat_date", _date),
            "spend_amount": ("spend", _number),
            "installs_reported": ("reported_installs", _integer),
            "reported_ts": ("reported_at", _timestamp),
        },
        required=["stat_date", "network", "campaign_id", "currency", "reported_at"],
    )
    campaigns, bad_campaigns = _usable_campaigns(raw["campaigns"])
    fx, bad_fx = _typed(
        raw["fx_rates"],
        {"fx_date": ("date", _date), "rate": ("usd_per_unit", _number)},
        required=["date", "currency", "usd_per_unit"],
    )
    fx, bad_rates = _usable_rates(fx)

    return Inputs(
        installs=installs,
        events=events.select("user_id", "event_ts", "event_type", "revenue"),
        spend=spend.select(
            "date", "network", "campaign_id", "spend_amount", "currency", "installs_reported", "reported_ts"
        ),
        campaigns=campaigns,
        fx=fx,
        rejected_rows={
            "mmp_installs": bad_installs,
            "mmp_events": bad_events,
            "network_spend": bad_spend,
            "campaigns": bad_campaigns,
            "fx_rates": bad_fx + bad_rates,
        },
    )


def _read_text(path: Path, columns: list[str]) -> pl.DataFrame:
    """The required columns of one file, every value a string or null, blank lines left out."""
    try:
        # infer_schema_length=0 switches type inference off: every column is a string.
        # null_values="" makes a quoted empty field ("") null, the same as an unquoted empty one.
        df = pl.read_csv(path, infer_schema_length=0, null_values="")
        df = df.rename({name: name.strip() for name in df.columns})
    except (pl.exceptions.PolarsError, OSError) as exc:
        reason = (str(exc).splitlines() or [type(exc).__name__])[0]
        raise InputError(f"{path.name}: cannot be read as CSV ({reason})") from exc
    missing = [name for name in columns if name not in df.columns]
    if missing:
        raise InputError(f"{path.name}: missing required column(s): {', '.join(missing)}")
    # Polars reads a blank line as a row of nulls. That is not data, so it is not counted as rejected.
    return df.filter(pl.any_horizontal(pl.all().is_not_null())).select(columns)


def _timestamp(column: str) -> pl.Expr:
    # The literal Z in the format yields a zone-less datetime holding the UTC wall clock.
    return pl.col(column).str.to_datetime(TS_FORMAT, time_unit="us", strict=False)


def _date(column: str) -> pl.Expr:
    return pl.col(column).str.to_date(DATE_FORMAT, strict=False)


def _number(column: str) -> pl.Expr:
    value = pl.col(column).cast(pl.Float64, strict=False)
    return pl.when(value.is_finite()).then(value)  # "nan" and "inf" cast, but they are not amounts


def _integer(column: str) -> pl.Expr:
    """A whole number. `59.0` is 59 (some exports write counts as floats); `59.5` and text are null."""
    as_integer = pl.col(column).cast(pl.Int64, strict=False)
    as_float = pl.col(column).cast(pl.Float64, strict=False)
    is_whole = as_float.is_finite() & (as_float == as_float.floor())
    # The plain integer cast is tried first because it is exact; the float route only rescues "59.0".
    return pl.coalesce(as_integer, pl.when(is_whole).then(as_float).cast(pl.Int64, strict=False))


def _typed(
    df: pl.DataFrame, parsed: dict[str, tuple[str, Parser]], required: list[str]
) -> tuple[pl.DataFrame, int]:
    """Add the parsed columns; drop rows where a value failed to parse or a required one is empty.

    `parsed` maps a new column name to (source column, parser). An empty source value stays null
    and the row is kept, unless that column is listed in `required`.
    """
    out = df.with_columns([parser(source).alias(new) for new, (source, parser) in parsed.items()])
    failed = pl.any_horizontal(
        [pl.col(source).is_not_null() & pl.col(new).is_null() for new, (source, _) in parsed.items()]
    )
    empty = pl.any_horizontal([pl.col(name).is_null() for name in required])
    good = out.filter(~(failed | empty))
    return good, out.height - good.height


def _usable_campaigns(campaigns: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """Reference rows that have an id and a network. A repeated id stops the run."""
    usable = campaigns.drop_nulls(["campaign_id", "network"])
    if usable["campaign_id"].is_duplicated().any():
        raise InputError("campaigns.csv: campaign_id is not unique, so every join on it would duplicate rows")
    return usable, campaigns.height - usable.height


def _usable_rates(fx: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """One positive rate per (date, currency), and how many rows could not be used.

    Identical copies of a rate are collapsed and not counted: they agree. A key with two different
    rates has no usable rate, because choosing one would be a guess; each of those rates is counted.
    """
    rates = fx.select(pl.col("fx_date").alias("date"), "currency", "rate")
    positive = rates.filter(pl.col("rate") > 0)
    distinct = positive.unique(maintain_order=True)
    usable = distinct.filter(~pl.struct("date", "currency").is_duplicated())
    return usable, (rates.height - positive.height) + (distinct.height - usable.height)
