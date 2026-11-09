# ---
# jupytext:
#   text_representation:
#     extension: .py
#     format_name: percent
#     format_version: '1.3'
# kernelspec:
#   display_name: Python 3
#   language: python
#   name: python3
# ---

# %% [markdown]
# # Tutorial: analysis with the Python API
#
# This notebook shows how to drive the first two pipeline stages and the start
# of the third **programmatically**, then reproduce some of the report's
# insights directly from the canonical intermediate representation,
# {py:class}`~ghinsight.actions.dataset.ActionsDataset`.
#
# In real use you would populate the cache with
# `ghinsight actions fetch --org <name>` (which needs a GitHub token). So that this
# page builds offline, we synthesise a tiny cache with the *same on-disk layout*
# that `fetch` produces, then run the rest of the pipeline against it unchanged.

# %%
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ORG = "demo-org"
cache_dir = Path(tempfile.mkdtemp()) / "cache"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# (repo, workflow, runner_group, labels, conclusion, run_minutes, (y, m, d))
jobs = [
    ("api", "ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 4.0, (2024, 1, 5)),
    ("api", "ci.yml", "GitHub Actions", ["ubuntu-latest"], "failure", 6.0, (2024, 1, 20)),
    ("api", "ci.yml", "GitHub Actions", ["windows-latest"], "success", 3.0, (2024, 2, 8)),
    ("api", "release.yml", "GitHub Actions", ["macos-latest"], "success", 5.0, (2024, 2, 18)),
    ("web", "ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 2.0, (2024, 1, 9)),
    ("web", "ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 7.0, (2024, 2, 27)),
]

for i, (repo, wf, group, labels, concl, minutes, ymd) in enumerate(jobs):
    run_id, job_id = 1000 + i, 2000 + i
    created = datetime(*ymd, 9, tzinfo=timezone.utc)
    started = created + timedelta(seconds=30)
    completed = started + timedelta(minutes=minutes)
    repo_dir = cache_dir / ORG / repo
    (repo_dir / "jobs").mkdir(parents=True, exist_ok=True)
    runs_file = repo_dir / "runs.json"
    runs = json.loads(runs_file.read_text()) if runs_file.exists() else []
    runs.append(
        {
            "id": run_id,
            "path": f".github/workflows/{wf}",
            "conclusion": concl,
            "created_at": _iso(created),
            "run_started_at": _iso(started),
            "updated_at": _iso(completed),
        }
    )
    runs_file.write_text(json.dumps(runs))
    (repo_dir / "jobs" / f"{run_id}.json").write_text(
        json.dumps(
            [
                {
                    "id": job_id,
                    "run_id": run_id,
                    "name": "build",
                    "workflow_name": wf.removesuffix(".yml"),
                    "status": "completed",
                    "conclusion": concl,
                    "runner_group_name": group,
                    "labels": labels,
                    "created_at": _iso(created),
                    "started_at": _iso(started),
                    "completed_at": _iso(completed),
                }
            ]
        )
    )

print("synthetic cache at", cache_dir)

# %% [markdown]
# ## Stage 2 — load the intermediate representation
#
# `ActionsDataset.from_cache` parses the cached JSON into typed records and
# exposes them as tidy DataFrames. This object is the single source of truth for
# every analysis below.

# %%
from ghinsight.actions.dataset import ActionsDataset

dataset = ActionsDataset.from_cache(ORG, cache_dir)
print(f"{len(dataset.jobs)} jobs, {len(dataset.runs)} runs")
dataset.hosted_jobs_frame[
    ["repo", "workflow_path", "runtime_os", "billed_minutes", "is_failure", "month"]
]

# %% [markdown]
# Each row is one completed, GitHub-hosted job. Derived measures such as
# `billed_minutes` come straight from the {py:class}`~ghinsight.actions.domain.Job`
# record's properties, so they are defined in exactly one place.

# %% [markdown]
# ## Stage 3a — metric tables (projections)
#
# The same tables the CLI exports as CSVs are available as DataFrames.

# %%
from ghinsight.actions.metrics import performance_table, usage_table

usage_table(dataset, "repositories")

# %%
performance_table(dataset, "runtime-os")

# %% [markdown]
# ## Stage 3b — headline insights
#
# {py:func}`~ghinsight.actions.analysis.summarize_usage` computes the KPI numbers
# shown on the dashboard cards. It needs an
# {py:class}`~ghinsight.actions.config.AnalysisConfig` for the OS multipliers and
# plan cap.

# %%
from ghinsight.actions.analysis import billed_equivalent_by_os, summarize_usage
from ghinsight.actions.config import AnalysisConfig

config = AnalysisConfig(
    org=ORG,
    root=cache_dir.parent,
    cache_dir=cache_dir,
    reports_dir=cache_dir.parent / "reports",
    data_dir=cache_dir.parent / "reports",
    output_html=cache_dir.parent / "docs" / "index.html",
    summary_json=cache_dir.parent / "docs" / "summary.json",
    os_multipliers={"linux": 1.0, "windows": 2.0, "macos": 10.0},
    plan_minutes=5.0,
)

summary = summarize_usage(dataset, config)
summary

# %%
billed_equivalent_by_os(dataset, config.os_multipliers)

# %% [markdown]
# Note how the macOS multiplier (×10) dominates billed-equivalent minutes even
# though macOS has few raw minutes — exactly the kind of insight the dashboard
# surfaces.

# %% [markdown]
# ## Reproduce a dashboard chart
#
# Every chart is a pure `data -> Figure` function. Here is the OS raw-vs-billed
# comparison, built from the frame above.

# %%
from IPython.display import HTML

from ghinsight.actions.charts import os_raw_vs_billed_bar

fig = os_raw_vs_billed_bar(billed_equivalent_by_os(dataset, config.os_multipliers))
# Render as HTML so the interactive chart embeds in the docs (myst-nb does not
# render Plotly's native mime type).
HTML(fig.to_html(include_plotlyjs="cdn", full_html=False))

# %% [markdown]
# ## From insight to full report
#
# When you want the whole dashboard, hand the config to
# {py:func}`~ghinsight.actions.report.build_dashboard`, which writes the HTML and
# JSON summary and returns the metadata.

# %%
from ghinsight.actions.report import build_dashboard

metadata = build_dashboard(config)
{k: metadata[k] for k in ("raw_total_minutes_period", "billed_equivalent_minutes_period")}
