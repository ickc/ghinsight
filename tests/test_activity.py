"""Offline tests for ghinsight.activity and the ``ghinsight activity`` command."""

from __future__ import annotations

import json
from datetime import date

import httpx2
import pytest
from typer.testing import CliRunner

from ghinsight import activity
from ghinsight.activity import ActivityRow, build_rows, format_table, search
from ghinsight.client import GitHubClient, GitHubError

USER = "octo-user"
START, END = date(2026, 5, 20), date(2026, 5, 27)


def _search_page(nodes, *, count=None, next_cursor=None):
    return {
        "data": {
            "search": {
                "issueCount": len(nodes) if count is None else count,
                "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
                "nodes": nodes,
            }
        }
    }


def _issue(number, *, author=USER, state="OPEN", created="2026-05-21T10:00:00Z", **extra):
    return {
        "__typename": "Issue",
        "number": number,
        "title": f"Issue {number}",
        "repository": {"name": "some-repo"},
        "url": f"https://github.com/my-org/some-repo/issues/{number}",
        "state": state,
        "createdAt": created,
        "author": {"login": author},
        **extra,
    }


def _pr(number, *, author=USER, state="OPEN", **extra):
    return {
        "__typename": "PullRequest",
        "number": number,
        "title": f"PR {number} | with a pipe",
        "repository": {"name": "other-repo"},
        "url": f"https://github.com/my-org/other-repo/pull/{number}",
        "state": state,
        "author": {"login": author},
        **extra,
    }


class FakeGraphQL:
    """Answers search queries by which search they are, recording each query."""

    def __init__(self, *, created=(), closed=(), updated=(), reviewed=()):
        self.pages = {"created": created, "closed": closed, "updated": updated, "reviewed": reviewed}
        self.queries: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        variables = body["variables"]
        self.queries.append(variables)
        q = variables["query"]
        if "reviewed-by:" in q:
            kind = "reviewed"
        elif "updated:>=" in q:
            kind = "updated"
        elif " closed:" in q:
            kind = "closed"
        else:
            kind = "created"
        return httpx2.Response(200, json=_search_page(list(self.pages[kind])))


def _client(handler) -> GitHubClient:
    return GitHubClient(
        "t",
        base_url="https://api.example.test",
        transport=httpx2.MockTransport(handler),
        sleep=lambda seconds: None,
    )


def test_build_rows_covers_every_category():
    fake = FakeGraphQL(
        created=[_issue(1), _issue(2, author="someone-else")],
        closed=[
            _issue(1, state="CLOSED"),  # already listed as opened
            _pr(
                3,
                author="someone-else",
                state="MERGED",
                merged=True,
                mergedAt="2026-05-22T09:00:00Z",
                mergedBy={"login": USER},
            ),
            _issue(4, author="someone-else", state="CLOSED", closedAt="2026-05-22T00:00:00Z"),
        ],
        updated=[
            _issue(
                5,
                created="2026-01-01T00:00:00Z",
                activity={
                    "nodes": [
                        {"__typename": "IssueComment", "createdAt": "2026-05-23T08:00:00Z", "author": {"login": USER}},
                        {"__typename": "IssueComment", "createdAt": "2026-05-24T08:00:00Z", "author": {"login": "bot"}},
                    ]
                },
            ),
            _issue(6, created="2026-01-01T00:00:00Z", activity={"nodes": []}),  # no activity by USER
        ],
        reviewed=[
            _pr(
                7,
                author="someone-else",
                reviews={
                    "nodes": [
                        {"state": "COMMENTED", "submittedAt": "2026-05-24T00:00:00Z"},
                        {"state": "APPROVED", "submittedAt": "2026-05-25T00:00:00Z"},
                        {"state": "APPROVED", "submittedAt": "2026-06-30T00:00:00Z"},  # outside
                    ]
                },
            )
        ],
    )
    rows = build_rows(_client(fake), scope="org:my-org", user=USER, date_from=START, date_to=END)

    assert [(r.activity_type, r.number, r.status) for r in rows] == [
        ("Issue opened", 1, "Open"),
        ("PR merged", 3, "Merged"),
        ("Issue updated", 5, "Open"),
        ("PR approved", 7, "Open"),
    ]
    assert rows[2].event_at == "2026-05-23T08:00:00Z"
    queries = [q["query"] for q in fake.queries]
    assert "org:my-org author:octo-user created:2026-05-20..2026-05-27" in queries
    assert "org:my-org closed:2026-05-20..2026-05-27" in queries


