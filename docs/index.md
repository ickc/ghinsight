# ghinsight

*Pronounced "gin-sight": the h is silent.*

Reusable tools for analysing GitHub organisations and user accounts:

- `ghinsight activity` (core install): a Markdown table of a user's issue and
  pull request activity over a date range; see {doc}`tutorials/activity`.
- `ghinsight actions` (`ghinsight[actions]`): GitHub Actions usage and
  performance. It fetches workflow-run and job data, caches the raw JSON,
  parses it into a single typed **intermediate representation**, and derives
  every report (metric tables, headline KPIs, a static HTML dashboard) as a pure
  projection of that representation.
- {py:mod}`ghinsight.client` and {py:mod}`ghinsight.repos` (core): a small
  GitHub REST/GraphQL client and repository listing for other tools to build on.

The core depends only on httpx2 and typer; the `actions` extra adds pandas,
plotly and rich.

```{toctree}
:maxdepth: 2
:caption: Contents

design
tutorials/activity
tutorials/cli
tutorials/python_analysis
autoapi/index
```

## Quick start: GitHub Actions

```bash
uv tool install 'ghinsight[actions]'
export GH_TOKEN=...                # or gh auth login
ghinsight actions recreate --config analysis.toml
```

See the {doc}`tutorials/cli` for the command-line walkthrough and the
{doc}`tutorials/python_analysis` notebook for the programmatic analysis API.

## How the Actions pipeline fits together

| Stage | Module | Output |
| ----- | ------ | ------ |
| 1. Fetch | {py:mod}`ghinsight.actions.fetch` | cached raw JSON |
| 2. Dataset (the IR) | {py:mod}`ghinsight.actions.dataset` | {py:class}`~ghinsight.actions.dataset.ActionsDataset` |
| 3a. Metric tables | {py:mod}`ghinsight.actions.metrics` | usage / performance DataFrames |
| 3b. Insights | {py:mod}`ghinsight.actions.analysis` | {py:class}`~ghinsight.actions.analysis.UsageSummary`, time-series |
| 3c. Report | {py:mod}`ghinsight.actions.report` | HTML dashboard + JSON summary |

The design and the reasoning behind the intermediate representation are
explained in {doc}`design`.
