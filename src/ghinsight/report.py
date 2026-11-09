"""Stage 3c — assemble the static HTML dashboard and JSON summary.

This module is a thin *assembler*: it loads the dataset, asks
:mod:`ghinsight.analysis` for the numbers, asks
:mod:`ghinsight.charts` for the figures, and stitches them into HTML. It
deliberately contains no metric maths of its own, so the report's content is
auditable in the analysis layer and only its *presentation* lives here.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
from plotly.graph_objects import Figure
from plotly.io import to_html

from . import charts
from .analysis import (
    UsageSummary,
    billed_equivalent_by_os,
    billing_monthly_usage,
    monthly_comparison,
    monthly_usage,
    summarize_usage,
)
from .billing import BillingUsage
from .config import AnalysisConfig
from .dataset import ActionsDataset
from .metrics import performance_table, usage_table

__all__ = ["build_dashboard", "render_html", "summary_metadata"]

_TOP_REPOS = 12
_HEAVY_JOBS = 25


_PRIVATE_NOTE = (
    "Private-only views drop public repositories (whose standard hosted-runner "
    "minutes are free) and keep private, internal and unknown-visibility ones."
)


def _fig_html(fig: Figure, *, include_js: bool = False) -> str:
    return to_html(fig, include_plotlyjs="cdn" if include_js else False, full_html=False)


def _html_list(items: Sequence[str]) -> str:
    return "".join(f"<li>{item}</li>" for item in items)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# Derived frames for the report
# ---------------------------------------------------------------------------


def _repo_share(usage_repos: pd.DataFrame, raw_total: float) -> pd.DataFrame:
    share = usage_repos.sort_values("Total minutes", ascending=False).copy()
    share["Share %"] = share["Total minutes"] / raw_total * 100 if raw_total else 0.0
    return share


def _workflow_billed(
    usage_workflows: pd.DataFrame, multipliers: Mapping[str, float]
) -> pd.DataFrame:
    cost = usage_workflows.groupby(
        ["Source repository", "Workflow", "Runtime OS"], as_index=False
    )["Total minutes"].sum()
    cost["Multiplier"] = (
        cost["Runtime OS"].astype(str).str.lower().map(multipliers).fillna(1.0)
    )
    cost["Billed equivalent minutes"] = cost["Total minutes"] * cost["Multiplier"]
    return (
        cost.groupby(["Source repository", "Workflow"], as_index=False)[
            "Billed equivalent minutes"
        ]
        .sum()
        .sort_values(
            ["Source repository", "Billed equivalent minutes"], ascending=[True, False]
        )
    )


def _monthly_aggregate(monthly: pd.DataFrame) -> pd.DataFrame:
    agg = (
        monthly.groupby("month")
        .agg(
            adj_billed=("adj_billed", "sum"),
            raw_billed=("billed_minutes", "sum"),
            runs=("is_failure", "count"),
            failures=("is_failure", "sum"),
        )
        .reset_index()
    )
    agg["failure_rate"] = agg["failures"] / agg["runs"] * 100
    return agg


# ---------------------------------------------------------------------------
# Scopes: all repositories vs private repositories only
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scopes:
    """The dataset in both scopes; ``private`` is ``None`` without visibility."""

    all: ActionsDataset
    private: ActionsDataset | None

    @classmethod
    def of(cls, dataset: ActionsDataset) -> "_Scopes":
        return cls(dataset, dataset.private_only() if dataset.has_visibility else None)

    def figure(self, build: Callable[[ActionsDataset], Figure]) -> Figure:
        """Build a chart per scope; with both, join them under a scope toggle."""
        if self.private is None:
            return build(self.all)
        return charts.scope_toggle(build(self.all), build(self.private))


@dataclass(frozen=True)
class _BillingView:
    """The billing report projected for the dashboard.

    Taken as billed, with no visibility filter: the report appears to omit
    free public-repository usage, so it is already the quota use.
    """

    usage: BillingUsage
    monthly: pd.DataFrame  # billing_monthly_usage frame

    @property
    def total(self) -> float:
        return float(self.monthly["adj_billed"].sum())

    @property
    def monthly_average(self) -> float:
        months = len(self.usage.months)
        return self.total / months if months else 0.0

    @property
    def net_charge(self) -> float:
        return float(self.monthly["net_amount"].sum())

    def months_at_cap(self, plan_minutes: float) -> list[str]:
        """Months whose billed-equivalent minutes reached ``plan_minutes``.

        Billed usage stops at the cap when a spending limit blocks further
        runs, so these months had demand the plan did not serve.
        """
        by_month = self.monthly.groupby("month")["adj_billed"].sum()
        # Tolerance for fractional minutes summing to just under the cap.
        return sorted(by_month[by_month >= plan_minutes - 0.5].index)


# ---------------------------------------------------------------------------
# Text blocks
# ---------------------------------------------------------------------------


def _insights(
    summary: UsageSummary,
    private_summary: UsageSummary | None,
    billing: _BillingView | None,
    config: AnalysisConfig,
) -> list[str]:
    items = [
        f"Raw exported usage over {escape(config.period_label)} is "
        f"<b>{summary.raw_total_minutes:,.0f} minutes</b>.",
        "After the configured OS billing multipliers, billed-equivalent usage is "
        f"<b>{summary.billed_equivalent_minutes:,.0f} minutes</b> for the same period.",
        "Estimated monthly billed demand is "
        f"<b>{summary.estimated_monthly_billed:,.0f} minutes/month</b> when the "
        "selected period is treated as an annual window.",
    ]
    if config.plan_minutes:
        items.append(
            f"Under the configured {config.plan_minutes:,.0f}-minute monthly cap: "
            f"{summary.cap_status_text}."
        )
    if private_summary is not None:
        text = (
            "Counting private repositories only, which is what uses the plan "
            f"quota, billed-equivalent usage is "
            f"<b>{private_summary.billed_equivalent_minutes:,.0f} minutes</b> "
            f"(<b>{private_summary.estimated_monthly_billed:,.0f} minutes/month</b>)"
        )
        if config.plan_minutes:
            text += f"; {private_summary.cap_status_text}"
        items.append(text + ".")
    if billing is not None:
        months = len(billing.usage.months)
        text = (
            f"GitHub's billing usage report covers <b>{months}</b> "
            f"month{'s' if months != 1 else ''} and records "
            f"<b>{billing.total:,.0f}</b> billed-equivalent minutes counted towards "
            f"the quota (<b>{billing.monthly_average:,.0f} minutes/month</b> on average"
        )
        if config.plan_minutes:
            text += f", {billing.monthly_average / config.plan_minutes * 100:.0f}% of the cap"
        text += f"). The net Actions charge was <b>${billing.net_charge:,.2f}</b>."
        items.append(text)
        if config.plan_minutes:
            at_cap = billing.months_at_cap(config.plan_minutes)
            if at_cap:
                consequence = (
                    "With no net charge, runs beyond the cap in those months were "
                    "most likely blocked, so demand was higher than the billed "
                    "figures show."
                    if billing.net_charge == 0
                    else "Usage beyond the cap is charged, as the net charge shows."
                )
                items.append(
                    f"Billed usage reached the {config.plan_minutes:,.0f}-minute cap in "
                    f"<b>{', '.join(at_cap)}</b>. {consequence}"
                )
    items.extend(config.extra_insights_html)
    return items


def _limitations(
    config: AnalysisConfig,
    *,
    has_visibility: bool = False,
    billing: BillingUsage | None = None,
) -> list[str]:
    items = [
        "Headline KPI cards treat the selected period as an annual window to "
        "estimate monthly demand; a shorter or longer period scales accordingly.",
        "Monthly charts use job start timestamps from the local cache and may "
        "differ from GitHub UI exports due to per-job minute rounding and "
        "skipped or cancelled jobs.",
    ]
    if config.plan_minutes:
        items.append(
            "The monthly cap simulation uses annual-average demand; actual "
            "month-by-month billing may differ."
        )
    if has_visibility:
        items.append(
            f"{_PRIVATE_NOTE} Visibility is as of the last fetch: a repository "
            "made public or private during the period is classified by its "
            "current visibility for the whole period."
        )
    else:
        items.append(
            "Repository visibility was not cached, so public and private "
            "repositories cannot be separated; minutes from public repositories, "
            "which do not count towards the plan quota, are included throughout. "
            "Re-fetch to record visibility."
        )
    if billing is None:
        items.append(
            "GitHub's billing usage report was not available (fetch it as an "
            "organisation owner, or import the usage report CSV), so all minutes "
            "are estimated from job durations."
        )
    else:
        if has_visibility:
            items.append(
                "Where they differ, prefer the billed figures to the private-only "
                "estimates: billing reflects each repository's visibility at the "
                "time, and includes repositories since transferred out or deleted."
            )
        if billing.missing_months:
            items.append(
                "The billing usage report is missing for "
                f"{', '.join(billing.missing_months)}; billing figures cover only "
                "the months shown."
            )
    items.extend(config.extra_limitations_html)
    return items


def _cards(
    summary: UsageSummary,
    private_summary: UsageSummary | None,
    billing: _BillingView | None,
    config: AnalysisConfig,
) -> list[tuple[str, str]]:
    cards = [
        ("Raw minutes", f"{summary.raw_total_minutes:,.0f}"),
        ("Billed-equivalent minutes", f"{summary.billed_equivalent_minutes:,.0f}"),
        ("Estimated monthly billed demand", f"{summary.estimated_monthly_billed:,.0f}"),
    ]
    if config.plan_minutes and summary.plan_pct is not None:
        cards.append(
            (f"Plan cap ({config.plan_minutes:,.0f} min/month)", f"{summary.plan_pct:.0f}% used avg")
        )
    if private_summary is not None:
        cards.append(
            (
                "Billed-equivalent minutes, private repos",
                f"{private_summary.billed_equivalent_minutes:,.0f}",
            )
        )
        if config.plan_minutes and private_summary.plan_pct is not None:
            cards.append(("Plan cap, private repos", f"{private_summary.plan_pct:.0f}% used avg"))
    if billing is not None:
        cards.append(("Billed minutes/month (GitHub report)", f"{billing.monthly_average:,.0f}"))
        if config.plan_minutes:
            pct = billing.monthly_average / config.plan_minutes * 100
            cards.append(("Plan cap, as billed", f"{pct:.0f}% used avg"))
    return cards


def _billing_html(billing: _BillingView, config: AnalysisConfig) -> str:
    fig = charts.monthly_billed_by_os(
        billing.monthly,
        config.os_multipliers,
        config.plan_minutes,
        title="Monthly billed-equivalent minutes by OS (GitHub billing report)",
    )
    return f"""
  <h2>Billed usage from GitHub's billing report</h2>
  <p class="muted">Minutes GitHub actually billed, per its billing usage report,
  with the configured OS multipliers applied. The report appears to omit free
  public-repository usage, so these are the minutes that counted towards the
  quota, with each repository's visibility as it was at the time. Unlike the
  estimates, they include repositories since transferred out or deleted.</p>
  {_fig_html(fig)}
