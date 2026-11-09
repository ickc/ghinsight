"""Stage 3b — derived insights, as pure functions over the dataset.

This module contains the *interpretation* layer: billed-equivalent minutes,
plan-cap projections and monthly time-series. It produces plain DataFrames and
an immutable :class:`UsageSummary`, with no plotting or HTML concerns, so it is
the natural entry point for ad-hoc analysis (see the Python tutorial) as well as
the data source for the dashboard.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from .config import AnalysisConfig
from .dataset import ActionsDataset
from .metrics import usage_table

__all__ = [
    "UsageSummary",
    "billed_equivalent_by_os",
    "billing_monthly_usage",
    "monthly_comparison",
    "monthly_usage",
    "summarize_usage",
]

MONTHS_PER_YEAR = 12


def _multiplier_series(os_column: pd.Series, multipliers: Mapping[str, float]) -> pd.Series:
    return os_column.astype(str).str.lower().map(multipliers).fillna(1.0)


def billed_equivalent_by_os(
    dataset: ActionsDataset, multipliers: Mapping[str, float]
) -> pd.DataFrame:
    """Per-OS usage with the configured billing multiplier applied.

    Columns: ``Runtime OS``, ``Total minutes``, ``Multiplier``,
    ``Billed equivalent minutes``.
    """
    table = usage_table(dataset, "runtime-os").copy()
    if table.empty:
        return pd.DataFrame(
            columns=["Runtime OS", "Total minutes", "Multiplier", "Billed equivalent minutes"]
        )
    table["Multiplier"] = _multiplier_series(table["Runtime OS"], multipliers)
    table["Billed equivalent minutes"] = table["Total minutes"] * table["Multiplier"]
    return table


def monthly_usage(
    dataset: ActionsDataset, multipliers: Mapping[str, float]
) -> pd.DataFrame:
    """Hosted jobs augmented with ``adj_billed`` (billed-equivalent minutes).

    This is the long-form basis for every monthly chart: one row per hosted job
    with its month, OS, repo, conclusion and multiplier-adjusted minutes.
    """
    # Even an empty frame gets ``adj_billed``, so charts over an empty scope
    # (e.g. private-only when every repository is public) still work.
    frame = dataset.hosted_jobs_frame.copy()
    frame["adj_billed"] = frame["billed_minutes"] * _multiplier_series(
        frame["runtime_os"], multipliers
    )
    return frame


def billing_monthly_usage(
    billing_frame: pd.DataFrame, multipliers: Mapping[str, float]
) -> pd.DataFrame:
    """Billing-report Actions minutes augmented with ``adj_billed``.

    ``billing_frame`` is :meth:`BillingUsage.actions_minutes_frame
    <ghinsight.billing.BillingUsage.actions_minutes_frame>`. The result
    has the same ``month``/``runtime_os``/``adj_billed`` columns as
    :func:`monthly_usage`, so the same monthly charts apply to it.
    """
    frame = billing_frame.copy()
    frame["adj_billed"] = frame["minutes"].astype(float) * _multiplier_series(
        frame["runtime_os"], multipliers
    )
    return frame


def _monthly_all_and_private(
    frame: pd.DataFrame, column: str
) -> tuple[pd.Series, pd.Series]:
    """Monthly sums of ``column`` over all repos and over non-public repos."""
    if frame.empty:
        empty = pd.Series(dtype=float)
        return empty, empty
    all_repos = frame.groupby("month")[column].sum()
    private = frame[frame["visibility"] != "public"].groupby("month")[column].sum()
    return all_repos, private.reindex(all_repos.index, fill_value=0.0)


def monthly_comparison(
    estimated: pd.DataFrame, billed: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Monthly billed-equivalent minutes: estimates by scope, and as billed.

    ``estimated`` is :func:`monthly_usage`; its private-only column drops public
    repos and keeps private, internal and unknown ones. ``billed`` (optional)
    is :func:`billing_monthly_usage`, taken as is: the billing report already
    omits free public-repository usage. Billing columns are present only when
    ``billed`` is given; months missing from one source are ``NaN``.
    """
    est_all, est_private = _monthly_all_and_private(estimated, "adj_billed")
    columns = {"Estimated, all repos": est_all, "Estimated, private only": est_private}
    if billed is not None:
        columns |= {
            "Billed (GitHub report)": billed.groupby("month")["adj_billed"].sum(),
            "Actions net charge (USD)": billed.groupby("month")["net_amount"].sum(),
        }
    table = pd.DataFrame(columns).sort_index()
    table.index.name = "Month"
    return table.reset_index()


@dataclass(frozen=True, slots=True)
class UsageSummary:
    """Headline usage figures for a period. All minutes are billed-equivalent
    unless named ``raw``."""

    org: str
    period: str
    period_label: str
    raw_total_minutes: float
    billed_equivalent_minutes: float
    estimated_monthly_billed: float
    estimated_daily_billed: float
    plan_minutes: float | None
    plan_pct: float | None
    cap_reached: bool | None
    estimated_unserved_billed: float

    @property
    def cap_status_text(self) -> str:
        """Human-readable description of the plan-cap situation (HTML-safe)."""
        if self.plan_minutes is None:
            return ""
        if self.cap_reached:
            day_cap_hit = (
                self.plan_minutes / self.estimated_daily_billed
                if self.estimated_daily_billed
                else 0.0
            )
            return (
                f"cap hit around day <b>{day_cap_hit:.1f}</b> of an average month, "
                f"leaving <b>{self.estimated_unserved_billed:,.0f}</b> "
                "billed-equivalent minutes unmet"
            )
        return (
            f"cap <b>not reached</b> in an average month "
            f"(average usage is {self.plan_pct:.0f}% of the "
            f"{self.plan_minutes:,.0f}-minute cap)"
        )


def summarize_usage(dataset: ActionsDataset, config: AnalysisConfig) -> UsageSummary:
    """Compute headline KPIs from the dataset and config.

    The selected period is treated as an annual window, so monthly demand is the
    billed-equivalent total divided by twelve (this matches the original report).
    """
    by_os = billed_equivalent_by_os(dataset, config.os_multipliers)
    raw_total = float(by_os["Total minutes"].sum()) if not by_os.empty else 0.0
    billed_total = (
        float(by_os["Billed equivalent minutes"].sum()) if not by_os.empty else 0.0
    )

    est_monthly = billed_total / MONTHS_PER_YEAR
    est_daily = est_monthly / config.days_per_month if config.days_per_month else 0.0

    plan = config.plan_minutes
    plan_pct: float | None = None
    cap_reached: bool | None = None
    unserved = 0.0
    if plan:
        plan_pct = est_monthly / plan * 100
        cap_reached = est_monthly > plan
        if cap_reached:
            unserved = est_monthly - plan

    return UsageSummary(
        org=config.org,
        period=config.period,
        period_label=config.period_label,
        raw_total_minutes=raw_total,
        billed_equivalent_minutes=billed_total,
        estimated_monthly_billed=est_monthly,
        estimated_daily_billed=est_daily,
        plan_minutes=plan,
        plan_pct=plan_pct,
        cap_reached=cap_reached,
        estimated_unserved_billed=unserved,
    )
