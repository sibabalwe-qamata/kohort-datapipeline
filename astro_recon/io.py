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

# A parser takes a column name and returns an expression that is null where the text will not parse.
Parser = Callable[[str], pl.Expr]
# A file's own last step after typing: the usable rows, and how many more rows were not usable.
Finish = Callable[[pl.DataFrame], tuple[pl.DataFrame, int]]


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


@dataclass(frozen=True)
class FileSpec:
    """One input file: the columns it must have, how they are cast, and which values may not be empty."""

    stem: str
    columns: tuple[str, ...]  # needed from the file; extra columns are ignored
    parsed: dict[str, tuple[str, Parser]]  # new column -> (source column, parser)
    non_null: tuple[str, ...]  # a row with one of these empty is rejected
    keep: tuple[str, ...] | None = None  # the columns handed on; None keeps them all
    finish: Finish | None = None

    @property
    def filename(self) -> str:
        return f"{self.stem}.csv"

    def read(self, data_dir: Path) -> pl.DataFrame:
        return _read_text(data_dir / self.filename, list(self.columns))

    def parse(self, raw: pl.DataFrame) -> tuple[pl.DataFrame, int]:
        """The typed frame and how many rows were rejected, counting the finish step's too."""
        typed, rejected = _typed(raw, self.parsed, list(self.non_null))
        if self.finish is not None:
            typed, unusable = self.finish(typed)
            rejected += unusable
        if self.keep is not None:
            typed = typed.select(self.keep)
        return typed, rejected


def load_inputs(data_dir: Path) -> Inputs:
    """Load and type the files in `FILES`, or raise InputError naming what is wrong with the folder."""
    if not data_dir.is_dir():
        raise InputError(f"--data {data_dir} is not a directory")
    missing = [spec.filename for spec in FILES if not (data_dir / spec.filename).is_file()]
    if missing:
        raise InputError(f"missing input file(s) in {data_dir}: {', '.join(missing)}")

    # Every file's shape is checked before any is typed, so a bad header is reported first.
    raw = {spec.stem: spec.read(data_dir) for spec in FILES}
    typed = {spec.stem: spec.parse(raw[spec.stem]) for spec in FILES}

    return Inputs(
        installs=typed["mmp_installs"][0],
        events=typed["mmp_events"][0],
        spend=typed["network_spend"][0],
        campaigns=typed["campaigns"][0],
        fx=typed["fx_rates"][0],
        rejected_rows={stem: rejected for stem, (_, rejected) in typed.items()},
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
    rejects = [pl.col(source).is_not_null() & pl.col(new).is_null() for new, (source, _) in parsed.items()]
    rejects += [pl.col(name).is_null() for name in required]
    good = out.filter(~pl.any_horizontal(rejects)) if rejects else out
    return good, out.height - good.height


def _unique_campaign_ids(campaigns: pl.DataFrame) -> tuple[pl.DataFrame, int]:
    """A repeated id stops the run."""
    if campaigns["campaign_id"].is_duplicated().any():
        raise InputError("campaigns.csv: campaign_id is not unique, so every join on it would duplicate rows")
    return campaigns, 0


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


# The five input files, in the order they are read and reported.
FILES = (
    FileSpec(
        stem="mmp_installs",
        columns=(
            "install_id", "user_id", "install_ts_utc", "platform", "country", "media_source", "campaign",
        ),
        parsed={"install_ts": ("install_ts_utc", _timestamp)},
        non_null=("user_id", "install_ts_utc"),
    ),
    FileSpec(
        stem="mmp_events",
        columns=("user_id", "event_ts_utc", "event_type", "revenue_usd"),
        parsed={"event_ts": ("event_ts_utc", _timestamp), "revenue": ("revenue_usd", _number)},
        non_null=("user_id", "event_ts_utc"),
        keep=("user_id", "event_ts", "event_type", "revenue"),
    ),
    FileSpec(
        stem="network_spend",
        columns=(
            "stat_date", "network", "campaign_id", "spend", "currency", "reported_installs", "reported_at",
        ),
        parsed={
            "date": ("stat_date", _date),
            "spend_amount": ("spend", _number),
            "installs_reported": ("reported_installs", _integer),
            "reported_ts": ("reported_at", _timestamp),
        },
        non_null=("stat_date", "network", "campaign_id", "currency", "reported_at"),
        keep=(
            "date", "network", "campaign_id", "spend_amount", "currency", "installs_reported", "reported_ts",
        ),
    ),
    FileSpec(
        stem="campaigns",
        columns=("campaign_id", "campaign_name", "network"),
        parsed={},
        non_null=("campaign_id", "network"),
        finish=_unique_campaign_ids,
    ),
    FileSpec(
        stem="fx_rates",
        columns=("date", "currency", "usd_per_unit"),
        parsed={"fx_date": ("date", _date), "rate": ("usd_per_unit", _number)},
        non_null=("date", "currency", "usd_per_unit"),
        finish=_usable_rates,
    ),
)
