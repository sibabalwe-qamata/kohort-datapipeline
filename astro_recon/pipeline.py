"""Orchestration only: load, apply --as-of, run each step in the spec's order, write the outputs.

No rule lives here. Each step is a function in its own module; this file is the order they run in.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from astro_recon import installs, spend
from astro_recon.checks import all_checks
from astro_recon.io import Inputs, load_inputs
from astro_recon.quality import build_report
from astro_recon.retention import RETENTION_COLUMNS, weekly_retention
from astro_recon.stages import Stages
from astro_recon.summary import SUMMARY_COLUMNS, build_summary
from astro_recon.visibility import default_as_of, filter_visible
from astro_recon.writers import retention_rows, summary_rows, write_csv, write_json

SUMMARY_FILE = "daily_campaign_summary.csv"
RETENTION_FILE = "weekly_retention.csv"
REPORT_FILE = "data_quality_report.json"


def run(data_dir: Path, out_dir: Path, as_of: date | None = None) -> date:
    """Run the pipeline and return the as-of date it used.

    Everything is computed before the first file is written, so a failure leaves no partial output.
    """
    inputs = load_inputs(data_dir)
    # The default is taken from the whole events file, before any filtering.
    if as_of is None:
        as_of = default_as_of(inputs.events)
    stages = build_stages(inputs, as_of)

    summary_text = summary_rows(stages.summary)
    retention_text = retention_rows(stages.retention)
    report = build_report(as_of, all_checks(stages))

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / SUMMARY_FILE, SUMMARY_COLUMNS, summary_text)
    write_csv(out_dir / RETENTION_FILE, RETENTION_COLUMNS, retention_text)
    write_json(out_dir / REPORT_FILE, report)
    return as_of


def build_stages(inputs: Inputs, as_of: date) -> Stages:
    """Apply every rule once and keep each intermediate frame, so the checks count what really happened."""
    visible = filter_visible(inputs, as_of)
    cleaned_installs = installs.clean(visible.installs, visible.campaigns)
    cleaned_spend = spend.clean(visible.spend, visible.fx)
    return Stages(
        as_of=as_of,
        inputs=inputs,
        visible=visible,
        installs=cleaned_installs,
        spend=cleaned_spend,
        summary=build_summary(cleaned_installs.mapped, cleaned_spend.kept),
        # The SQL applies the as-of rule and cleaning rules 1-2 itself, so it gets the unfiltered frames.
        retention=weekly_retention(inputs.installs, inputs.events, as_of),
    )
