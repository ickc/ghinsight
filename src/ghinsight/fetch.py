"""Stage 1 — fetch raw workflow-run and job data from GitHub, cached on disk.

This stage is deliberately *dumb*: it copies GitHub's JSON payloads to local
files and does no interpretation. Keeping the cache as verbatim API responses
means the typed parsing in :mod:`ghinsight.domain` is the single place
where raw data becomes structured, and a cache can be re-parsed by a newer
version of the code without re-hitting the network.

Cache layout::

    {cache_dir}/{org}/{repo}/repo.json        # repository metadata (visibility)
    {cache_dir}/{org}/{repo}/runs.json        # list of workflow-run payloads
    {cache_dir}/{org}/{repo}/jobs/{run_id}.json  # list of job payloads per run
    {cache_dir}/_billing/{org}/{YYYY-MM}.json # billing usage items (optional)

``repo.json`` keeps only the few repository fields the analysis needs (see
:data:`REPO_METADATA_FIELDS`), not the full payload. It records the
repository's visibility *at fetch time* and is rewritten on every org fetch.

The billing usage report is optional: the API appears to need an organisation
owner (billing managers get a 404), or, for a user account, the ``user`` token
scope. Without that access :func:`fetch_billing_usage` logs a warning and caches
nothing, and the analysis falls back to minutes estimated from job timings.
Billing managers can import the report CSV instead
(:func:`ghinsight.billing.import_usage_csv`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .client import GitHubError, github_api, github_api_paginate
from .config import DateRange
from .domain import utcnow
from .repos import list_account_repos

log = logging.getLogger(__name__)

__all__ = [
    "CachePaths",
    "REPO_METADATA_FIELDS",
    "billing_months",
    "fetch_billing_usage",
    "fetch_org",
    "fetch_repo",
    "list_org_repos",
    "list_account_repos",
]

# Repository fields cached in ``repo.json``.
REPO_METADATA_FIELDS: tuple[str, ...] = ("name", "private", "visibility", "archived")


@dataclass(frozen=True, slots=True)
class CachePaths:
    """Resolves the on-disk cache locations for one org. Pure path algebra."""

    cache_dir: Path
    org: str

    @property
    def org_dir(self) -> Path:
        return self.cache_dir / self.org

    def repo_dir(self, repo: str) -> Path:
        return self.org_dir / repo

    def repo_metadata_file(self, repo: str) -> Path:
        return self.repo_dir(repo) / "repo.json"

    def runs_file(self, repo: str) -> Path:
        return self.repo_dir(repo) / "runs.json"

    def jobs_dir(self, repo: str) -> Path:
        return self.repo_dir(repo) / "jobs"

    def job_file(self, repo: str, run_id: int) -> Path:
        return self.jobs_dir(repo) / f"{run_id}.json"

    @property
    def billing_dir(self) -> Path:
        # Outside ``org_dir`` so it can never be mistaken for a repository.
        return self.cache_dir / "_billing" / self.org

    def billing_file(self, month: str) -> Path:
        return self.billing_dir / f"{month}.json"


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# API calls (network)
# ---------------------------------------------------------------------------


# Backwards-compatible alias.
list_org_repos = list_account_repos


def get_repo(org: str, repo: str) -> dict[str, Any]:
    """The repository payload for ``org/repo``."""
    return github_api(f"/repos/{org}/{repo}")


# Billing usage endpoints, tried in order: organisation, then user account.
_BILLING_USAGE_ENDPOINTS: tuple[str, ...] = (
    "/organizations/{account}/settings/billing/usage",
    "/users/{account}/settings/billing/usage",
)

# Statuses meaning "this token/account cannot read the billing report".
_BILLING_UNAVAILABLE = frozenset({401, 403, 404, 410})


def get_billing_usage(endpoint: str, month: str) -> list[dict[str, Any]]:
    """Billing usage items for one ``YYYY-MM`` month."""
    year, month_number = month.split("-")
    payload = github_api(
        endpoint, retry_on_rate_limit=False, year=year, month=str(int(month_number))
    )
    items = payload.get("usageItems", []) if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def list_workflow_runs(
    org: str, repo: str, date_range: DateRange | None = None
) -> list[dict[str, Any]]:
    """All workflow-run payloads for a repo, optionally filtered by creation date."""
    params: dict[str, str] = {}
    if date_range is not None:
        since = date_range.since.strftime("%Y-%m-%dT%H:%M:%SZ")
        until = date_range.until.strftime("%Y-%m-%dT%H:%M:%SZ")
        params["created"] = f"{since}..{until}"
    return github_api_paginate(
        f"/repos/{org}/{repo}/actions/runs",
        response_key="workflow_runs",
        **params,
    )


def list_run_jobs(org: str, repo: str, run_id: int) -> list[dict[str, Any]]:
    """All job payloads for a single workflow run."""
    return github_api_paginate(
        f"/repos/{org}/{repo}/actions/runs/{run_id}/jobs",
        response_key="jobs",
        filter="all",
    )


# ---------------------------------------------------------------------------
# Fetch-and-cache
# ---------------------------------------------------------------------------


def _write_repo_metadata(paths: CachePaths, repo: str, payload: Mapping[str, Any]) -> None:
    trimmed = {key: payload[key] for key in REPO_METADATA_FIELDS if key in payload}
    _write_json(paths.repo_metadata_file(repo), trimmed)


def _cache_repo_metadata(org: str, repo: str, paths: CachePaths, *, force: bool) -> None:
    """Cache ``repo.json`` for one repo if missing; failures are only logged."""
    if not force and paths.repo_metadata_file(repo).exists():
        return
    try:
        _write_repo_metadata(paths, repo, get_repo(org, repo))
    except Exception as exc:  # noqa: BLE001 - metadata is optional
        log.warning("No repository metadata for %s/%s: %s", org, repo, exc)


def fetch_repo(
    org: str,
    repo: str,
    cache_dir: Path,
    *,
    date_range: DateRange | None = None,
    force: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch and cache runs (and their jobs) for one repo.

    Cached files are reused unless ``force`` is set. ``metadata`` is the repo's
    payload from an org listing; without it the repo is looked up (once) for
    its visibility. Returns the run payloads.
    """
    paths = CachePaths(cache_dir, org)
    runs_file = paths.runs_file(repo)

    if metadata is not None:
        _write_repo_metadata(paths, repo, metadata)
    else:
        _cache_repo_metadata(org, repo, paths, force=force)

    if force or not runs_file.exists():
        log.info("Fetching runs for %s/%s", org, repo)
        runs = list_workflow_runs(org, repo, date_range)
        _write_json(runs_file, runs)
    else:
        runs = _read_json(runs_file)

    for run in runs:
        run_id = int(run["id"])
        job_file = paths.job_file(repo, run_id)
        if not force and job_file.exists():
            continue
        log.debug("Fetching jobs for run %s (%s/%s)", run_id, org, repo)
        _write_json(job_file, list_run_jobs(org, repo, run_id))

    return runs


