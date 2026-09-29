"""Read-only Morning Brief schedule detector for the shadow beta."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SLOT = clock_time(6, 36)
GRACE = clock_time(7, 15)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must have timezone")
    return parsed.astimezone(timezone.utc)


def classify(now: datetime, runs: list[dict], jobs_by_run: dict[int, list[dict]], complete: bool = True) -> dict:
    now_kst = now.astimezone(KST)
    day = now_kst.date()
    slot = datetime.combine(day, SLOT, KST).astimezone(timezone.utc)
    grace = datetime.combine(day, GRACE, KST).astimezone(timezone.utc)
    result = {"date_kst": day.isoformat(), "slot_utc": slot.isoformat(), "status": "UNKNOWN", "reason": "INSUFFICIENT_EVIDENCE"}
    if day.weekday() >= 5:
        return {**result, "status": "NOT_DUE", "reason": "WEEKEND"}
    if now.astimezone(timezone.utc) < grace:
        return {**result, "status": "NOT_DUE", "reason": "BEFORE_GRACE"}
    if not complete:
        return result

    scheduled, manual = [], []
    for run in runs:
        created = parse_utc(run.get("created_at"))
        if created is None or not slot - timedelta(minutes=10) <= created <= now.astimezone(timezone.utc):
            continue
        if run.get("event") not in ("schedule", "workflow_dispatch"):
            continue
        jobs = jobs_by_run.get(run["id"])
        if jobs is None:
            return result
        matches = [job for job in jobs if job.get("name") == "Morning Brief" and job.get("conclusion") != "skipped"]
        for job in matches:
            (scheduled if run["event"] == "schedule" else manual).append((run, job))

    if scheduled:
        run, job = min(scheduled, key=lambda pair: parse_utc(pair[1].get("started_at")) or parse_utc(pair[0]["created_at"]))
        started = parse_utc(job.get("started_at")) or parse_utc(run["created_at"])
        base = {**result, "run_id": run["id"], "job_status": job.get("status"), "job_conclusion": job.get("conclusion"), "started_at": started.isoformat()}
        if job.get("status") != "completed":
            return {**base, "status": "LATE_RUNNING", "reason": "MORNING_JOB_IN_FLIGHT"}
        if job.get("conclusion") != "success":
            return {**base, "status": "SCHEDULE_FAILED", "reason": "MORNING_JOB_NOT_SUCCESS"}
        return {**base, "status": "ON_TIME" if started <= grace else "LATE_SUCCESS", "reason": "MORNING_JOB_SUCCESS"}

    if manual:
        successful = [run for run, job in manual if job.get("status") == "completed" and job.get("conclusion") == "success"]
        if successful:
            return {**result, "status": "MANUAL_RUN_SUCCEEDED_UNVERIFIED", "reason": "SCHEDULE_MISSING_MANUAL_JOB_SUCCESS", "manual_run_id": successful[-1]["id"]}
    return {**result, "status": "MISSED_SCHEDULE", "reason": "NO_SCHEDULED_MORNING_JOB"}


def github_get(url: str, token: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            delay = min(float(error.headers.get("Retry-After", 2**attempt)), 10)
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            delay = 2**attempt
        time.sleep(delay)
    raise RuntimeError("GitHub API retry exhausted")


def fetch_runs(repo: str, token: str, now: datetime) -> tuple[list[dict], dict[int, list[dict]], bool]:
    base = f"https://api.github.com/repos/{repo}/actions"
    slot = datetime.combine(now.astimezone(KST).date(), SLOT, KST).astimezone(timezone.utc)
    runs: list[dict] = []
    complete = False
    for page in range(1, 6):
        query = urlencode({"branch": "main", "per_page": 100, "page": page})
        batch = github_get(f"{base}/workflows/main.yml/runs?{query}", token)["workflow_runs"]
        runs.extend(batch)
        if not batch or parse_utc(batch[-1]["created_at"]) < slot - timedelta(minutes=10):
            complete = True
            break
    jobs_by_run: dict[int, list[dict]] = {}
    for run in runs:
        created = parse_utc(run.get("created_at"))
        if created is None or created < slot - timedelta(minutes=10):
            continue
        if run.get("event") not in ("schedule", "workflow_dispatch"):
            continue
        response = github_get(f"{base}/runs/{run['id']}/jobs?per_page=100", token)
        if response.get("total_count", 0) > len(response.get("jobs", [])):
            complete = False
        jobs_by_run[run["id"]] = response["jobs"]
    return runs, jobs_by_run, complete


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", help="UTC timestamp for fixture/manual checks")
    parser.add_argument("--fixture", help="offline JSON containing runs, jobs_by_run and complete")
    args = parser.parse_args()
    now = parse_utc(args.now) if args.now else datetime.now(timezone.utc)
    try:
        if args.fixture:
            with open(args.fixture, encoding="utf-8") as source:
                fixture = json.load(source)
            runs = fixture["runs"]
            jobs = {int(k): v for k, v in fixture["jobs_by_run"].items()}
            complete = fixture.get("complete", True)
        else:
            token, repo = os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"]
            runs, jobs, complete = fetch_runs(repo, token, now)
        result = classify(now, runs, jobs, complete)
    except (KeyError, ValueError, HTTPError, URLError, TimeoutError, OSError) as error:
        result = {"status": "UNKNOWN", "reason": "DETECTOR_ERROR", "error_type": type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] != "UNKNOWN" else 1


if __name__ == "__main__":
    sys.exit(main())