def test_build_rows_authored_only_skips_reviews_and_others_items():
    fake = FakeGraphQL(
        closed=[
            _pr(3, author="someone-else", state="MERGED", merged=True, mergedAt="2026-05-22T09:00:00Z", mergedBy={"login": USER}),
            _pr(8, state="CLOSED", closedAt="2026-05-23T00:00:00Z"),
        ],
        reviewed=[_pr(7, author="someone-else", reviews={"nodes": [{"state": "APPROVED", "submittedAt": "2026-05-25T00:00:00Z"}]})],
    )
    rows = build_rows(
        _client(fake), scope="repo:my-org/r", user=USER, date_from=START, date_to=END, authored_only=True
    )
    assert [(r.activity_type, r.number) for r in rows] == [("PR closed", 8)]
    assert not any("reviewed-by:" in q["query"] for q in fake.queries)


def test_search_by_date_splits_ranges_over_the_limit():
    seen = []

    def handler(request):
        query = json.loads(request.content)["variables"]["query"]
        seen.append(query)
        if query.endswith("2026-05-20..2026-05-27"):
            return httpx2.Response(200, json=_search_page([], count=1500))
        return httpx2.Response(200, json=_search_page([_issue(len(seen))]))

    nodes = activity.search_by_date(_client(handler), "org:o created:{range}", START, END)
    assert len(nodes) == 2
    assert seen[1:] == ["org:o created:2026-05-20..2026-05-23", "org:o created:2026-05-24..2026-05-27"]


def test_search_follows_pages():
    def handler(request):
        after = json.loads(request.content)["variables"]["after"]
        if after is None:
            return httpx2.Response(200, json=_search_page([_issue(1)], count=2, next_cursor="c1"))
        assert after == "c1"
        return httpx2.Response(200, json=_search_page([_issue(2)], count=2))

    assert [n["number"] for n in search(_client(handler), "q")] == [1, 2]


def test_search_halves_page_size_on_server_errors():
    sizes = []

    def handler(request):
        first = json.loads(request.content)["variables"]["first"]
        sizes.append(first)
        if first > 25:
            return httpx2.Response(502)
        return httpx2.Response(200, json=_search_page([_issue(1)]))

    assert len(search(_client(handler), "q")) == 1
    assert sizes == [100, 50, 25]


def test_search_gives_up_at_the_minimum_page_size():
    def handler(request):
        return httpx2.Response(504)

    with pytest.raises(GitHubError) as info:
        search(_client(handler), "q")
    assert info.value.status == 504


def test_format_table_escapes_and_labels():
    rows = [
        ActivityRow("2026-05-22T00:00:00Z", "r", "PR", 3, "PR merged", "https://x/3", "a | b\nc", "Merged"),
        ActivityRow("2026-05-21T00:00:00Z", "r", "Issue", 1, "Issue opened", "https://x/1", "t", "Open"),
    ]
    assert format_table(rows, show_repo=True) == (
        "| Type | No. | Title | Status |\n"
        "|---|---|---|---|\n"
        "| Issue opened | [r#1](https://x/1) | t | Open |\n"
        "| PR merged | [r#3](https://x/3) | a \\| b c | Merged |\n"
    )
    assert "[#1](https://x/1)" in format_table(rows, show_repo=False)


def test_activity_cli(monkeypatch, tmp_path):
    from ghinsight import cli

    fake = FakeGraphQL(created=[_issue(1)])
    monkeypatch.setattr("ghinsight.client.GitHubClient", lambda: _client(fake))
    out = tmp_path / "table.md"
    result = CliRunner().invoke(
        cli.app,
        ["activity", "--repo", "my-org/some-repo", "--user", USER, "--from", "2026-05-20", "--to", "2026-05-27", "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert "| Issue opened | [#1](" in out.read_text()


@pytest.mark.parametrize(
    "args",
    [
        ["--user", USER, "--from", "2026-05-20", "--to", "2026-05-27"],  # no scope
        ["--repo", "o/r", "--org", "o", "--user", USER, "--from", "2026-05-20", "--to", "2026-05-27"],
        ["--repo", "o/r", "--user", USER, "--from", "2026-05-28", "--to", "2026-05-27"],
        ["--repo", "o/r", "--user", USER, "--from", "yesterday", "--to", "2026-05-27"],
    ],
)
def test_activity_cli_rejects_bad_arguments(args):
    from ghinsight import cli

    result = CliRunner().invoke(cli.app, ["activity", *args])
    assert result.exit_code == 2


def test_cli_version():
    from ghinsight import __version__, cli

    result = CliRunner().invoke(cli.app, ["--version"])
    assert result.exit_code == 0 and __version__ in result.output
