"""Stage 2 — the canonical intermediate representation.

:class:`ActionsDataset` is the single source of truth for the analysis. It holds
the org's jobs and runs as typed, immutable records (see
:mod:`ghinsight.domain`) and exposes them as *tidy* pandas DataFrames —
one row per job, one row per run — via cached properties.

**Why a tidy per-job table instead of GitHub-style aggregate CSVs?**
The original pipeline used GitHub's UI-export CSVs (already grouped by
workflow/repo/OS) as its intermediate representation. Those aggregates drop the
per-job timestamps, so any time-based view (monthly trends, "last green day")
could not be derived from them — the old dashboard had to secretly re-read the
raw cache to draw its monthly charts. A tidy per-job table keeps every measure
and dimension at full resolution, so *every* downstream view — the GitHub-style
aggregate tables **and** the time series — is a pure projection of this one
object. The aggregate CSVs become an export (see
:mod:`ghinsight.csv_export`), not the source of truth.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import pandas as pd

from .domain import Job, Run, Visibility
from .fetch import CachePaths

__all__ = ["ActionsDataset", "JOB_COLUMNS", "RUN_COLUMNS"]

# Column contract for the tidy job table. Listing it explicitly means the schema
# is documented in one place and an empty dataset still has the right columns.
JOB_COLUMNS: tuple[str, ...] = (
    "job_id",
    "run_id",
    "repo",
    "visibility",
    "job_name",
    "workflow_name",
    "workflow_path",
    "conclusion",
    "runner_type",
    "runtime_os",
    "runner_labels",
    "run_ms",
    "queue_ms",
    "billed_minutes",
    "is_failure",
    "started_at",
    "completed_at",
    "month",
)

RUN_COLUMNS: tuple[str, ...] = (
    "run_id",
    "repo",
    "workflow_path",
    "conclusion",
    "wall_ms",
    "has_failure",
)


def _job_to_row(job: Job, visibility: Visibility) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "run_id": job.run_id,
        "repo": job.repo,
        "visibility": visibility.value,
        "job_name": job.name,
        "workflow_name": job.workflow_name,
        "workflow_path": job.workflow_path,
        "conclusion": job.conclusion,
        "runner_type": job.runner_type.value,
        "runtime_os": job.runtime_os.value,
        "runner_labels": job.runner_labels,
        "run_ms": job.run_ms,
        "queue_ms": job.queue_ms,
        "billed_minutes": job.billed_minutes,
        "is_failure": int(job.is_failure),
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "month": job.month,
    }


# Note: no ``slots=True`` here — ``cached_property`` needs an instance ``__dict__``
# to memoise the frame views. The class is still frozen and immutable.
@dataclass(frozen=True)
class ActionsDataset:
    """An immutable, fully-parsed view of one org's Actions activity.

    Construct it with :meth:`from_cache`. The DataFrame views are computed once
    and memoised, so repeated metric calls share the same frames.

    ``visibility`` maps repo name to its :class:`~ghinsight.domain.Visibility`
    at fetch time; repos without cached metadata are ``UNKNOWN``.
    """

    org: str
    jobs: tuple[Job, ...]
    runs: tuple[Run, ...]
    visibility: Mapping[str, Visibility] = field(default_factory=dict)

    # -- constructors -------------------------------------------------------

    @classmethod
    def from_records(
        cls,
        org: str,
        jobs: Iterable[Job],
        runs: Iterable[Run],
        visibility: Mapping[str, Visibility] | None = None,
    ) -> "ActionsDataset":
        return cls(
            org=org, jobs=tuple(jobs), runs=tuple(runs), visibility=dict(visibility or {})
        )

    @classmethod
    def from_cache(cls, org: str, cache_dir: Path) -> "ActionsDataset":
        """Load and parse all cached runs and jobs for ``org``.

        Malformed records are skipped. Each job's ``workflow_path`` is resolved
        from its parent run, since the job payload only carries a name.
        """
        paths = CachePaths(cache_dir, org)
        runs = tuple(_load_runs(paths))
        path_by_run = {run.run_id: run.workflow_path for run in runs}
        jobs = tuple(_load_jobs(paths, path_by_run))
        return cls(org=org, jobs=jobs, runs=runs, visibility=dict(_load_visibility(paths)))

    # -- projections --------------------------------------------------------

    def visibility_of(self, repo: str) -> Visibility:
        return self.visibility.get(repo, Visibility.UNKNOWN)

    @property
    def has_visibility(self) -> bool:
        """Whether any repo in the dataset has known visibility."""
        return any(
            self.visibility_of(repo) is not Visibility.UNKNOWN
            for repo in {job.repo for job in self.jobs}
        )

    def private_only(self) -> "ActionsDataset":
        """The subset of repos whose minutes may count towards the plan quota.

        Keeps private, internal and unknown-visibility repos; drops public ones
        (see :attr:`Visibility.uses_quota`).
        """
        keep = {
            repo
            for repo in {job.repo for job in self.jobs} | {run.repo for run in self.runs}
            if self.visibility_of(repo).uses_quota
        }
        return ActionsDataset(
            org=self.org,
            jobs=tuple(job for job in self.jobs if job.repo in keep),
            runs=tuple(run for run in self.runs if run.repo in keep),
            visibility=self.visibility,
        )

    # -- tidy frames --------------------------------------------------------

    @cached_property
    def jobs_frame(self) -> pd.DataFrame:
        """One row per *completed* job, with all derived measures."""
        rows = [
            _job_to_row(job, self.visibility_of(job.repo))
            for job in self.jobs
            if job.is_completed
        ]
        if not rows:
            return pd.DataFrame(columns=JOB_COLUMNS)
        return pd.DataFrame.from_records(rows, columns=JOB_COLUMNS)

    @cached_property
    def hosted_jobs_frame(self) -> pd.DataFrame:
        """Completed jobs limited to GitHub-hosted runners.

        GitHub's exported metrics only include hosted activity; self-hosted jobs
        are present in the raw cache but excluded here to stay comparable.
        """
        frame = self.jobs_frame
        if frame.empty:
            return frame
        return frame[frame["runner_type"] == "hosted"].copy()

    @cached_property
    def runs_frame(self) -> pd.DataFrame:
        """One row per *completed* run, with wall-clock duration and failure flag.

        ``has_failure`` is true when any job in the run concluded as a failure.
        """
        failed_run_ids = {job.run_id for job in self.jobs if job.is_failure}
        rows = [
            {
                "run_id": run.run_id,
                "repo": run.repo,
                "workflow_path": run.workflow_path,
                "conclusion": run.conclusion,
                "wall_ms": run.wall_ms,
                "has_failure": int(run.run_id in failed_run_ids),
            }
            for run in self.runs
            if run.is_completed
        ]
        if not rows:
            return pd.DataFrame(columns=RUN_COLUMNS)
        return pd.DataFrame.from_records(rows, columns=RUN_COLUMNS)

    @property
    def is_empty(self) -> bool:
        return self.jobs_frame.empty


# ---------------------------------------------------------------------------
# Cache readers (raw JSON -> typed records)
# ---------------------------------------------------------------------------


def _iter_cached_json(paths: Sequence[Path]) -> Iterable[tuple[Path, Any]]:
    for path in paths:
        try:
            yield path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue


def _load_runs(paths: CachePaths) -> Iterable[Run]:
    org_dir = paths.org_dir
    if not org_dir.exists():
        return
    for runs_file, payloads in _iter_cached_json(sorted(org_dir.glob("*/runs.json"))):
        repo = runs_file.parent.name
        if not isinstance(payloads, list):
            continue
        for payload in payloads:
            run = Run.from_payload(payload, repo=repo)
            if run is not None:
                yield run


def _load_visibility(paths: CachePaths) -> Iterable[tuple[str, Visibility]]:
    org_dir = paths.org_dir
    if not org_dir.exists():
        return
    for meta_file, payload in _iter_cached_json(sorted(org_dir.glob("*/repo.json"))):
        if isinstance(payload, dict):
            yield meta_file.parent.name, Visibility.from_payload(payload)


def _load_jobs(paths: CachePaths, path_by_run: dict[int, str]) -> Iterable[Job]:
    org_dir = paths.org_dir
    if not org_dir.exists():
        return
    for job_file, payloads in _iter_cached_json(sorted(org_dir.glob("*/jobs/*.json"))):
        repo = job_file.parent.parent.name
        if not isinstance(payloads, list):
            continue
        for payload in payloads:
            run_id = payload.get("run_id")
            workflow_path = path_by_run.get(int(run_id), "") if run_id is not None else ""
            job = Job.from_payload(payload, repo=repo, workflow_path=workflow_path)
            if job is not None:
                yield job
