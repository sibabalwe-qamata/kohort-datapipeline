"""Orchestration only: load, apply --as-of, run each step in the spec's order, write the outputs.

No rule lives here. Each step is a function in its own module; this file is the order they run in.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from astro_recon import installs, spend
from astro_recon.checks import Stages, all_checks
from astro_recon.io import Inputs, load_inputs
from astro_recon.quality import build_report
from astro_recon.retention import RETENTION_COLUMNS, weekly_retention
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

    # Install cleaning rules 1-5.
    deduped, duplicate_installs = installs.dedupe_exact(visible.installs)
    cleaned, reinstall_rows = installs.keep_earliest_per_user(deduped)
    paid, organic = installs.split_organic(cleaned)
    mapped, unmapped = installs.map_campaigns(paid, visible.campaigns)

    # Spend rules: one report per key, then USD.
    reports, duplicate_reports = spend.drop_duplicate_reports(visible.spend)
    latest, restated_rows = spend.latest_reports(reports)
    kept = spend.convert_fx(latest, visible.fx)

    summary = build_summary(mapped, kept)
    # The SQL applies the as-of rule and cleaning rules 1-2 itself, so it gets the unfiltered frames.
    retention = weekly_retention(inputs.installs, inputs.events, as_of)

    return Stages(
        as_of=as_of,
        inputs=inputs,
        visible=visible,
        deduped=deduped,
        cleaned=cleaned,
        organic=organic,
        paid=paid,
        mapped=mapped,
        unmapped=unmapped,
        reports=reports,
        kept=kept,
        summary=summary,
        retention=retention,
        duplicate_installs=duplicate_installs,
        reinstall_rows=reinstall_rows,
        duplicate_reports=duplicate_reports,
        restated_rows=restated_rows,
    )
