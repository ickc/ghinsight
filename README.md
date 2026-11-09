# ghinsight

*Pronounced "gin-sight": the h is silent.*

Reusable, auditable tools for analyzing GitHub Actions usage and performance
across any GitHub organization **or user account**.

The package fetches workflow-run and job data from the GitHub REST API, caches
the raw JSON locally, parses it into a single typed **intermediate
representation**, and derives every report — GitHub-style metric tables,
headline KPIs, and a static HTML dashboard — as a pure projection of that
representation. It is organization-agnostic: org/user names, output paths, and
billing assumptions live in your config file.

## Architecture

A three-stage, functional pipeline (see the [design docs](docs/design.md) for
the full rationale):

| Stage | Module | Output |
| ----- | ------ | ------ |
| 1. Fetch | `ghinsight.fetch` | cached raw JSON (verbatim API responses) |
| 2. Dataset (the IR) | `ghinsight.dataset` | `ActionsDataset` — a tidy per-job / per-run table |
| 3a. Metric tables | `ghinsight.metrics` | usage / performance DataFrames |
| 3b. Insights | `ghinsight.analysis` | `UsageSummary`, monthly time-series |
| 3c. Report | `ghinsight.report` | HTML dashboard + JSON summary |

The canonical intermediate representation is a **tidy per-job table** (one row
per job, with all derived measures and dimensions), not GitHub's aggregate CSV
exports. Aggregate exports drop per-job timestamps, so they cannot support
time-series views; the tidy table can, and every other view derives from it.
GitHub-compatible CSVs are still produced as an *export*
(`ghinsight.csv_export`) for diffing against GitHub's own metrics.

## Install

```bash
uv tool install ghinsight
ghinsight --help
```

For local development from a checkout:

```bash
uv sync --group dev
uv run ghinsight --help
```

A conda-forge `pixi` environment is also provided:

```bash
pixi run help
pixi run check
pixi run test
```

## Authentication

The CLI needs a GitHub token that can read Actions metadata for the target
account. Use either:

```bash
export GH_TOKEN=...          # or GITHUB_TOKEN
```

or login with the GitHub CLI (`gh auth login`): if neither variable is set, the
token comes from `gh auth token`, wherever `gh` stores it. `GITHUB_API_URL` (set
inside GitHub Actions) selects a GitHub Enterprise Server API.

## Quick start

```bash
mkdir github-actions-report && cd github-actions-report
$EDITOR analysis.toml          # see examples/analysis.toml
ghinsight recreate --config analysis.toml
```

`recreate` runs fetch → CSV export → dashboard end to end. The dashboard is
written to `docs/index.html` and machine-readable metadata to
`docs/summary.json`. See the [CLI tutorial](docs/tutorials/cli.md) for the
step-by-step version and the [Python tutorial](docs/tutorials/python_analysis.py)
for the programmatic analysis API.

## Other commands

```bash
# Fetch, then export CSVs, separately:
ghinsight fetch  --org example-org --period last-year --cache-dir cache
ghinsight report --org example-org --cache-dir cache --output-dir reports

# Print a table to the terminal:
ghinsight show usage repositories --org example-org --cache-dir cache

# Diff generated CSVs against a reference snapshot exported from GitHub:
ghinsight compare reference-data reports
```

## Config reference

See [examples/analysis.toml](examples/analysis.toml). `period` accepts
`last-year`, `last-6-months`, `last-3-months`, `last-month`, or
`YYYY-MM-DD..YYYY-MM-DD`.

`[analysis]`: `org`, `period`, `cache_dir`, `reports_dir`, `data_dir`,
`output_html`, `summary_json`, `billing` (optional; see below).

`[dashboard]`: `title`, `period_label`, `plan_minutes` (optional monthly cap),
`extra_insights_html`, `extra_limitations_html`.

`[os_multipliers]`: runtime OS billing multipliers for billed-equivalent
estimates (defaults: linux 1, windows 2, macos 10).

## Private-repository minutes and the billing report

A plan's included minutes only apply to private (and internal) repositories:
public repositories on standard GitHub-hosted runners are free. `fetch` records
each repository's visibility (`repo.json` in the cache), and the dashboard's
minute charts get an **All repositories / Private repositories only** toggle,
with private-only KPIs and a `private_only` block in `summary.json`.
Visibility is as of the fetch, so a repository whose visibility changed during
the period is classified by its current visibility throughout. Caches written
before visibility was recorded render without the toggle; re-run `fetch` to add
it.

Optionally, `fetch --billing` (or `billing = true` in the config) also caches
GitHub's [billing usage report](https://docs.github.com/en/rest/billing/usage),
which gives the minutes GitHub actually billed and the net charge. The dashboard
then adds a billed-usage section and a `billing_report` block in `summary.json`.
The API appears to admit **organisation owners only**: billing managers get a
404 even with a classic token carrying `admin:org` (for a user account, the
token needs the `user` scope). Without access the fetch logs a warning and
caches nothing, and the dashboard notes that all figures are estimates.

Billing managers can instead download the usage report CSV from the
organisation's billing usage page (the *summarized* report covers up to a year
and breaks usage down by repository) and import it:

```bash
ghinsight import-billing usage-report.csv --org example-org --cache-dir cache
```

This writes the same cache files as the API fetch, which then reuses them.

The billing report appears to list only usage that counted towards the
quota: in testing, a public repository's usage while public was not billed at
all, while a repository billed for months was made public only afterwards.
The dashboard therefore shows billed minutes as they are, with no visibility
filter; being as-billed, they also reflect each repository's visibility *at the
time*, which the private-only estimates cannot.

## Development

```bash
uv sync --group dev
uv run pytest                  # offline test suite (synthetic fixtures)
uv build                       # build the wheel/sdist
```

The test suite runs fully offline against a synthetic cache (`tests/synthetic.py`),
so no token or network is required.

## Documentation

```bash
uv sync --group docs
uv run --group docs sphinx-build -b html docs docs/_build/html
```

The site uses Sphinx with AutoAPI (API reference from source), a design page,
and CLI + Python tutorials (the Python tutorial is an executable jupytext
notebook rendered via MyST-NB).