"""


def _comparison_table(
    monthly: pd.DataFrame, billing: _BillingView | None, has_visibility: bool
) -> pd.DataFrame:
    table = monthly_comparison(monthly, billing.monthly if billing is not None else None)
    if not has_visibility:
        table = table.drop(columns=[c for c in table.columns if "private only" in c])
    return table


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def render_html(
    dataset: ActionsDataset,
    config: AnalysisConfig,
    billing: BillingUsage | None = None,
) -> tuple[str, dict[str, Any]]:
    """Render the dashboard HTML and return ``(html, summary_metadata)``.

    ``billing`` is the optional billing usage report; without it every figure
    is estimated from job durations.
    """
    multipliers = config.os_multipliers
    scopes = _Scopes.of(dataset)
    summary = summarize_usage(dataset, config)
    private_summary = (
        summarize_usage(scopes.private, config) if scopes.private is not None else None
    )
    billing_view = (
        _BillingView(
            billing,
            billing_monthly_usage(billing.actions_minutes_frame(dataset.visibility), multipliers),
        )
        if billing is not None
        else None
    )

    usage_repos = usage_table(dataset, "repositories")
    usage_workflows = usage_table(dataset, "workflows")
    usage_jobs = usage_table(dataset, "jobs")
    by_os = billed_equivalent_by_os(dataset, multipliers)
    perf_repos = performance_table(dataset, "repositories")
    perf_os = performance_table(dataset, "runtime-os")

    repo_share = _repo_share(usage_repos, summary.raw_total_minutes)
    top_repos = repo_share.head(_TOP_REPOS)
    heavy_jobs = usage_jobs.sort_values("Total minutes", ascending=False).head(_HEAVY_JOBS)
    workflow_billed = _workflow_billed(usage_workflows, multipliers)

    def top_repos_of(ds: ActionsDataset) -> pd.DataFrame:
        raw_total = summarize_usage(ds, config).raw_total_minutes
        return _repo_share(usage_table(ds, "repositories"), raw_total).head(_TOP_REPOS)

    # -- period-level figures ----------------------------------------------
    fig_repo = scopes.figure(
        lambda ds: charts.repo_minutes_bar(top_repos_of(ds), config.period_label)
    )
    fig_os = scopes.figure(
        lambda ds: charts.os_raw_vs_billed_bar(billed_equivalent_by_os(ds, multipliers))
    )
    fig_workflow = charts.workflow_explorer(workflow_billed)
    fig_perf = charts.reliability_scatter(perf_repos, config.period_label)
    fig_queue = charts.queue_by_os_bar(perf_os, config.period_label)

    toggle_note = (
        f'  <p class="muted">Use the buttons above a chart to switch between all '
        f"repositories and private repositories only. {_PRIVATE_NOTE}</p>\n"
        if scopes.private is not None
        else ""
    )

    cap_html = ""
    if config.plan_minutes:
        fig_cap = scopes.figure(
            lambda ds: charts.cap_simulation(summarize_usage(ds, config), config.days_per_month)
        )
        cap_html = (
            "\n  <h2>Monthly cap simulation (annual-average demand)</h2>\n"
            '  <p class="muted">Based on annual-average demand, not individual months.</p>\n'
            f"  {_fig_html(fig_cap)}\n"
        )

    # -- monthly figures (derived from the tidy IR) ------------------------
    monthly = monthly_usage(dataset, multipliers)
    monthly_html = ""
    monthly_metadata: dict[str, Any] = {}
    comparison = pd.DataFrame()
    if not monthly.empty:
        monthly_agg = _monthly_aggregate(monthly)
        months_sorted = sorted(monthly["month"].unique())
        fig_m_billed = scopes.figure(
            lambda ds: charts.monthly_billed_by_os(
                monthly_usage(ds, multipliers), multipliers, config.plan_minutes
            )
        )
        fig_m_failure = charts.monthly_failure_rate(monthly_agg)
        fig_m_runs = charts.monthly_runs(monthly_agg)
        fig_m_last = charts.last_success_day(monthly)
        fig_m_repos = charts.monthly_repository_explorer(monthly, months_sorted)
        monthly_html = f"""
  <h2>Monthly trends (from job cache)</h2>
  <p class="muted">Charts below use raw job timestamps from the local cache.
  Billed-equivalent minutes apply the configured OS multipliers.</p>
  {_fig_html(fig_m_billed)}
  {_fig_html(fig_m_failure)}
  {_fig_html(fig_m_runs)}
  {_fig_html(fig_m_last)}

  <h2>Monthly repository breakdown</h2>
  <p class="muted">Select a month from the dropdown to see which repositories
  consumed the most billed-equivalent minutes in that period.</p>
  {_fig_html(fig_m_repos)}
