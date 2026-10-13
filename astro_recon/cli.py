"""Command line: `python -m astro_recon --data <dir> --out <dir> [--as-of YYYY-MM-DD]`.

Exit codes: 0 on success; 2 for anything the operator can fix (bad arguments, bad input files, an
output folder that cannot be written). Any other exception is a bug: it is left to propagate, so
Python prints the traceback and exits 1.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from astro_recon import pipeline
from astro_recon.io import InputError
from astro_recon.visibility import parse_as_of


def _as_of(text: str) -> date:
    """argparse turns ArgumentTypeError into a usage message on stderr and exit code 2."""
    try:
        return parse_as_of(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m astro_recon",
        description="Reconcile MMP installs and events against ad-network spend.",
    )
    parser.add_argument("--data", required=True, type=Path, help="folder holding the five input CSV files")
    parser.add_argument("--out", required=True, type=Path, help="folder to write the three output files to")
    parser.add_argument(
        "--as-of",
        type=_as_of,
        metavar="YYYY-MM-DD",
        help="report as of the end of this UTC day (default: the date of the latest event)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        as_of = pipeline.run(args.data, args.out, args.as_of)
    except (InputError, OSError) as exc:
        print(f"astro_recon: error: {exc}", file=sys.stderr)
        return 2
    print(f"astro_recon: wrote outputs to {args.out} (as_of {as_of.isoformat()})")
    return 0
