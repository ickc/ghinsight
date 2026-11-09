"""Deterministic synthetic GitHub Actions cache for offline tests and docs.

This writes the same on-disk cache layout that ``ghinsight fetch`` would
produce, so the entire stage 2/3 pipeline can be exercised without a network or
a token. The numbers are small and hand-chosen so tests can assert exact values.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

__all__ = ["write_synthetic_billing", "write_synthetic_cache", "ORG", "VISIBILITY"]

ORG = "demo-org"

# Repository metadata written to ``repo.json``: one private, one public repo.
VISIBILITY: dict[str, str] = {"api": "private", "web": "public"}

# (repo, workflow_path, runner_group, labels, conclusion, run_minutes, month_day)
# run_minutes is the exact job execution time; billed = ceil(minutes) per job.
_JOBS: list[tuple[str, str, str, list[str], str, float, tuple[int, int, int]]] = [
    ("api", ".github/workflows/ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 4.0, (2024, 1, 5)),
    ("api", ".github/workflows/ci.yml", "GitHub Actions", ["ubuntu-latest"], "failure", 6.0, (2024, 1, 20)),
    ("api", ".github/workflows/ci.yml", "GitHub Actions", ["windows-latest"], "success", 3.0, (2024, 2, 8)),
    ("api", ".github/workflows/release.yml", "GitHub Actions", ["macos-latest"], "success", 5.0, (2024, 2, 18)),
    ("web", ".github/workflows/ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 2.0, (2024, 1, 9)),
    ("web", ".github/workflows/ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 7.0, (2024, 2, 27)),
    # A self-hosted job: present in the cache but excluded from hosted metrics.
    ("web", ".github/workflows/ci.yml", "self-hosted-group", ["self-hosted", "linux"], "success", 9.0, (2024, 2, 14)),
]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_synthetic_cache(
    cache_dir: Path, org: str = ORG, *, repo_metadata: bool = True
) -> Path:
    """Create a synthetic cache under ``cache_dir/org`` and return ``cache_dir``.

    ``repo_metadata=False`` omits ``repo.json``, as in caches written before
    visibility was recorded.
    """
    runs_by_repo: dict[str, list[dict]] = {}
    jobs_by_run: dict[tuple[str, int], list[dict]] = {}

    for index, (repo, path, group, labels, conclusion, minutes, ymd) in enumerate(_JOBS):
        run_id = 1000 + index
        job_id = 2000 + index
        created = datetime(*ymd, 9, 0, 0, tzinfo=timezone.utc)
        started = created + timedelta(seconds=30)  # 30s queue time
        completed = started + timedelta(minutes=minutes)

        runs_by_repo.setdefault(repo, []).append(
            {
                "id": run_id,
                "path": path,
                "conclusion": conclusion,
                "created_at": _iso(created),
                "run_started_at": _iso(started),
                "updated_at": _iso(completed),
            }
        )
        jobs_by_run[(repo, run_id)] = [
            {
                "id": job_id,
                "run_id": run_id,
                "name": "build",
                "workflow_name": Path(path).stem,
                "status": "completed",
                "conclusion": conclusion,
                "runner_group_name": group,
                "labels": labels,
                "created_at": _iso(created),
                "started_at": _iso(started),
                "completed_at": _iso(completed),
            }
        ]

    for repo, runs in runs_by_repo.items():
        repo_dir = cache_dir / org / repo
        (repo_dir / "jobs").mkdir(parents=True, exist_ok=True)
        (repo_dir / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
        if repo_metadata:
            visibility = VISIBILITY[repo]
            meta = {"name": repo, "private": visibility != "public", "visibility": visibility}
            (repo_dir / "repo.json").write_text(json.dumps(meta), encoding="utf-8")
        for run in runs:
            job_file = repo_dir / "jobs" / f"{run['id']}.json"
            job_file.write_text(json.dumps(jobs_by_run[(repo, run["id"])]), encoding="utf-8")

    return cache_dir


# (month, repositoryName, sku, minutes, net_amount): what GitHub billed. Names may
# carry the owner prefix. The public "web" repo is listed like the private one.
_BILLING: list[tuple[str, str, str, float, float]] = [
    ("2024-01", f"{ORG}/api", "actions_linux", 11.0, 0.0),
    ("2024-01", "web", "actions_linux", 2.0, 0.0),
    ("2024-02", f"{ORG}/api", "actions_windows", 3.0, 0.0),
    ("2024-02", f"{ORG}/api", "actions_macos", 5.0, 0.4),
    ("2024-02", "web", "actions_linux", 7.0, 0.0),
]


def write_synthetic_billing(cache_dir: Path, org: str = ORG) -> Path:
    """Write a synthetic billing usage report under ``cache_dir/_billing/org``."""
    by_month: dict[str, list[dict]] = {}
    for month, repo, sku, minutes, net in _BILLING:
        by_month.setdefault(month, []).append(
            {
                "date": f"{month}-15T00:00:00Z",
                "product": "actions",
                "sku": sku,
                "quantity": minutes,
                "unitType": "Minutes",
                "pricePerUnit": 0.008,
                "grossAmount": minutes * 0.008,
                "discountAmount": minutes * 0.008 - net,
                "netAmount": net,
                "organizationName": org,
                "repositoryName": repo,
            }
        )
    # A non-Actions line that must be ignored by the minutes analysis.
    by_month["2024-01"].append(
        {"date": "2024-01-15T00:00:00Z", "product": "packages", "sku": "packages_storage",
         "quantity": 1.5, "unitType": "GigabyteHours", "pricePerUnit": 0.0,
         "grossAmount": 0.0, "discountAmount": 0.0, "netAmount": 0.0,
         "organizationName": org}
    )
    billing_dir = cache_dir / "_billing" / org
    billing_dir.mkdir(parents=True, exist_ok=True)
    for month, items in by_month.items():
        (billing_dir / f"{month}.json").write_text(json.dumps(items), encoding="utf-8")
    return cache_dir
