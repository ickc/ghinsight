# Tutorial: GitHub Actions from the command line

This walkthrough takes you from nothing to a published HTML dashboard using only
the `ghinsight actions` commands. They mirror the three pipeline stages: **fetch →
report → dashboard** (with `recreate` running all three at once).

## 1. Install and authenticate

```bash
uv tool install 'ghinsight[actions]'
ghinsight --help
```

The CLI needs a GitHub token that can read Actions metadata for the target
account. Provide one of:

```bash
export GH_TOKEN=...            # or GITHUB_TOKEN
# or: gh auth login            # the token then comes from `gh auth token`
```

## 2. Create a project and config

```bash
mkdir github-actions-report && cd github-actions-report
```

Create `analysis.toml` (works for an organization *or* a user account — set
`org` to either):

```toml
[analysis]
org = "example-org"
period = "last-year"
cache_dir = "cache"
reports_dir = "reports"
data_dir = "reports"
output_html = "docs/index.html"
summary_json = "docs/summary.json"

[dashboard]
title = "GitHub Actions Usage Analysis"
period_label = "last year"
# plan_minutes = 3000          # uncomment if you have a monthly minutes cap

[os_multipliers]
linux = 1
windows = 2
macos = 10
```

`period` accepts `last-year`, `last-6-months`, `last-3-months`, `last-month`, or
an explicit `YYYY-MM-DD..YYYY-MM-DD` range.

## 3. Fetch raw data (stage 1)

```bash
ghinsight actions fetch --org example-org --period last-year --cache-dir cache
```

This caches verbatim API responses under `cache/example-org/<repo>/`, plus each
repository's visibility in `repo.json`. Re-running is cheap — cached runs and
jobs are reused unless you pass `--force`.

Add `--billing` to also cache GitHub's billing usage report under
`cache/_billing/example-org/`. The API appears to need an organisation owner (or the
`user` token scope for a user account); without that access `fetch` prints a
warning, skips it, and the dashboard falls back to estimated minutes.

Billing managers can download the usage report CSV from the organisation's
billing usage page instead (the summarized report covers up to a year) and
import it into the same cache:

```bash
ghinsight actions import-billing usage-report.csv --org example-org --cache-dir cache
```

## 4. Export metric CSVs (stage 2/3)

```bash
ghinsight actions report --org example-org --cache-dir cache --output-dir reports
```

Produces GitHub-compatible CSVs::

```text
reports/actions-usage-metrics/{workflows,jobs,repositories,runtime-os,runner-type}.csv
reports/actions-performance-metrics/{workflows,jobs,repositories,runtime-os,runner-type}.csv
```

Peek at a table without writing files:

```bash
ghinsight actions show usage repositories --org example-org --cache-dir cache --top 10
```

## 5. Build the dashboard (stage 3)

```bash
ghinsight actions dashboard --config analysis.toml
```

Writes the static dashboard to `docs/index.html` and machine-readable metadata
to `docs/summary.json`. When repository visibility is cached, the minute charts
have an **All repositories / Private repositories only** toggle, since only
private repositories use the plan's included minutes.

## 6. Or do it all at once

```bash
ghinsight actions recreate --config analysis.toml
```

`recreate` runs fetch → report → dashboard from a single config. Useful flags:

- `--skip-fetch` — reuse the cache and only regenerate outputs.
- `--skip-dashboard` — produce CSVs only.
- `--force` — re-fetch even when cached.
- `--billing/--no-billing` — fetch the billing usage report (default: the
  config's `billing` setting).

## 7. (Optional) verify against a GitHub export

If you have a reference snapshot exported from GitHub's UI, diff it against your
generated CSVs:

```bash
ghinsight actions compare reference-data reports
```

The comparison distinguishes genuine structural mismatches from expected
"snapshot drift" (newer runs, or repos no longer visible to your token).
