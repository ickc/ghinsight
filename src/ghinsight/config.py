"""Configuration types and loaders.

Everything the pipeline needs to know — *which* org to crawl, *where* to cache,
*how* to bill each OS — lives in a single immutable :class:`AnalysisConfig`.
It is a frozen dataclass, so it can be passed freely without any function being
able to mutate it, and an override is an explicit :func:`dataclasses.replace`.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .domain import utcnow

__all__ = [
    "DateRange",
    "AnalysisConfig",
    "parse_period",
    "load_config",
    "DEFAULT_OS_MULTIPLIERS",
    "DEFAULT_DAYS_PER_MONTH",
]

DEFAULT_OS_MULTIPLIERS: Mapping[str, float] = {"linux": 1.0, "windows": 2.0, "macos": 10.0}
DEFAULT_DAYS_PER_MONTH = 30.44

# Named relative periods expressed as a number of days back from "now".
_NAMED_PERIODS: Mapping[str, int] = {
    "last-year": 365,
    "last-6-months": 183,
    "last-3-months": 91,
    "last-month": 30,
}


@dataclass(frozen=True, slots=True)
class DateRange:
    """A closed time interval ``[since, until]`` used to filter fetches."""

    since: datetime
    until: datetime

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.since.date()}..{self.until.date()}"


def parse_period(period: str, *, now: datetime | None = None) -> DateRange:
    """Resolve a period string into a concrete :class:`DateRange`.

    Accepts a named period (``last-year``, ``last-6-months``, ``last-3-months``,
    ``last-month``) or an explicit ``YYYY-MM-DD..YYYY-MM-DD`` range.

    Raises:
        ValueError: if the period is neither a known name nor a valid range.
    """
    until = now or utcnow()
    days = _NAMED_PERIODS.get(period)
    if days is not None:
        return DateRange(since=until - timedelta(days=days), until=until)

    parts = period.split("..")
    try:
        since = datetime.fromisoformat(parts[0])
        if len(parts) > 1 and parts[1]:
            until = datetime.fromisoformat(parts[1])
    except ValueError as exc:
        raise ValueError(
            f"Unknown period {period!r}. Use one of {sorted(_NAMED_PERIODS)} "
            "or 'YYYY-MM-DD..YYYY-MM-DD'."
        ) from exc
    return DateRange(since=since, until=until)


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Immutable configuration for a full analysis run.

    Path fields are always absolute once constructed via :func:`load_config`,
    so downstream code never has to reason about the current working directory.
    """

    org: str
    root: Path
    cache_dir: Path
    reports_dir: Path
    data_dir: Path
    output_html: Path
    summary_json: Path
    period: str = "last-year"
    period_label: str = "selected period"
    title: str = "GitHub Actions Usage Analysis"
    plan_minutes: float | None = None
    fetch_billing: bool = False
    days_per_month: float = DEFAULT_DAYS_PER_MONTH
    os_multipliers: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_OS_MULTIPLIERS)
    )
    extra_insights_html: tuple[str, ...] = ()
    extra_limitations_html: tuple[str, ...] = ()

    def with_overrides(self, **changes: Any) -> "AnalysisConfig":
        """Return a copy with the given fields replaced."""
        return replace(self, **{k: v for k, v in changes.items() if v is not None})

    @property
    def date_range(self) -> DateRange:
        """The concrete :class:`DateRange` implied by :attr:`period`."""
        return parse_period(self.period)


def _resolve_path(root: Path, value: object, default: str) -> Path:
    raw = Path(str(value) if value is not None else default).expanduser()
    return raw if raw.is_absolute() else (root / raw).resolve()


def _table(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"[{name}] must be a table")
    return value


def load_config(path: Path) -> AnalysisConfig:
    """Load an :class:`AnalysisConfig` from a TOML file.

    Relative paths in the file are resolved against the directory containing
    the config (or ``[analysis].root`` if set). The expected shape is::

        [analysis]
        org = "example-org"
        cache_dir = "cache"
        reports_dir = "reports"
        output_html = "docs/index.html"
        # Optional: also fetch the billing usage report (the API appears to
        # need an org owner); skipped with a warning if access is missing.
        billing = true

        [dashboard]
        title = "..."
        plan_minutes = 3000

        [os_multipliers]
        linux = 1
        windows = 2
        macos = 10
    """
    path = path.resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    analysis = _table(data, "analysis")
    dashboard = _table(data, "dashboard")
    root = _resolve_path(path.parent, analysis.get("root"), ".")

    org = analysis.get("org")
    if not org:
        raise ValueError("Config must set [analysis].org")

    reports_dir = _resolve_path(root, analysis.get("reports_dir"), "reports")
    data_dir = _resolve_path(root, analysis.get("data_dir"), str(reports_dir))

    os_multipliers = dict(DEFAULT_OS_MULTIPLIERS)
    for key, value in _table(data, "os_multipliers").items():
        os_multipliers[str(key).lower()] = float(value)

    period = str(analysis.get("period", "last-year"))
    plan = dashboard.get("plan_minutes")

    return AnalysisConfig(
        org=str(org),
        root=root,
        cache_dir=_resolve_path(root, analysis.get("cache_dir"), "cache"),
        reports_dir=reports_dir,
        data_dir=data_dir,
        output_html=_resolve_path(root, analysis.get("output_html"), "docs/index.html"),
        summary_json=_resolve_path(root, analysis.get("summary_json"), "docs/summary.json"),
        period=period,
        period_label=str(dashboard.get("period_label", period)),
        title=str(dashboard.get("title", "GitHub Actions Usage Analysis")),
        plan_minutes=float(plan) if plan is not None else None,
        fetch_billing=bool(analysis.get("billing", False)),
        days_per_month=float(dashboard.get("days_per_month", DEFAULT_DAYS_PER_MONTH)),
        os_multipliers=os_multipliers,
        extra_insights_html=tuple(str(x) for x in dashboard.get("extra_insights_html", [])),
        extra_limitations_html=tuple(str(x) for x in dashboard.get("extra_limitations_html", [])),
    )