"""
        monthly_metadata = {
            "months": months_sorted,
            "monthly_billed_equivalent_minutes": {
                row["month"]: float(row["adj_billed"]) for _, row in monthly_agg.iterrows()
            },
        }
        comparison = _comparison_table(monthly, billing_view, scopes.private is not None)

    billing_html = _billing_html(billing_view, config) if billing_view else ""
    comparison_html = ""
    if not comparison.empty and (billing_view is not None or scopes.private is not None):
        comparison_html = f"""
  <h2>Monthly billed-equivalent minutes by scope</h2>
  <p class="muted">"Estimated" is derived from job durations; "Billed" is from
  GitHub's billing usage report, where available.</p>
  {comparison.to_html(index=False, na_rep="-", float_format=lambda v: f"{v:,.0f}")}
"""

    # -- tables ------------------------------------------------------------
    summary_table = top_repos[
        ["Source repository", "Total minutes", "Workflow runs", "Workflows", "Share %"]
    ].to_html(index=False, float_format=lambda v: f"{v:,.1f}")
    os_table = by_os[
        ["Runtime OS", "Total minutes", "Multiplier", "Billed equivalent minutes"]
    ].to_html(index=False)
    heavy_jobs_table = heavy_jobs[
        ["Source repository", "Workflow", "Job", "Total minutes", "Job runs", "Runner labels"]
    ].to_html(index=False)

    cards_html = "".join(
        f'<div class="card"><div class="muted">{escape(label)}</div>'
        f"<div><b>{value}</b></div></div>"
        for label, value in _cards(summary, private_summary, billing_view, config)
    )
    provenance = _relative(config.cache_dir / config.org, config.root)
    limitations = _limitations(
        config, has_visibility=scopes.private is not None, billing=billing
    )

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(config.title)}</title>
  <style>
    body {{ font-family: Inter, Arial, sans-serif; margin: 24px; max-width: 1200px; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 16px 0 24px; }}
    .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px; background: #fafafa; }}
    .muted {{ color: #555; font-size: 0.9rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 8px 0 20px; font-size: 0.9rem; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; text-align: left; }}
    th {{ background: #f2f2f2; }}
  </style>
</head>
<body>
  <h1>{escape(config.title)}</h1>
  <p class="muted">Generated from cached job data in <code>{escape(provenance)}</code>.</p>

  <div class="cards">
    {cards_html}
  </div>

  <h2>Key insights</h2>
  <ul>{_html_list(_insights(summary, private_summary, billing_view, config))}</ul>

  <h2>Important limitations</h2>
  <ul>{_html_list(limitations)}</ul>

  <h2>Repository usage - {escape(config.period_label)}</h2>
{toggle_note}  {_fig_html(fig_repo, include_js=True)}

  <h2>OS multiplier impact - {escape(config.period_label)}</h2>
  <p class="muted">Billed-equivalent minutes apply the configured OS multipliers.</p>
  {_fig_html(fig_os)}

  <h2>Workflow explorer - {escape(config.period_label)}</h2>
  {_fig_html(fig_workflow)}
{cap_html}{monthly_html}{billing_html}{comparison_html}
  <h2>Reliability and performance - {escape(config.period_label)}</h2>
  {_fig_html(fig_perf)}
  {_fig_html(fig_queue)}

  <h2>Top repositories table - {escape(config.period_label)}</h2>
  {summary_table}

  <h2>OS multiplier table - {escape(config.period_label)}</h2>
  {os_table}

  <h2>Highest-cost jobs - {escape(config.period_label)}</h2>
  {heavy_jobs_table}
</body>
</html>
"""
    metadata = summary_metadata(
        summary,
        config,
        monthly_metadata,
        private_summary=private_summary,
        comparison=comparison,
        billing=billing_view,
        limitations=limitations,
    )
    return html, metadata


