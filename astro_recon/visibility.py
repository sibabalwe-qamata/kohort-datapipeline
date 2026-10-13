"""`--as-of D`: the data that could have been seen at the end of UTC day D."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import polars as pl

from astro_recon.io import InputError, Inputs

_AS_OF_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def parse_as_of(text: str) -> date:
    """Strict YYYY-MM-DD. `date.fromisoformat` would also accept 20260510 and 2026-W19-1."""
    parsed = None
    if _AS_OF_SHAPE.fullmatch(text):
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            pass  # the right shape but not a real date, such as 2026-13-01
    if parsed is None:
        raise ValueError(f"expected a real date written as YYYY-MM-DD, got {text!r}")
    if parsed == date.max:
        # `cutoff` needs the day after as_of, and 9999-12-31 is the last day a `date` can hold.
        raise ValueError(f"{text} is too late: the report needs the day after it, which does not exist here")
    return parsed


def default_as_of(events: pl.DataFrame) -> date:
    """The date of the latest event in the whole file, before any filtering."""
    latest = events["event_ts"].max()
    if latest is None:
        raise InputError("mmp_events.csv has no usable rows to take a default as-of date from; pass --as-of")
    return latest.date()


def cutoff(as_of: date) -> datetime:
    """Visible means strictly before this instant: midnight at the start of D + 1."""
    return datetime.combine(as_of + timedelta(days=1), time.min)


def filter_visible(inputs: Inputs, as_of: date) -> Inputs:
    """MMP rows by their own timestamp; spend by arrival time, not stat_date. Reference files unfiltered."""
    end = cutoff(as_of)
    return replace(
        inputs,
        installs=inputs.installs.filter(pl.col("install_ts") < end),
        events=inputs.events.filter(pl.col("event_ts") < end),
        spend=inputs.spend.filter(pl.col("reported_ts") < end),
    )
