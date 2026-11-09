# ghinsight

*Pronounced "gin-sight": the h is silent.*

Reusable tools for analysing GitHub organisations and user accounts:

| Part | Install | What it does |
| ---- | ------- | ------------ |
| `ghinsight activity` | core | Markdown table of a user's issue and PR activity over a date range |
| `ghinsight actions` | `[actions]` extra | GitHub Actions usage and performance: cached fetch, metric CSVs, HTML dashboard |
| `ghinsight.client`, `ghinsight.repos` | core | A small GitHub REST/GraphQL client and repository listing, for other tools to build on |

The core depends only on [httpx2](https://github.com/pydantic/httpx2) and
[typer](https://typer.tiangolo.com); the `actions` extra adds pandas, plotly and
rich. Everything is organisation-agnostic: names, paths and billing assumptions
are arguments or config.

## Install

```bash
uv tool install ghinsight              # core: activity
uv tool install 'ghinsight[actions]'   # also: actions
ghinsight --help
```

For local development from a checkout:

```bash
uv sync --group dev                    # includes the actions extra
uv run ghinsight --help
```

A conda-forge `pixi` environment is also provided:

```bash
pixi run help
pixi run check
pixi run test
```

## Authentication

Every command needs a GitHub token. For `ghinsight actions`, it must be able to
read Actions metadata for the target account. Use either:

```bash
export GH_TOKEN=...          # or GITHUB_TOKEN
```

or login with the GitHub CLI (`gh auth login`): if neither variable is set, the
token comes from `gh auth token`, wherever `gh` stores it. `GITHUB_API_URL` (set
inside GitHub Actions) selects a GitHub Enterprise Server API.

## Issue and PR activity

```bash
ghinsight activity --repo my-org/some-repo --user octocat --from 2026-05-20 --to 2026-05-27
ghinsight activity --org my-org --user octocat --from 2026-09-16 --to 2026-09-24 --output week.md
```

With `--org`, every repository of the organisation (or user) is searched and
items are labelled `repo#123`. An issue or PR is listed when, within the range,
it was:

- opened by `--user`;
- closed or merged, and either authored or closed/merged by `--user`;
- authored by `--user` earlier and updated by them (comments, commits,
  force-pushes, draft/ready changes, review requests), even if still open;
- reviewed by `--user` (someone else's PR; the type shows approved, changes
  requested or reviewed).

`--authored-only` drops the last category and PRs closed/merged by `--user`
that they did not author. Date ranges with more than 1,000 search results are
split automatically; long ranges across a large organisation are slower, since
every item closed in the range is fetched to check who closed it.

## GitHub Actions usage and performance

`ghinsight actions` fetches workflow-run and job data from the GitHub REST API,
caches the raw JSON locally, parses it into a single typed **intermediate
representation**, and derives every report — GitHub-style metric tables,
headline KPIs, and a static HTML dashboard — as a pure projection of that
representation.

### Architecture

A three-stage, functional pipeline (see the [design docs](docs/design.md) for
the full rationale):

| Stage | Module | Output |
| ----- | ------ | ------ |
| 1. Fetch | `ghinsight.actions.fetch` | cached raw JSON (verbatim API responses) |
| 2. Dataset (the IR) | `ghinsight.actions.dataset` | `ActionsDataset` — a tidy per-job / per-run table |
| 3a. Metric tables | `ghinsight.actions.metrics` | usage / performance DataFrames |
| 3b. Insights | `ghinsight.actions.analysis` | `UsageSummary`, monthly time-series |
| 3c. Report | `ghinsight.actions.report` | HTML dashboard + JSON summary |

The canonical intermediate representation is a **tidy per-job table** (one row
per job, with all derived measures and dimensions), not GitHub's aggregate CSV
exports. Aggregate exports drop per-job timestamps, so they cannot support
time-series views; the tidy table can, and every other view derives from it.
GitHub-compatible CSVs are still produced as an *export*
(`ghinsight.actions.csv_export`) for diffing against GitHub's own metrics.

### Quick start

```bash
mkdir github-actions-report && cd github-actions-report
$EDITOR analysis.toml          # see examples/analysis.toml
ghinsight actions recreate --config analysis.toml
```

`recreate` runs fetch → CSV export → dashboard end to end. The dashboard is
written to `docs/index.html` and machine-readable metadata to
`docs/summary.json`. See the [CLI tutorial](docs/tutorials/cli.md) for the
step-by-step version and the [Python tutorial](docs/tutorials/python_analysis.py)
for the programmatic analysis API.

### Other commands

```bash
# Fetch, then export CSVs, separately:
ghinsight actions fetch  --org example-org --period last-year --cache-dir cache
ghinsight actions report --org example-org --cache-dir cache --output-dir reports

# Print a table to the terminal:
ghinsight actions show usage repositories --org example-org --cache-dir cache

# Diff generated CSVs against a reference snapshot exported from GitHub:
ghinsight actions compare reference-data reports
```

### Config reference

See [examples/analysis.toml](examples/analysis.toml). `period` accepts
`last-year`, `last-6-months`, `last-3-months`, `last-month`, or
`YYYY-MM-DD..YYYY-MM-DD`.

`[analysis]`: `org`, `period`, `cache_dir`, `reports_dir`, `data_dir`,
`output_html`, `summary_json`, `billing` (optional; see below).

`[dashboard]`: `title`, `period_label`, `plan_minutes` (optional monthly cap),
`extra_insights_html`, `extra_limitations_html`.

`[os_multipliers]`: runtime OS billing multipliers for billed-equivalent
estimates (defaults: linux 1, windows 2, macos 10).

### Private-repository minutes and the billing report

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
ghinsight actions import-billing usage-report.csv --org example-org --cache-dir cache
```

This writes the same cache files as the API fetch, which then reuses them.

The billing report appears to list only usage that counted towards the
quota: in testing, a public repository's usage while public was not billed at
all, while a repository billed for months was made public only afterwards.
The dashboard therefore shows billed minutes as they are, with no visibility
filter; being as-billed, they also reflect each repository's visibility *at the
time*, which the private-only estimates cannot.

## Using the client from Python

```python
from ghinsight import GitHubClient, list_repos

with GitHubClient() as gh:                      # token from GH_TOKEN, GITHUB_TOKEN or gh
    repos = list_repos("my-org", gh)            # RepoInfo: slug, visibility, fork, archived, ...
    runs = gh.paginate("/repos/my-org/some-repo/actions/runs", response_key="workflow_runs")
    viewer = gh.graphql("{ viewer { login } }")
```

`GitHubClient` waits out primary and secondary rate limits (up to
`max_rate_limit_wait` seconds), retries server and network errors with backoff,
and raises `GitHubError` (with `.status`) otherwise. For the authenticated user,
`list_repos` includes private repositories, which `/users/{user}/repos` omits.

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
