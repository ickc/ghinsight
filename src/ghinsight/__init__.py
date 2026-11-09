"""GitHub Actions metrics analysis library.

A three-stage, functional pipeline:

1. **fetch** (:mod:`ghinsight.fetch`) — cache raw runs/jobs from GitHub.
2. **dataset** (:mod:`ghinsight.dataset`) — parse the cache into the
   canonical tidy intermediate representation, :class:`ActionsDataset`.
3. **analysis** — derive metric tables (:mod:`ghinsight.metrics`),
   insights (:mod:`ghinsight.analysis`), and a report
   (:mod:`ghinsight.report`) — all pure projections of the dataset.
"""

from __future__ import annotations

from .analysis import UsageSummary, summarize_usage
from .config import AnalysisConfig, DateRange, load_config, parse_period
from .csv_export import write_reports
from .dataset import ActionsDataset
from .domain import Job, Run, RunnerType, RuntimeOS, Visibility
from .metrics import performance_table, usage_table
from .report import build_dashboard, render_html

__all__ = [
    "ActionsDataset",
    "AnalysisConfig",
    "DateRange",
    "Job",
    "Run",
    "RunnerType",
    "RuntimeOS",
    "UsageSummary",
    "Visibility",
    "build_dashboard",
    "load_config",
    "parse_period",
    "performance_table",
    "render_html",
    "summarize_usage",
    "usage_table",
    "write_reports",
]
