"""Stage 3a — aggregate metric tables, as pure projections of the dataset.

These reproduce GitHub's *Actions → Metrics* Usage and Performance tables,
column-for-column, so generated CSVs stay comparable with GitHub's own exports
(see :mod:`ghinsight.compare`). Each table is described *declaratively* by
a :class:`TableSpec`; one small interpreter, :func:`compute_table`, turns a spec
plus a tidy frame into the output. The workflow-level performance table needs
run-level wall-clock time and is handled by a dedicated function.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Literal

import pandas as pd

from .dataset import ActionsDataset

__all__ = [
    "Measure",
    "TableSpec",
    "compute_table",
    "usage_table",
    "performance_table",
    "USAGE_SPECS",
    "PERFORMANCE_SPECS",
    "USAGE_TABLES",
    "PERFORMANCE_TABLES",
]

How = Literal["sum", "count", "nunique", "mean"]
Source = Literal["hosted_jobs", "jobs", "runs"]


@dataclass(frozen=True, slots=True)
class Measure:
    """One aggregated output column: ``display = how(column) * scale``."""

    display: str
    column: str
    how: How
    scale: float = 1.0


@dataclass(frozen=True, slots=True)
class TableSpec:
    """A declarative description of one aggregate table.

    ``group_by`` names internal frame columns; ``key_names`` are their display
    names, in the same order. The result columns are ``key_names`` followed by
    the measures, sorted by ``sort_by`` descending.
    """

    key: str
    source: Source
    group_by: tuple[str, ...]
    key_names: tuple[str, ...]
    measures: tuple[Measure, ...]
    sort_by: str
    dropna: tuple[str, ...] = ()

    @property
    def columns(self) -> tuple[str, ...]:
        return self.key_names + tuple(m.display for m in self.measures)


def _source_frame(dataset: ActionsDataset, source: Source) -> pd.DataFrame:
    return {
        "hosted_jobs": dataset.hosted_jobs_frame,
        "jobs": dataset.jobs_frame,
        "runs": dataset.runs_frame,
    }[source]


def compute_table(dataset: ActionsDataset, spec: TableSpec) -> pd.DataFrame:
    """Interpret a :class:`TableSpec` into an output DataFrame."""
    frame = _source_frame(dataset, spec.source)
    if spec.dropna:
        frame = frame.dropna(subset=list(spec.dropna))
    if frame.empty:
        return pd.DataFrame(columns=spec.columns)

    aggregated = frame.groupby(list(spec.group_by)).agg(
        **{m.display: (m.column, m.how) for m in spec.measures}
    )
    for measure in spec.measures:
        if measure.scale != 1.0:
            aggregated[measure.display] = aggregated[measure.display] * measure.scale

    result = aggregated.reset_index().rename(
        columns=dict(zip(spec.group_by, spec.key_names))
    )
    result = result[list(spec.columns)]
    return result.sort_values(spec.sort_by, ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Usage specs (mirror GitHub Actions → Metrics → Usage)
# ---------------------------------------------------------------------------

_USAGE_COUNTS = (
    Measure("Total minutes", "billed_minutes", "sum"),
    Measure("Workflow runs", "run_id", "nunique"),
    Measure("Workflows", "workflow_path", "nunique"),
)

USAGE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        key="workflows",
        source="hosted_jobs",
        group_by=("workflow_path", "repo", "runner_type", "runtime_os"),
        key_names=("Workflow", "Source repository", "Runner type", "Runtime OS"),
        measures=(
            Measure("Total minutes", "billed_minutes", "sum"),
            Measure("Workflow runs", "run_id", "nunique"),
            Measure("Jobs", "job_name", "nunique"),
        ),
        sort_by="Total minutes",
    ),
    TableSpec(
        key="jobs",
        source="hosted_jobs",
        group_by=("job_name", "workflow_path", "repo", "runner_type", "runner_labels"),
        key_names=("Job", "Workflow", "Source repository", "Runner type", "Runner labels"),
        measures=(
            Measure("Total minutes", "billed_minutes", "sum"),
            Measure("Job runs", "job_id", "count"),
        ),
        sort_by="Total minutes",
    ),
    TableSpec(
        key="repositories",
        source="hosted_jobs",
        group_by=("repo",),
        key_names=("Source repository",),
        measures=_USAGE_COUNTS,
        sort_by="Total minutes",
    ),
    TableSpec(
        key="runtime-os",
        source="hosted_jobs",
        group_by=("runtime_os",),
        key_names=("Runtime OS",),
        measures=_USAGE_COUNTS,
        sort_by="Total minutes",
    ),
    TableSpec(
        key="runner-type",
        source="hosted_jobs",
        group_by=("runner_type",),
        key_names=("Runner type",),
        measures=_USAGE_COUNTS,
        sort_by="Total minutes",
    ),
)

# ---------------------------------------------------------------------------
# Performance specs (mirror GitHub Actions → Metrics → Performance)
# ---------------------------------------------------------------------------

_PERF_JOB_MEASURES = (
    Measure("Failure rate", "is_failure", "mean", scale=100.0),
    Measure("Avg job run time", "run_ms", "mean"),
    Measure("Avg job queue time", "queue_ms", "mean"),
    Measure("Job runs", "job_id", "count"),
)

PERFORMANCE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        key="jobs",
        source="hosted_jobs",
        group_by=("job_name", "workflow_path", "repo", "runner_type", "runner_labels"),
        key_names=("Job", "Workflow", "Source repository", "Runner type", "Runner labels"),
        measures=(
            Measure("Failure rate", "is_failure", "mean", scale=100.0),
            Measure("Avg run time", "run_ms", "mean"),
            Measure("Avg queue time", "queue_ms", "mean"),
            Measure("Job runs", "job_id", "count"),
        ),
        sort_by="Avg run time",
        dropna=("run_ms", "queue_ms"),
    ),
    TableSpec(
        key="repositories",
        source="hosted_jobs",
        group_by=("repo",),
        key_names=("Source repository",),
        measures=_PERF_JOB_MEASURES,
        sort_by="Avg job run time",
        dropna=("run_ms", "queue_ms"),
    ),
    TableSpec(
        key="runtime-os",
        source="hosted_jobs",
        group_by=("runtime_os",),
        key_names=("Runtime OS",),
        measures=_PERF_JOB_MEASURES,
        sort_by="Avg job run time",
        dropna=("run_ms", "queue_ms"),
    ),
    TableSpec(
        key="runner-type",
        source="hosted_jobs",
        group_by=("runner_type",),
        key_names=("Runner type",),
        measures=_PERF_JOB_MEASURES,
        sort_by="Avg job run time",
        dropna=("run_ms", "queue_ms"),
    ),
)

_USAGE_BY_KEY: Mapping[str, TableSpec] = {spec.key: spec for spec in USAGE_SPECS}
_PERFORMANCE_BY_KEY: Mapping[str, TableSpec] = {spec.key: spec for spec in PERFORMANCE_SPECS}


def perf_workflows(dataset: ActionsDataset) -> pd.DataFrame:
    """Per-workflow performance, using run-level wall-clock duration.

    Unlike the per-job tables, this groups *runs* (so ``Avg run time`` is the
    whole-run wall clock) and is restricted to runs that have at least one
    hosted job. The distinct hosted-job count is merged in as ``Jobs``.
    """
    runs = dataset.runs_frame
    hosted = dataset.hosted_jobs_frame
    columns = (
        "Workflow",
        "Source repository",
        "Has job failures",
        "Avg run time",
        "Workflow runs",
        "Jobs",
    )
    if runs.empty or hosted.empty:
        return pd.DataFrame(columns=columns)

    runs = runs[runs["run_id"].isin(set(hosted["run_id"]))]
    if runs.empty:
        return pd.DataFrame(columns=columns)

    grouped = runs.groupby(["workflow_path", "repo"]).agg(
        **{
            "Has job failures": ("has_failure", "mean"),
            "Avg run time": ("wall_ms", "mean"),
            "Workflow runs": ("run_id", "count"),
        }
    )
    grouped["Has job failures"] = grouped["Has job failures"] * 100.0
    result = grouped.reset_index()

    job_counts = (
        hosted.groupby(["workflow_path", "repo"])["job_name"]
        .nunique()
        .reset_index()
        .rename(columns={"job_name": "Jobs"})
    )
    result = result.merge(job_counts, on=["workflow_path", "repo"], how="left")
    result = result.rename(
        columns={"workflow_path": "Workflow", "repo": "Source repository"}
    )
    result = result[list(columns)]
    return result.sort_values("Avg run time", ascending=False).reset_index(drop=True)


def usage_table(dataset: ActionsDataset, key: str) -> pd.DataFrame:
    """Compute one usage table by key (``workflows``, ``jobs``, ...)."""
    return compute_table(dataset, _USAGE_BY_KEY[key])


def performance_table(dataset: ActionsDataset, key: str) -> pd.DataFrame:
    """Compute one performance table by key. ``workflows`` is run-level."""
    if key == "workflows":
        return perf_workflows(dataset)
    return compute_table(dataset, _PERFORMANCE_BY_KEY[key])


# Callable catalogs keyed by table name, used by the CLI and CSV export.
TableFn = Callable[[ActionsDataset], pd.DataFrame]

USAGE_TABLES: Mapping[str, TableFn] = {
    spec.key: (lambda ds, k=spec.key: usage_table(ds, k)) for spec in USAGE_SPECS
}
PERFORMANCE_TABLES: Mapping[str, TableFn] = {
    "workflows": perf_workflows,
    **{spec.key: (lambda ds, k=spec.key: performance_table(ds, k)) for spec in PERFORMANCE_SPECS},
}
