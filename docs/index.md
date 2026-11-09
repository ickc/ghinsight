# ghinsight

Reusable, auditable tools for analyzing **GitHub Actions** usage and performance
across any GitHub organization or user account.

The package fetches workflow-run and job data from the GitHub REST API, caches
the raw JSON locally, parses it into a single typed **intermediate
representation**, and derives every report — metric tables, headline KPIs, and a
static HTML dashboard — as a pure projection of that representation.

```{toctree}
:maxdepth: 2
:caption: Contents

design
tutorials/cli
tutorials/python_analysis
autoapi/index
```

## Quick start

```bash
uv tool install ghinsight
export GITHUB_TOKEN=...            # a token that can read Actions metadata
ghinsight recreate --config analysis.toml
```

See the {doc}`tutorials/cli` for the command-line walkthrough and the
{doc}`tutorials/python_analysis` notebook for the programmatic analysis API.

## How it fits together

| Stage | Module | Output |
| ----- | ------ | ------ |
| 1. Fetch | {py:mod}`ghinsight.fetch` | cached raw JSON |
| 2. Dataset (the IR) | {py:mod}`ghinsight.dataset` | {py:class}`~ghinsight.dataset.ActionsDataset` |
| 3a. Metric tables | {py:mod}`ghinsight.metrics` | usage / performance DataFrames |
| 3b. Insights | {py:mod}`ghinsight.analysis` | {py:class}`~ghinsight.analysis.UsageSummary`, time-series |
| 3c. Report | {py:mod}`ghinsight.report` | HTML dashboard + JSON summary |

The design and the reasoning behind the intermediate representation are
explained in {doc}`design`.
