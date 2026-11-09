"""The ``ghinsight`` command.

``ghinsight activity`` needs only the core dependencies. ``ghinsight actions``
needs the ``actions`` extra; without it, the command explains how to install it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import typer

from . import __version__

app = typer.Typer(
    help='GitHub analysis tools. Pronounced "gin-sight".',
    no_args_is_help=True,
    add_completion=False,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"ghinsight {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """GitHub analysis tools."""


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{value!r} is not a valid date; use YYYY-MM-DD") from exc


@app.command()
def activity(
    user: str = typer.Option(..., help="GitHub login whose activity is listed."),
    repo: Optional[str] = typer.Option(None, help="Repository in OWNER/NAME form."),
    org: Optional[str] = typer.Option(
        None, help="Organisation or user whose repositories are all searched."
    ),
    date_from: str = typer.Option(..., "--from", help="Start date, inclusive (YYYY-MM-DD)."),
    date_to: str = typer.Option(..., "--to", help="End date, inclusive (YYYY-MM-DD)."),
    output: Optional[Path] = typer.Option(None, help="Write the table here instead of stdout."),
    authored_only: bool = typer.Option(
        False,
        "--authored-only",
        help="Only items authored by --user; drop reviews and items they only closed or merged.",
    ),
) -> None:
    """Markdown table of a user's issue and pull request activity in a date range.

    An item is listed when, within the range, it was opened by --user; closed or
    merged, and authored or closed/merged by --user; authored by --user earlier
    and updated by them; or (someone else's PR) reviewed by --user.
    """
    from .activity import build_rows, format_table
    from .client import GitHubClient

    if (repo is None) == (org is None):
        raise typer.BadParameter("give exactly one of --repo or --org")
    start, end = _parse_date(date_from), _parse_date(date_to)
    if start > end:
        raise typer.BadParameter("--from must be on or before --to")

    with GitHubClient() as client:
        rows = build_rows(
            client,
            scope=f"repo:{repo}" if repo else f"org:{org}",
            user=user,
            date_from=start,
            date_to=end,
            authored_only=authored_only,
        )
    table = format_table(rows, show_repo=repo is None)
    if output is None:
        typer.echo(table, nl=False)
    else:
        output.write_text(table, encoding="utf-8")


# Top-level modules installed by the 'actions' extra.
_ACTIONS_EXTRA_MODULES = frozenset({"pandas", "plotly"})


def _register_missing_actions_extra(exc: ModuleNotFoundError) -> None:
    """Register ``actions`` as a command that explains how to install the extra."""

    @app.command(
        "actions",
        help="GitHub Actions usage and performance (needs the 'actions' extra).",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        add_help_option=False,
    )
    def actions_unavailable(ctx: typer.Context) -> None:
        typer.echo(
            f"'ghinsight actions' needs the 'actions' extra ({exc.name} is not installed). "
            "Install it with: pip install 'ghinsight[actions]'",
            err=True,
        )
        raise typer.Exit(1)


try:
    from .actions.cli import app as _actions_app
except ModuleNotFoundError as _exc:
    if (_exc.name or "").partition(".")[0] not in _ACTIONS_EXTRA_MODULES:
        raise
    _register_missing_actions_extra(_exc)
else:
    app.add_typer(_actions_app, name="actions")
