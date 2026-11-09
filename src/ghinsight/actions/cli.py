"""The ``ghinsight actions`` commands: GitHub Actions usage and performance.

The CLI is intentionally thin: each command parses arguments, builds an
immutable config or dataset, and delegates to the library. The three pipeline
stages map onto commands directly:

    fetch      stage 1 — cache raw runs/jobs from the GitHub API
    import-billing  stage 1 — cache a billing usage report CSV from the web UI
    report     stage 2/3 — export GitHub-compatible metric CSVs
    dashboard  stage 3 — build the static HTML report + JSON summary
    recreate   run the whole pipeline from one config file
    show       print a metric table to the terminal
    compare    diff generated CSVs against a reference snapshot
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .compare import main as compare_reports
from .config import AnalysisConfig, DateRange, load_config, parse_period
from .csv_export import Metric, write_reports
from .billing import import_usage_csv
from .dataset import ActionsDataset
from .fetch import fetch_billing_usage, fetch_org, fetch_repo
from .metrics import PERFORMANCE_TABLES, USAGE_TABLES
from .report import build_dashboard

app = typer.Typer(help="GitHub Actions usage and performance analysis.", no_args_is_help=True)
console = Console()


def _resolve(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else (root / path).resolve()


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


@app.command()
def fetch(
    org: str = typer.Option(..., help="GitHub organization (or user) name."),
    repo: Optional[str] = typer.Option(None, help="Single repo to fetch (default: all)."),
    period: str = typer.Option("last-year", help="last-year, last-6-months, last-3-months, last-month, or YYYY-MM-DD..YYYY-MM-DD."),
    cache_dir: Path = typer.Option(Path("cache"), help="Directory to store cached JSON."),
    force: bool = typer.Option(False, "--force", help="Re-fetch even if cached."),
    billing: bool = typer.Option(
        False,
        "--billing",
        help="Also fetch the billing usage report (needs an org owner or billing "
        "manager; skipped with a warning otherwise).",
    ),
) -> None:
    """Fetch raw workflow-run and job data from GitHub and cache it (stage 1)."""
    try:
        date_range = parse_period(period)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[bold]Fetching[/bold] {org} | period: {date_range}")

    if repo:
        fetch_repo(org, repo, cache_dir, date_range=date_range, force=force)
        console.print(f"[green]Done[/green]: {org}/{repo}")
    else:
        fetched = fetch_org(org, cache_dir, date_range=date_range, force=force)
        console.print(f"[green]Done[/green]: fetched {len(fetched)} repos")

    if billing:
        _fetch_billing(org, cache_dir, date_range, force=force)


def _fetch_billing(org: str, cache_dir: Path, date_range: DateRange, *, force: bool) -> None:
    months = fetch_billing_usage(org, cache_dir, date_range, force=force)
    if months is None:
        console.print(
            "[yellow]Billing usage report unavailable[/yellow] (the API appears to "
            "need an org owner); billing managers can use 'ghinsight actions import-billing' instead."
        )
    else:
        console.print(f"[green]Done[/green]: billing usage for {len(months)} months")


@app.command("import-billing")
def import_billing(
    csv_file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Usage report CSV."),
    org: str = typer.Option(..., help="GitHub organization (or user) name."),
    cache_dir: Path = typer.Option(Path("cache"), help="Directory to store cached JSON."),
) -> None:
    """Cache a billing usage report CSV downloaded from GitHub's billing pages.

    Use this when the billing API is unavailable, as it is to billing managers
    who are not organisation owners: they can still download the summarized
    usage report, which covers up to a year. The dashboard then treats it like fetched billing data.
    """
    months = import_usage_csv(csv_file, org, cache_dir)
    if not months:
        console.print(f"[yellow]No rows for {org} in {csv_file}.[/yellow]")
        raise typer.Exit(1)
    console.print(
        f"[green]Imported[/green] billing usage for {len(months)} months "
        f"({months[0]} to {months[-1]})"
    )


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@app.command()
def report(
    org: str = typer.Option(..., help="GitHub organization (or user) name."),
    cache_dir: Path = typer.Option(Path("cache"), help="Directory with cached JSON."),
    output_dir: Path = typer.Option(Path("reports"), help="Directory to write CSV reports."),
    metric: str = typer.Option("both", help="Which metrics: usage, performance, or both."),
) -> None:
    """Export usage and/or performance metric CSVs from the cache (stage 2/3)."""
    dataset = ActionsDataset.from_cache(org, cache_dir)
    if dataset.is_empty:
        console.print("[yellow]No cached data — run 'ghinsight actions fetch' first.[/yellow]")
        raise typer.Exit(1)
    try:
        paths = write_reports(dataset, output_dir, _metric(metric))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for path in paths:
        console.print(f"[green]Wrote[/green] {path}")


def _metric(value: str) -> Metric:
    if value not in ("usage", "performance", "both"):
        raise typer.BadParameter("metric must be one of: usage, performance, both")
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


@app.command()
def dashboard(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="TOML analysis config."),
    org: Optional[str] = typer.Option(None, help="GitHub organization (or user) name."),
    cache_dir: Optional[Path] = typer.Option(None, help="Directory with cached JSON."),
    output: Optional[Path] = typer.Option(None, help="HTML dashboard output path."),
    summary: Optional[Path] = typer.Option(None, help="JSON summary output path."),
    title: Optional[str] = typer.Option(None, help="Dashboard title."),
    period_label: Optional[str] = typer.Option(None, help="Human-readable period label."),
    plan_minutes: Optional[float] = typer.Option(None, help="Monthly included-minutes cap."),
) -> None:
    """Build the static HTML dashboard and JSON summary (stage 3)."""
    if config is not None:
        cfg = load_config(config)
        cfg = cfg.with_overrides(
            cache_dir=_resolve(cfg.root, cache_dir) if cache_dir else None,
            output_html=_resolve(cfg.root, output) if output else None,
            summary_json=_resolve(cfg.root, summary) if summary else None,
            title=title,
            period_label=period_label,
            plan_minutes=plan_minutes,
        )
    else:
        if org is None:
            raise typer.BadParameter("--org is required when --config is not supplied")
        root = Path.cwd().resolve()
        cfg = AnalysisConfig(
            org=org,
            root=root,
            cache_dir=_resolve(root, cache_dir or Path("cache")),
            reports_dir=_resolve(root, Path("reports")),
            data_dir=_resolve(root, Path("reports")),
            output_html=_resolve(root, output or Path("docs/index.html")),
            summary_json=_resolve(root, summary or Path("docs/summary.json")),
            title=title or "GitHub Actions Usage Analysis",
            period_label=period_label or "selected period",
            plan_minutes=plan_minutes,
        )

    metadata = build_dashboard(cfg)
    console.print(f"[green]Wrote[/green] {cfg.output_html} and {cfg.summary_json}")
    console.print(
        "[green]Summary[/green]: "
        f"{metadata['billed_equivalent_minutes_period']:,.0f} billed-equivalent minutes"
    )


# ---------------------------------------------------------------------------
# recreate
# ---------------------------------------------------------------------------


@app.command()
def recreate(
    config: Path = typer.Option(..., "--config", "-c", help="TOML analysis config."),
    force: bool = typer.Option(False, "--force", help="Re-fetch even if cached."),
    skip_fetch: bool = typer.Option(False, "--skip-fetch", help="Reuse cache; regenerate outputs."),
    skip_dashboard: bool = typer.Option(False, "--skip-dashboard", help="Do not build the dashboard."),
    metric: str = typer.Option("both", help="Which CSV metrics: usage, performance, or both."),
    billing: Optional[bool] = typer.Option(
        None,
        "--billing/--no-billing",
        help="Fetch the billing usage report (default: the config's analysis.billing).",
    ),
) -> None:
    """Run the configured end-to-end pipeline: fetch, export, dashboard."""
    cfg = load_config(config)

    if not skip_fetch:
        console.print(f"[bold]Fetching[/bold] {cfg.org} | period: {cfg.date_range}")
        fetched = fetch_org(cfg.org, cfg.cache_dir, date_range=cfg.date_range, force=force)
        console.print(f"[green]Done[/green]: fetched {len(fetched)} repos")
        if cfg.fetch_billing if billing is None else billing:
            _fetch_billing(cfg.org, cfg.cache_dir, cfg.date_range, force=force)

    dataset = ActionsDataset.from_cache(cfg.org, cfg.cache_dir)
    if dataset.is_empty:
        console.print("[yellow]No cached data found — nothing to report.[/yellow]")
        raise typer.Exit(1)

    for path in write_reports(dataset, cfg.reports_dir, _metric(metric)):
        console.print(f"[green]Wrote[/green] {path}")

    if not skip_dashboard:
        build_dashboard(cfg)
        console.print(f"[green]Wrote[/green] {cfg.output_html} and {cfg.summary_json}")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


@app.command()
def compare(
    reference_dir: Path = typer.Argument(..., help="Reference metrics CSV directory."),
    generated_dir: Path = typer.Argument(..., help="Generated metrics CSV directory."),
) -> None:
    """Compare generated reports against a reference metrics snapshot."""
    raise typer.Exit(compare_reports(reference_dir, generated_dir))


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@app.command()
def show(
    metric: str = typer.Argument(..., help="Metric type: usage or performance."),
    table: str = typer.Argument(..., help="Table: workflows, jobs, repositories, runtime-os, runner-type."),
    org: str = typer.Option(..., help="GitHub organization (or user) name."),
    cache_dir: Path = typer.Option(Path("cache"), help="Directory with cached JSON."),
    top: int = typer.Option(20, help="Show top N rows."),
) -> None:
    """Pretty-print a metric table from cached data."""
    catalog = {"usage": USAGE_TABLES, "performance": PERFORMANCE_TABLES}
    if metric not in catalog:
        raise typer.BadParameter(f"metric must be one of: {list(catalog)}")
    table_fns = catalog[metric]
    if table not in table_fns:
        raise typer.BadParameter(f"table must be one of: {list(table_fns)}")

    dataset = ActionsDataset.from_cache(org, cache_dir)
    frame = table_fns[table](dataset).head(top)
    if frame.empty:
        console.print("[yellow]No data — have you run 'ghinsight actions fetch' yet?[/yellow]")
        return

    rich_table = Table(title=f"{metric} / {table} — {org} (top {top})")
    for col in frame.columns:
        rich_table.add_column(str(col), no_wrap=True)
    for _, row in frame.iterrows():
        rich_table.add_row(*[str(v) for v in row])
    console.print(rich_table)