def fetch_org(
    org: str,
    cache_dir: Path,
    *,
    date_range: DateRange | None = None,
    force: bool = False,
    repos: Iterable[str] | None = None,
) -> list[str]:
    """Fetch and cache every repo in the org (or an explicit ``repos`` subset).

    Repos that error (e.g. no Actions access) are logged and skipped. Returns
    the list of repo names successfully fetched.
    """
    metadata: dict[str, Mapping[str, Any]] = {}
    if repos is None:
        metadata = {r["name"]: r for r in list_account_repos(org)}
        repos = list(metadata)

    fetched: list[str] = []
    for repo in repos:
        try:
            fetch_repo(
                org,
                repo,
                cache_dir,
                date_range=date_range,
                force=force,
                metadata=metadata.get(repo),
            )
            fetched.append(repo)
        except Exception as exc:  # noqa: BLE001 - resilience over strictness
            log.warning("Skipping %s/%s: %s", org, repo, exc)
    return fetched


# ---------------------------------------------------------------------------
# Billing usage report (optional)
# ---------------------------------------------------------------------------


def billing_months(date_range: DateRange) -> list[str]:
    """Calendar months (``YYYY-MM``) overlapping ``[since, until)``."""
    year, month = date_range.since.year, date_range.since.month
    months: list[str] = []
    while datetime(year, month, 1) < date_range.until.replace(tzinfo=None):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def fetch_billing_usage(
    account: str,
    cache_dir: Path,
    date_range: DateRange,
    *,
    force: bool = False,
) -> list[str] | None:
    """Fetch and cache the billing usage report, one file per month.

    Completed months are reused from the cache unless ``force`` is set; the
    current month is always refreshed. Returns the cached months, or ``None``
    if the report is unavailable to this token, in which case nothing is
    written and the caller should carry on without it.
    """
    paths = CachePaths(cache_dir, account)
    current = utcnow().strftime("%Y-%m")
    endpoints = [e.format(account=account) for e in _BILLING_USAGE_ENDPOINTS]

    cached: list[str] = []
    for month in billing_months(date_range):
        billing_file = paths.billing_file(month)
        if not force and month < current and billing_file.exists():
            cached.append(month)
            continue
        while endpoints:
            try:
                items = get_billing_usage(endpoints[0], month)
                break
            except GitHubError as exc:
                if exc.status not in _BILLING_UNAVAILABLE:
                    raise
                log.debug("Billing usage unavailable at %s: %s", endpoints[0], exc.status)
                endpoints.pop(0)
        else:
            log.warning(
                "Billing usage report for %s is unavailable to this token (the API "
                "appears to need an organisation owner; billing managers can use "
                "import-billing); continuing without it.",
                account,
            )
            return None
        _write_json(billing_file, items)
        cached.append(month)
    return cached
