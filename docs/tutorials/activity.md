# Tutorial: issue and pull request activity

`ghinsight activity` lists what a user did on issues and pull requests over a
date range, as a Markdown table ready to paste into a report or timesheet. It
needs only the core install:

```bash
uv tool install ghinsight
export GH_TOKEN=...            # or: gh auth login
```

## One repository

```bash
ghinsight activity --repo my-org/some-repo --user octocat --from 2026-05-20 --to 2026-05-27
```

```text
| Type | No. | Title | Status |
|---|---|---|---|
| Issue opened | [#12](https://github.com/my-org/some-repo/issues/12) | Document the release process | Open |
| PR merged | [#13](https://github.com/my-org/some-repo/pull/13) | Add release notes template | Merged |
| PR approved | [#9](https://github.com/my-org/some-repo/pull/9) | Fix CI cache key | Merged |
```

Rows are sorted by when the activity happened. Each item appears once, under
the first category that matches: opened, closed/merged, updated, reviewed.

## A whole organisation or user account

```bash
ghinsight activity --org my-org --user octocat --from 2026-09-16 --to 2026-09-24 --output week.md
```

With `--org`, items are labelled `repo#123`. A week across a large organisation
takes seconds; long ranges are slower, because every item closed in the range is
fetched to check who closed it. Ranges with more than 1,000 search results are
split automatically.

## What counts as activity

An issue or PR is listed when, within the range, it was:

- opened by `--user`;
- closed or merged, and either authored or closed/merged by `--user`;
- authored by `--user` earlier and updated by them (comments, commits,
  force-pushes, draft/ready changes, review requests), even if still open;
- reviewed by `--user` (someone else's PR; the type shows approved, changes
  requested or reviewed).

`--authored-only` drops the last category and PRs closed/merged by `--user`
that they did not author.

From Python, {py:func}`ghinsight.activity.build_rows` returns the rows and
{py:func}`ghinsight.activity.format_table` renders them.
