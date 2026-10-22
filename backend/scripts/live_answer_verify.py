"""Verify live run answers against Jira simulator ground truth.

Usage (stack on :8000):
    python scripts/live_answer_verify.py                  # re-run all + verify
    python scripts/live_answer_verify.py --reuse-runs     # verify saved run IDs only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))
os.chdir(BACKEND)

from ground_truth import (
    extract_numbers,
    issue_facts,
    mentions_any,
    plat_closed_velocities,
    plat_last_n_bug_counts,
    project_health,
    project_leads,
    velocity_forecast_band,
)
from live_dataset_runner import CASES, run_case  # noqa: E402


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("API_TOKEN", "").strip()
    if token:
        headers["X-API-Key"] = token
    return headers


def _req(base: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(base + path, headers=_headers())
    with urllib.request.urlopen(request, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _full_answer(base: str, run_id: str) -> str:
    run = _req(base, f"/runs/{run_id}")
    return str(run.get("result_summary") or run.get("result") or "")


# Prior live run IDs (from last validation session)
SAVED_RUNS: dict[str, str] = {
    "TC-R01": "53e987d5-c366-4a02-a457-2e94444a4b1d",
    "TC-R02": "3cb67c53-560e-4fe4-b571-d7ec02801155",
    "TC-R03": "1ad6fc21-946c-4a9f-8d41-011179f51c69",
    "TC-R04": "266ac913-77d5-4736-b0c1-5a18387cb4b0",
    "TC-B03": "be098ad8-5fcb-4833-a7ce-376a950c9ae7",
    "TC-B04": "925fab2d-461d-4e4b-ad73-293428afd2c1",
    "TC-B01": "e4881ab6-1497-4578-8223-718dd9d344d6",
    "Q-10": "cdba2a42-81cb-4ea4-aad2-5255f62d23ec",
    "Q-30": "23f6a8e2-095b-40ae-80a3-b9f6bbba22f0",
}


def verify_answer(case_id: str, answer: str) -> tuple[bool, list[str], dict[str, Any]]:
    """Return (correct, issues, ground_truth_snapshot)."""
    issues: list[str] = []
    gt: dict[str, Any] = {}
    text = answer or ""
    lower = text.lower()

    if case_id == "TC-R01":
        if len(text.strip()) < 20:
            issues.append("Answer too short for capability overview")
        if not mentions_any(text, ["agent", "tool", "jira", "help", "coordinate"]):
            issues.append("Should describe platform capabilities")
        return not issues, issues, gt

    if case_id == "TC-R02":
        gt["plat_closed_velocities"] = plat_closed_velocities()
        lo, hi = velocity_forecast_band()
        gt["forecast_band"] = [lo, hi]
        nums = extract_numbers(text)
        forecast_nums = [n for n in nums if 15 <= n <= 80]
        if not mentions_any(text, ["plat", "platform delivery"]):
            issues.append("Should anchor forecast to PLAT (has closed sprint history)")
        if not forecast_nums:
            issues.append("No numeric velocity forecast found")
        elif not any(lo <= n <= hi for n in forecast_nums):
            issues.append(
                f"Forecast {forecast_nums} outside reasonable band {lo}-{hi} "
                f"(from closed velocities {gt['plat_closed_velocities']})"
            )
        if "spr-6" in lower and any(n <= 12 for n in forecast_nums):
            issues.append("Should not treat active SPR-6 (8 pts) as historical velocity")
        cited = sum(1 for v in gt["plat_closed_velocities"] if str(int(v)) in text)
        if cited == 0 and not any(
            str(v) in text for v in (30, 36, 38, 42, 45)
        ):
            issues.append("Should cite or derive from closed sprint point totals")
        return not issues, issues, gt

    if case_id == "TC-R03":
        gt["last_3_bug_counts"] = plat_last_n_bug_counts(3)
        gt["bug_values"] = [r["bug_count"] for r in gt["last_3_bug_counts"]]
        if not mentions_any(text, ["bug", "defect"]):
            issues.append("Should discuss bugs")
        if not mentions_any(text, ["sprint", "spr-"]):
            issues.append("Should reference sprints")
        if not mentions_any(text, ["trend", "up", "down", "declin", "increas", "stable", "flat"]):
            issues.append("Should describe trend direction")
        # Check cited bug counts match ground truth if numbers appear
        for row in gt["last_3_bug_counts"]:
            sid = row["id"].lower()
            if sid in lower:
                gt.setdefault("sprints_mentioned", []).append(sid)
        for expected in gt["bug_values"]:
            if str(expected) in text:
                gt.setdefault("bug_numbers_cited", []).append(expected)
        # Trend sanity: compare first vs last of last 3 closed
        counts = gt["bug_values"]
        if len(counts) >= 2:
            if counts[-1] < counts[0]:
                gt["expected_trend"] = "declining"
            elif counts[-1] > counts[0]:
                gt["expected_trend"] = "increasing"
            else:
                gt["expected_trend"] = "stable"
            trend_words = {
                "declining": ["declin", "down", "decreas", "drop", "lower", "fewer"],
                "increasing": ["increas", "up", "rise", "grow", "higher", "more"],
                "stable": ["stable", "flat", "steady", "unchanged", "consistent"],
            }
            expected = gt["expected_trend"]
            if not mentions_any(text, trend_words.get(expected, [])):
                issues.append(
                    f"Trend language should match data ({expected}: counts {counts})"
                )
        return not issues, issues, gt

    if case_id == "TC-R04":
        gt["project_leads"] = project_leads()
        for key in ("PLAT", "NOVA", "ANLT", "PAY"):
            if key.lower() not in lower and key not in text:
                issues.append(f"Missing project {key}")
        for key, lead in gt["project_leads"].items():
            if key.lower() in lower or key in text:
                # accept first or last name
                parts = lead.lower().split()
                if not any(p in lower for p in parts if len(p) > 2):
                    issues.append(f"{key} lead should be {lead}")
        return not issues, issues, gt

    if case_id in ("TC-B03", "Q-30"):
        gt["plat_health"] = project_health("PLAT")
        gt["nova_health"] = project_health("NOVA")
        if "plat" not in lower:
            issues.append("Missing PLAT")
        if "nova" not in lower:
            issues.append("Missing NOVA")
        for label in ("at risk", "healthy", "elevated"):
            if label in lower:
                gt.setdefault("health_terms_found", []).append(label)
        if not gt.get("health_terms_found"):
            issues.append("Should state health rating (e.g. At Risk)")
        return not issues, issues, gt

    if case_id == "TC-B04":
        gt["health"] = {k: project_health(k) for k in ("PLAT", "NOVA", "ANLT", "PAY")}
        for key in ("PLAT", "NOVA", "ANLT", "PAY"):
            if key.lower() not in lower:
                issues.append(f"Missing project {key}")
        return not issues, issues, gt

    if case_id == "TC-B01":
        if not re.search(r"plat-\d+", text, re.IGNORECASE):
            issues.append("Should confirm created issue key PLAT-NN")
        if "track next sprint commitments" not in lower:
            issues.append("Should echo issue summary")
        gt["expected_summary"] = "Track next sprint commitments"
        return not issues, issues, gt

    if case_id == "Q-10":
        gt["plat_9"] = issue_facts("PLAT-9")
        if not gt["plat_9"]:
            issues.append("Ground truth PLAT-9 unavailable")
            return False, issues, gt
        if not re.search(r"plat-9", text, re.IGNORECASE):
            issues.append("Should reference PLAT-9")
        # Accept partial fetch if status/summary/blocked mentioned correctly
        summary = str(gt["plat_9"].get("summary") or "").lower()
        status = str(gt["plat_9"].get("status") or "").lower()
        blocked = gt["plat_9"].get("blocked")
        gt["expected"] = gt["plat_9"]
        if "wasn't able" in lower or "couldn't" in lower or "unable" in lower:
            issues.append(
                f"Answer claims fetch failure but PLAT-9 exists: "
                f"{gt['plat_9']['summary']} / {gt['plat_9']['status']}"
            )
        else:
            if summary and not any(w in lower for w in summary.split()[:3]):
                issues.append(f"Should mention issue summary ({gt['plat_9']['summary']})")
            if status and status not in lower and "progress" not in lower:
                issues.append(f"Should mention status ({gt['plat_9']['status']})")
            if blocked and not mentions_any(text, ["block", "blocked", "dependency"]):
                issues.append("PLAT-9 is blocked — answer should mention blockage")
        return not issues, issues, gt

    return True, [], gt


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify answer correctness vs simulator ground truth")
    parser.add_argument("--base", default="http://127.0.0.1:8000/api")
    parser.add_argument("--reuse-runs", action="store_true", help="Use saved run IDs (no re-run)")
    parser.add_argument("--ids", default="", help="Comma-separated case IDs")
    args = parser.parse_args()

    try:
        ready = _req(args.base, "/health/ready")
        if ready.get("status") != "ok":
            print(f"FAIL: stack not ready: {ready}")
            return 1
    except urllib.error.URLError as exc:
        print(f"FAIL: API not reachable ({exc})")
        return 1

    selected = CASES
    if args.ids.strip():
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        selected = tuple(c for c in CASES if c.case_id in wanted)

    results: list[dict[str, Any]] = []
    wrong = 0

    print("Answer correctness vs Jira simulator ground truth (AS_OF 2026-09-05)")
    print("-" * 80)

    for case in selected:
        latency = 0.0
        run_id = ""
        if args.reuse_runs and case.case_id in SAVED_RUNS:
            run_id = SAVED_RUNS[case.case_id]
            try:
                t0 = time.perf_counter()
                answer = _full_answer(args.base, run_id)
                latency = round(time.perf_counter() - t0, 2)
            except urllib.error.HTTPError:
                row = run_case(args.base, case)
                run_id = str(row["run_id"])
                answer = _full_answer(args.base, run_id)
                latency = float(row["latency_s"])
        else:
            print(f"[{case.case_id}] running...", flush=True)
            row = run_case(args.base, case)
            run_id = str(row["run_id"])
            answer = _full_answer(args.base, run_id)
            latency = float(row["latency_s"])

        correct, issues, gt = verify_answer(case.case_id, answer)
        if not correct:
            wrong += 1
        status = "CORRECT" if correct else "INCORRECT"
        print(f"[{case.case_id}] {status} | latency={latency}s | run={run_id[:8]}...")
        if gt:
            print(f"         ground_truth: {json.dumps(gt, default=str)[:200]}")
        if issues:
            for item in issues:
                print(f"         issue: {item}")
        preview = answer[:180].replace("\n", " ")
        print(f"         answer: {preview}")
        print("-" * 80)
        results.append({
            "id": case.case_id,
            "correct": correct,
            "latency_s": latency,
            "run_id": run_id,
            "issues": issues,
            "ground_truth": gt,
            "answer_preview": preview,
        })

    out = BACKEND / "data" / "live_answer_verification.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    correct_n = len(results) - wrong
    print(f"SUMMARY: {correct_n}/{len(results)} answers correct | saved: {out}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
