"""Plotly figure builders — each a pure ``data -> Figure`` function.

Keeping figure construction free of I/O (no file reads, no config objects beyond
plain values) makes every chart independently testable and lets the report
module stay a thin assembler. The monthly helpers take the long-form frame from
:func:`ghinsight.actions.analysis.monthly_usage` and do their own grouping.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .analysis import UsageSummary

__all__ = [
    "repo_minutes_bar",
    "os_raw_vs_billed_bar",
    "workflow_explorer",
    "cap_simulation",
    "reliability_scatter",
    "queue_by_os_bar",
    "monthly_billed_by_os",
    "monthly_failure_rate",
    "monthly_runs",
    "last_success_day",
    "monthly_repository_explorer",
    "scope_toggle",
    "SCOPE_LABELS",
]

_OS_ORDER = ("linux", "windows", "macos", "unknown")
_OS_COLORS = {
    "linux": "#4C78A8",
    "windows": "#72B7B2",
    "macos": "#F58518",
    "unknown": "#BAB0AC",
}


SCOPE_LABELS = ("All repositories", "Private repositories only")


def _scoped_title(title: str, label: str) -> str:
    return f"{title} · {label.lower()}" if title else ""


def scope_toggle(
    all_fig: go.Figure,
    private_fig: go.Figure,
    labels: Sequence[str] = SCOPE_LABELS,
) -> go.Figure:
    """Combine two versions of a chart into one with a two-button scope toggle.

    ``all_fig`` and ``private_fig`` are the same chart built from all repos and
    from private repos only. The result starts on ``all_fig`` and the buttons
    swap which traces are visible, along with the title. The layout (axes,
    shapes such as a plan-cap line) is taken from ``all_fig``; the y-axis
    autoscales on each switch.
    """
    # Traces a builder hid itself stay hidden in both scopes.
    shown = [
        (trace.visible is not False, in_all)
        for in_all, source in ((True, all_fig), (False, private_fig))
        for trace in source.data
    ]
    fig = go.Figure(layout=all_fig.layout)
    for trace in all_fig.data:
        fig.add_trace(trace)
    for trace in private_fig.data:
        fig.add_trace(trace)
        fig.data[-1].visible = False
    titles = (all_fig.layout.title.text or "", private_fig.layout.title.text or "")

    def button(label: str, show_all: bool, title: str) -> dict:
        visible = [base and in_all == show_all for base, in_all in shown]
        relayout = {
            "title.text": _scoped_title(title, label),
            "xaxis.autorange": True,
            "yaxis.autorange": True,
        }
        return {"label": label, "method": "update", "args": [{"visible": visible}, relayout]}

    fig.update_layout(
        title_text=_scoped_title(titles[0], labels[0]) or None,
        updatemenus=[
            {
                "type": "buttons",
                "direction": "right",
                "buttons": [
                    button(labels[0], True, titles[0]),
                    button(labels[1], False, titles[1]),
                ],
                "showactive": True,
                "active": 0,
                "x": 0.0,
                "xanchor": "left",
                "y": 1.02,
                "yanchor": "bottom",
            }
        ],
    )
    return fig


def _month_length(year_month: str) -> int:
    year, month = int(year_month[:4]), int(year_month[5:7])
    return calendar.monthrange(year, month)[1]


# ---------------------------------------------------------------------------
# Period-level figures
# ---------------------------------------------------------------------------


def repo_minutes_bar(top_repos: pd.DataFrame, period_label: str) -> go.Figure:
    """Bar chart of top repositories by raw Actions minutes."""
    return px.bar(
        top_repos,
        x="Source repository",
        y="Total minutes",
        color="Total minutes",
        color_continuous_scale="Blues",
        title=f"Top repositories by raw Actions minutes ({period_label})",
        hover_data={"Share %": ":.1f"},
    )


def os_raw_vs_billed_bar(by_os: pd.DataFrame) -> go.Figure:
    """Grouped bars: raw minutes vs multiplier-adjusted minutes, per OS."""
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Raw minutes", x=by_os["Runtime OS"], y=by_os["Total minutes"]))
    fig.add_trace(
        go.Bar(
            name="Billed-equivalent minutes",
            x=by_os["Runtime OS"],
            y=by_os["Billed equivalent minutes"],
        )
    )
    fig.update_layout(
        barmode="group",
        title="Raw vs multiplier-adjusted minutes by OS",
        xaxis_title="Runtime OS",
        yaxis_title="Minutes",
    )
    return fig


def workflow_explorer(workflow_billed: pd.DataFrame) -> go.Figure:
    """Per-repo dropdown of workflow billed-equivalent minutes.

    ``workflow_billed`` must have columns ``Source repository``, ``Workflow``,
    ``Billed equivalent minutes``.
    """
    repos = sorted(workflow_billed["Source repository"].unique())
    fig = go.Figure()
    buttons = []
    for idx, repo in enumerate(repos):
        sub = workflow_billed[workflow_billed["Source repository"] == repo]
        fig.add_trace(
            go.Bar(
                x=sub["Workflow"],
                y=sub["Billed equivalent minutes"],
                name=repo,
                visible=(idx == 0),
            )
        )
        visible = [i == idx for i in range(len(repos))]
        buttons.append(
            {
                "label": repo,
                "method": "update",
                "args": [
                    {"visible": visible},
                    {"title": f"Workflow billed-equivalent minutes - {repo}"},
                ],
            }
        )
    fig.update_layout(
        title=f"Workflow billed-equivalent minutes - {repos[0] if repos else 'N/A'}",
        updatemenus=[{"buttons": buttons, "x": 1.02, "y": 1.0, "xanchor": "left"}],
        xaxis_title="Workflow",
        yaxis_title="Billed-equivalent minutes",
        showlegend=False,
    )
    return fig


def cap_simulation(summary: UsageSummary, days_per_month: float) -> go.Figure:
    """Monthly cap simulation from annual-average daily demand."""
    plan = summary.plan_minutes or 0.0
    days = pd.DataFrame({"Day": range(1, 32)})
    days["Projected billed usage"] = days["Day"] * summary.estimated_daily_billed
    days["Usable under plan cap"] = days["Projected billed usage"].clip(upper=plan)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=days["Day"], y=days["Projected billed usage"], mode="lines", name="Projected demand"
        )
    )
    fig.add_trace(
        go.Scatter(
            x=days["Day"], y=days["Usable under plan cap"], mode="lines", name="Usable before cap"
        )
    )
    fig.add_hline(
        y=plan, line_dash="dash", line_color="red", annotation_text=f"{plan:,.0f}-minute cap"
    )
    fig.update_layout(
        title="Monthly cap simulation from annual average",
        xaxis_title="Day of month",
        yaxis_title="Billed-equivalent minutes",
    )
    return fig


def reliability_scatter(perf_repos: pd.DataFrame, period_label: str) -> go.Figure:
    """Reliability vs runtime scatter, one bubble per repository."""
    frame = perf_repos.copy()
    frame["Avg job run time (min)"] = frame["Avg job run time"] / 60_000
    frame["Avg job queue time (min)"] = frame["Avg job queue time"] / 60_000
    return px.scatter(
        frame,
        x="Failure rate",
        y="Avg job run time (min)",
        size="Job runs",
        color="Avg job queue time (min)",
        hover_name="Source repository",
        title=f"Reliability vs runtime by repository ({period_label})",
        labels={"Failure rate": "Failure rate (%)"},
    )


def queue_by_os_bar(perf_os: pd.DataFrame, period_label: str) -> go.Figure:
    """Average queue time per OS."""
    frame = perf_os.assign(**{"Avg queue time (min)": perf_os["Avg job queue time"] / 60_000})
    return px.bar(
        frame,
        x="Runtime OS",
        y="Avg queue time (min)",
        color="Runtime OS",
        title=f"Average queue time by OS ({period_label})",
    )


# ---------------------------------------------------------------------------
# Monthly figures (consume the long-form monthly_usage frame)
# ---------------------------------------------------------------------------


def monthly_billed_by_os(
    monthly: pd.DataFrame,
    multipliers: Mapping[str, float],
    plan_minutes: float | None,
    title: str = "Monthly billed-equivalent minutes by OS",
) -> go.Figure:
    """Stacked monthly billed-equivalent minutes, split by OS.

    ``monthly`` needs ``month``, ``runtime_os`` and ``adj_billed`` columns, as
    produced by :func:`~ghinsight.actions.analysis.monthly_usage` or
    :func:`~ghinsight.actions.analysis.billing_monthly_usage`.
    """
    by_month_os = monthly.groupby(["month", "runtime_os"])["adj_billed"].sum().reset_index()
    fig = go.Figure()
    for runtime_os in _OS_ORDER:
        sub = by_month_os[by_month_os["runtime_os"] == runtime_os]
        if sub.empty or sub["adj_billed"].sum() == 0:
            continue
        multiplier = multipliers.get(runtime_os, 1.0)
        fig.add_trace(
            go.Bar(
                x=sub["month"],
                y=sub["adj_billed"],
                name=f"{runtime_os} (x{multiplier:g})",
                marker_color=_OS_COLORS.get(runtime_os),
            )
        )
    if plan_minutes:
        fig.add_hline(
            y=plan_minutes,
            line_dash="dash",
            line_color="red",
            annotation_text=f"{plan_minutes:,.0f}-minute plan cap",
            annotation_position="top left",
        )
    fig.update_layout(
        barmode="stack",
        title=title,
        xaxis_title="Month",
        yaxis_title="Billed-equivalent minutes",
        legend_title="Runtime OS",
    )
    return fig


def monthly_failure_rate(monthly_agg: pd.DataFrame) -> go.Figure:
    """Line chart of monthly job failure rate (%)."""
    fig = px.line(
        monthly_agg,
        x="month",
        y="failure_rate",
        markers=True,
        title="Monthly job failure rate (%)",
        labels={"failure_rate": "Failure rate (%)", "month": "Month"},
    )
    fig.update_traces(line_color="#E45756")
    return fig


def monthly_runs(monthly_agg: pd.DataFrame) -> go.Figure:
    """Bar chart of completed monthly job runs."""
    return px.bar(
        monthly_agg,
        x="month",
        y="runs",
        title="Monthly job runs (completed, hosted runners)",
        labels={"runs": "Job runs", "month": "Month"},
        color_discrete_sequence=["#4C78A8"],
    )


def last_success_day(monthly: pd.DataFrame) -> go.Figure:
    """For each month, the last day a job ran vs the last day a job succeeded."""
    frame = monthly.copy()
    frame["day"] = pd.to_datetime(frame["started_at"]).dt.day
    last_any = (
        frame.groupby("month")["day"].max().reset_index().rename(columns={"day": "last_any_day"})
    )
    success = frame[frame["conclusion"] == "success"]
    last_success = (
        success.groupby("month")["day"]
        .max()
        .reset_index()
        .rename(columns={"day": "last_success_day"})
    )
    merged = last_any.merge(last_success, on="month", how="outer")
    merged["month_length"] = merged["month"].apply(_month_length)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=merged["month"],
            y=merged["last_any_day"],
            mode="markers",
            name="Last day any job ran",
            marker=dict(symbol="circle-open", size=10, color="#AECDE8"),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=merged["month"],
            y=merged["last_success_day"],
            mode="markers+lines",
            name="Last day a job succeeded",
            marker=dict(size=10, color="#4C78A8"),
            line=dict(color="#4C78A8"),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=merged["month"],
            y=merged["month_length"],
            mode="lines",
            name="Month length",
            line=dict(color="#BAB0AC", dash="dot"),
        )
    )
    fig.update_layout(
        title="Last day of month with a successful CI job",
        xaxis_title="Month",
        yaxis_title="Day of month",
        yaxis=dict(range=[0, 32]),
    )
    return fig


def monthly_repository_explorer(
    monthly: pd.DataFrame, months_sorted: Sequence[str], top_n: int = 10
) -> go.Figure:
    """Per-month dropdown of top repositories by billed-equivalent minutes.

    Repositories outside the overall top ``top_n`` are folded into ``other``.
    """
    top_repos = (
        monthly.groupby("repo")["adj_billed"].sum().sort_values(ascending=False).head(top_n).index
    )
    labelled = monthly.copy()
    labelled["repo_label"] = labelled["repo"].where(labelled["repo"].isin(top_repos), other="other")
    by_month_repo = (
        labelled.groupby(["month", "repo_label"])["adj_billed"]
        .sum()
        .reset_index()
        .sort_values(["month", "adj_billed"], ascending=[True, False])
    )

    fig = go.Figure()
    buttons = []
    for idx, month in enumerate(months_sorted):
        sub = by_month_repo[by_month_repo["month"] == month].sort_values(
            "adj_billed", ascending=False
        )
        fig.add_trace(
            go.Bar(
                x=sub["repo_label"],
                y=sub["adj_billed"],
                name=month,
                visible=(idx == 0),
                marker_color=px.colors.qualitative.Plotly[: len(sub)],
            )
        )
        visible = [i == idx for i in range(len(months_sorted))]
        buttons.append(
            {
                "label": month,
                "method": "update",
                "args": [
                    {"visible": visible},
                    {"title": f"Top repositories by billed-equivalent minutes - {month}"},
                ],
            }
        )
    fig.update_layout(
        title=(
            "Top repositories by billed-equivalent minutes - "
            f"{months_sorted[0] if len(months_sorted) else 'N/A'}"
        ),
        updatemenus=[
            {"buttons": buttons, "x": 1.02, "y": 1.0, "xanchor": "left", "type": "dropdown"}
        ],
        xaxis_title="Repository",
        yaxis_title="Billed-equivalent minutes",
        showlegend=False,
    )
    return fig
