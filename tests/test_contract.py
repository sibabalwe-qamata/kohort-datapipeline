"""Contract test shipped with the assignment. Keep it green.

Runs `python -m astro_recon` on data_mini/ (a default run and --as-of 2026-05-10) and compares the
three graded files with data_mini/expected/. Our grader is this same check on the full datasets.
Standard library only, so it does not care which dataframe library you use.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_mini"
EXPECTED = DATA / "expected"
CMD = [sys.executable, "-m", "astro_recon"]

SUMMARY_FILE, RETENTION_FILE, REPORT_FILE = "daily_campaign_summary.csv", "weekly_retention.csv", "data_quality_report.json"
SUMMARY_COLS = ["date", "network", "campaign_id", "mmp_installs", "network_reported_installs",
                "spend_usd", "cpi_usd", "install_gap_pct", "spend_reported_at"]
RETENTION_COLS = ["install_week", "cohort_size", "d7_retained_users", "d7_retention_pct", "is_mature"]
SUMMARY_KEYS, RETENTION_KEYS = ["date", "network", "campaign_id"], ["install_week"]
INT_COLS = {"mmp_installs", "network_reported_installs", "cohort_size", "d7_retained_users"}
TOLERANCE = {"spend_usd": 0.011, "cpi_usd": 0.001, "install_gap_pct": 0.051, "d7_retention_pct": 0.051}
REQUIRED_IDS = ["duplicate_install_rows", "install_reinstall_rows", "organic_installs_excluded",
                "campaign_id_normalised", "campaign_unmapped", "spend_restated_rows",
                "spend_fx_converted_rows", "fx_rate_missing", "spend_missing"]
SEVERITIES, ACTIONS = {"info", "warning", "error"}, {"removed", "corrected", "excluded", "flagged", "none"}
NULL_TOKENS = {"", "na", "n/a", "nan", "null", "none"}
CLOCK_KEY = re.compile(r"generated_at|timestamp|run_at|created_at", re.I)

RUNS = [("default", []), ("asof_2026-05-10", ["--as-of", "2026-05-10"])]


def run_cli(out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(CMD + ["--data", str(DATA), "--out", str(out), *extra],
                          cwd=ROOT, capture_output=True, text=True, timeout=300)


@pytest.fixture(scope="module", params=RUNS, ids=[r[0] for r in RUNS])
def run_dir(request, tmp_path_factory) -> tuple[str, Path]:
    label, args = request.param
    out = tmp_path_factory.mktemp(label)
    proc = run_cli(out, *args)
    assert proc.returncode == 0, f"pipeline failed (exit {proc.returncode}):\n{proc.stderr}"
    return label, out


# ---------------------------------------------------------------- helpers
def read_csv(path: Path) -> tuple[list[str], list[dict[str, str | None]]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    header = [h.strip() for h in rows[0]]
    body = [{h: norm(v) for h, v in zip(header, r)} for r in rows[1:] if any(c.strip() for c in r)]
    return header, body


def norm(v: str | None) -> str | None:
    s = (v or "").strip()
    return None if s.lower() in NULL_TOKENS else s


def norm_value(col: str, v: str | None):
    if v is None:
        return None
    if col in INT_COLS:
        f = float(v)
        assert f == int(f), f"{col}={v!r} is not an integer"
        return int(f)
    if col in TOLERANCE:
        return float(v)
    if col == "is_mature":
        return {"true": True, "false": False, "1": True, "0": False}[v.lower()]
    if col == "spend_reported_at":
        return re.sub(r"\.\d+", "", v.replace("+00:00", "Z"))
    if col in ("date", "install_week"):
        return v[:10]
    return v


def compare(cand: Path, exp: Path, cols: list[str], keys: list[str]) -> None:
    c_header, c_rows = read_csv(cand)
    e_header, e_rows = read_csv(exp)
    assert c_header == cols, f"{cand.name}: columns must be exactly {cols}, got {c_header}"
    assert len(c_rows) == len(e_rows), f"{cand.name}: {len(c_rows)} rows, expected {len(e_rows)}"
    key = lambda r: tuple(norm_value(k, r[k]) for k in keys)
    c_keys, e_keys = [key(r) for r in c_rows], [key(r) for r in e_rows]
    assert set(c_keys) == set(e_keys), (f"{cand.name}: key mismatch; missing {sorted(set(e_keys) - set(c_keys))[:5]}, "
                                        f"extra {sorted(set(c_keys) - set(e_keys))[:5]}")
    assert c_keys == sorted(c_keys), f"{cand.name}: rows are not sorted by {keys}"
    by_key = {key(r): r for r in c_rows}
    mismatches = []
    for e in e_rows:
        c = by_key[key(e)]
        for col in cols:
            cv, ev = norm_value(col, c[col]), norm_value(col, e[col])
            if (cv is None) != (ev is None):
                mismatches.append(f"{key(e)} {col}: got {cv!r}, expected {ev!r} (null-ness)")
            elif cv is not None and col in TOLERANCE:
                if abs(cv - ev) > TOLERANCE[col]:
                    mismatches.append(f"{key(e)} {col}: got {cv}, expected {ev}")
            elif cv != ev:
                mismatches.append(f"{key(e)} {col}: got {cv!r}, expected {ev!r}")
    assert not mismatches, f"{cand.name}: {len(mismatches)} mismatched cell(s):\n  " + "\n  ".join(mismatches[:15])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def walk_keys(obj, found: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if CLOCK_KEY.search(k):
                found.append(k)
            walk_keys(v, found)
    elif isinstance(obj, list):
        for v in obj:
            walk_keys(v, found)


# ---------------------------------------------------------------- tests
def test_outputs_exist(run_dir):
    _, out = run_dir
    for name in (SUMMARY_FILE, RETENTION_FILE, REPORT_FILE):
        assert (out / name).is_file(), f"{name} was not written to --out"


def test_summary_matches_expected(run_dir):
    label, out = run_dir
    compare(out / SUMMARY_FILE, EXPECTED / label / SUMMARY_FILE, SUMMARY_COLS, SUMMARY_KEYS)


def test_retention_matches_expected(run_dir):
    label, out = run_dir
    compare(out / RETENTION_FILE, EXPECTED / label / RETENTION_FILE, RETENTION_COLS, RETENTION_KEYS)


def test_quality_report_shape_and_required_counts(run_dir):
    label, out = run_dir
    report = json.loads((out / REPORT_FILE).read_text())
    expected = json.loads((EXPECTED / label / REPORT_FILE).read_text())
    assert isinstance(report, dict) and isinstance(report.get("as_of"), str) and isinstance(report.get("checks"), list)
    assert report["as_of"] == expected["as_of"]
    ids = [c["id"] for c in report["checks"]]
    assert len(ids) == len(set(ids)), "check ids must be unique"
    assert ids == sorted(ids), "checks must be sorted by id"
    for c in report["checks"]:
        assert isinstance(c["id"], str) and c["severity"] in SEVERITIES and c["action"] in ACTIONS
        assert isinstance(c["rows_affected"], int) and isinstance(c["summary"], str) and len(c["summary"]) <= 300
    clock = []
    walk_keys(report, clock)
    assert not clock, f"no wall-clock values in the graded report; found keys {clock}"
    got = {c["id"]: c["rows_affected"] for c in report["checks"]}
    want = {c["id"]: c["rows_affected"] for c in expected["checks"] if c["id"] in REQUIRED_IDS}
    missing = [i for i in REQUIRED_IDS if i not in got]
    assert not missing, f"required check ids missing: {missing}"
    wrong = {i: (got[i], want[i]) for i in REQUIRED_IDS if got[i] != want[i]}
    assert not wrong, f"rows_affected differs (got, expected): {wrong}"


def test_two_runs_are_byte_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    assert run_cli(a).returncode == 0 and run_cli(b).returncode == 0
    for name in (SUMMARY_FILE, RETENTION_FILE, REPORT_FILE):
        assert sha256(a / name) == sha256(b / name), f"{name} differs between two identical runs"


def test_missing_data_dir_exits_non_zero(tmp_path):
    proc = subprocess.run(CMD + ["--data", str(tmp_path / "does-not-exist"), "--out", str(tmp_path / "out")],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0
    assert proc.stderr.strip(), "expected an error message on stderr"


def test_bad_as_of_exits_non_zero(tmp_path):
    proc = subprocess.run(CMD + ["--data", str(DATA), "--out", str(tmp_path / "out"), "--as-of", "yesterday"],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0