def _summary_fields(summary: UsageSummary) -> dict[str, Any]:
    return {
        "raw_total_minutes_period": summary.raw_total_minutes,
        "billed_equivalent_minutes_period": summary.billed_equivalent_minutes,
        "estimated_monthly_billed_minutes": summary.estimated_monthly_billed,
        "estimated_monthly_pct_of_cap": (
            round(summary.plan_pct, 1) if summary.plan_pct is not None else None
        ),
        "cap_reached_on_average_month": summary.cap_reached,
        "estimated_unserved_billed_minutes_month": summary.estimated_unserved_billed,
    }


def _column_by_month(table: pd.DataFrame, column: str) -> dict[str, float]:
    if column not in table.columns:
        return {}
    return {
        row["Month"]: float(row[column]) for _, row in table.iterrows() if pd.notna(row[column])
    }


def summary_metadata(
    summary: UsageSummary,
    config: AnalysisConfig,
    monthly_metadata: Mapping[str, Any],
    *,
    private_summary: UsageSummary | None = None,
    comparison: pd.DataFrame | None = None,
    billing: _BillingView | None = None,
    limitations: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Machine-readable summary written alongside the HTML report.

    ``private_only`` is ``None`` when repository visibility is unknown, and
    ``billing_report`` is ``None`` when the billing usage report is unavailable.
    """
    comparison = comparison if comparison is not None else pd.DataFrame()
    private_only = None
    if private_summary is not None:
        private_only = {
            **_summary_fields(private_summary),
            "monthly_billed_equivalent_minutes": _column_by_month(
                comparison, "Estimated, private only"
            ),
        }
    billing_report = None
    if billing is not None:
        billing_report = {
            "months": list(billing.usage.months),
            "missing_months": list(billing.usage.missing_months),
            "billed_equivalent_minutes_period": billing.total,
            "estimated_monthly_billed_minutes": billing.monthly_average,
            "monthly_billed_equivalent_minutes": _column_by_month(
                comparison, "Billed (GitHub report)"
            ),
            "actions_net_charge_usd": billing.net_charge,
            "months_at_cap": (
                billing.months_at_cap(config.plan_minutes) if config.plan_minutes else []
            ),
        }
    return {
        "org": config.org,
        "period": config.period,
        "period_label": config.period_label,
        **{k: v for k, v in _summary_fields(summary).items() if k.endswith("_period")},
        "plan_minutes": config.plan_minutes,
        **{k: v for k, v in _summary_fields(summary).items() if not k.endswith("_period")},
        "os_multipliers": dict(config.os_multipliers),
        "limitations": list(limitations) if limitations is not None else _limitations(config),
        **monthly_metadata,
        "private_only": private_only,
        "billing_report": billing_report,
    }


def build_dashboard(config: AnalysisConfig) -> dict[str, Any]:
    """Load the dataset, render the dashboard, and write HTML + JSON to disk.

    The billing usage report is included when it has been cached. Returns the
    summary metadata.
    """
    dataset = ActionsDataset.from_cache(config.org, config.cache_dir)
    if dataset.is_empty:
        raise FileNotFoundError(
            f"No cached job data found under {config.cache_dir / config.org}. "
            "Run 'ghinsight fetch' first."
        )
    billing = BillingUsage.from_cache(config.org, config.cache_dir, config.date_range)

    html, metadata = render_html(dataset, config, billing)

    config.output_html.parent.mkdir(parents=True, exist_ok=True)
    config.output_html.write_text(html, encoding="utf-8")
    config.summary_json.parent.mkdir(parents=True, exist_ok=True)
    config.summary_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
