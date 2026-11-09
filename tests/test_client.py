"""Offline tests for the GitHub API client and repository listing."""

from __future__ import annotations

import json

import httpx2
import pytest

from ghinsight import client as client_module
from ghinsight.client import GitHubClient, GitHubError, resolve_token
from ghinsight.repos import RepoInfo, list_account_repos, list_repos


def make_client(handler, **kwargs) -> tuple[GitHubClient, list[float]]:
    sleeps: list[float] = []
    gh = GitHubClient(
        "test-token",
        base_url="https://api.example.test",
        transport=httpx2.MockTransport(handler),
        sleep=sleeps.append,
        clock=lambda: 1000.0,
        **kwargs,
    )
    return gh, sleeps


# -- token ---------------------------------------------------------------------


def test_resolve_token_prefers_gh_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "from-gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "from-github-token")
    assert resolve_token() == "from-gh-token"


def test_resolve_token_falls_back_to_gh_cli(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(client_module, "_token_from_gh", lambda: "from-gh-cli")
    assert resolve_token() == "from-gh-cli"


def test_resolve_token_missing(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(client_module, "_token_from_gh", lambda: None)
    assert resolve_token(required=False) is None
    with pytest.raises(GitHubError, match="No GitHub token"):
        resolve_token()


# -- REST ----------------------------------------------------------------------


def test_get_sends_auth_and_api_headers():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update(request.headers)
        return httpx2.Response(200, json={"ok": True})

    gh, _ = make_client(handler)
    assert gh.get("/rate_limit") == {"ok": True}
    assert seen["authorization"] == "Bearer test-token"
    assert seen["x-github-api-version"] == "2022-11-28"
    assert seen["user-agent"].startswith("ghinsight")


def test_paginate_follows_link_header():
    def handler(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params.get("page", "1"))
        assert request.url.params["per_page"] == "100"
        assert request.url.params["filter"] == "all"
        headers = {}
        if page < 3:
            nxt = f"https://api.example.test/things?filter=all&per_page=100&page={page + 1}"
            headers["link"] = f'<{nxt}>; rel="next"'
        return httpx2.Response(200, json={"jobs": [page]}, headers=headers)

    gh, _ = make_client(handler)
    assert gh.paginate("/things", {"filter": "all"}, response_key="jobs") == [1, 2, 3]


def test_paginate_rejects_non_list():
    gh, _ = make_client(lambda request: httpx2.Response(200, json={"message": "odd"}))
    with pytest.raises(GitHubError, match="expected a list"):
        gh.paginate("/things")


def test_permission_403_is_not_retried():
    calls = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(403, json={"message": "Resource not accessible"})

    gh, sleeps = make_client(handler)
    with pytest.raises(GitHubError) as info:
        gh.get("/orgs/x/settings/billing/usage")
    assert info.value.status == 403
    assert "Resource not accessible" in str(info.value)
    assert len(calls) == 1 and sleeps == []


def test_primary_rate_limit_waits_until_reset():
    responses = iter(
        [
            httpx2.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"},
            ),
            httpx2.Response(200, json={"ok": True}),
        ]
    )
    gh, sleeps = make_client(lambda request: next(responses))
    assert gh.get("/x") == {"ok": True}
    assert sleeps == [31.0]  # reset - now + 1


def test_secondary_rate_limit_honours_retry_after():
    responses = iter(
        [
            httpx2.Response(429, headers={"retry-after": "7"}),
            httpx2.Response(200, json=[]),
        ]
    )
    gh, sleeps = make_client(lambda request: next(responses))
    assert gh.get("/x") == []
    assert sleeps == [7.0]


def test_rate_limit_wait_beyond_cap_raises():
    def handler(request):
        return httpx2.Response(429, headers={"retry-after": "7200"})

    gh, sleeps = make_client(handler, max_rate_limit_wait=60)
    with pytest.raises(GitHubError) as info:
        gh.get("/x")
    assert info.value.status == 429 and sleeps == []


def test_rate_limit_retry_can_be_disabled():
    def handler(request):
        return httpx2.Response(403, headers={"x-ratelimit-remaining": "0"})

    gh, sleeps = make_client(handler)
    with pytest.raises(GitHubError):
        gh.get("/x", retry_on_rate_limit=False)
    assert sleeps == []


def test_transient_errors_are_retried_with_backoff():
    responses = iter([httpx2.Response(502), httpx2.Response(503), httpx2.Response(200, json=1)])
    gh, sleeps = make_client(lambda request: next(responses))
    assert gh.get("/x") == 1
    assert sleeps == [1.0, 2.0]


def test_transient_errors_give_up_after_retries():
    gh, sleeps = make_client(lambda request: httpx2.Response(500), retries=2)
    with pytest.raises(GitHubError) as info:
        gh.get("/x")
    assert info.value.status == 500 and sleeps == [1.0, 2.0]


def test_transport_errors_become_github_errors():
    def handler(request):
        raise httpx2.ConnectError("connection refused")

    gh, _ = make_client(handler, retries=1)
    with pytest.raises(GitHubError) as info:
        gh.get("/x")
    assert info.value.status is None


# -- GraphQL -------------------------------------------------------------------


def test_graphql_returns_data():
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.example.test/graphql"
        body = json.loads(request.content)
        assert body == {"query": "{ viewer { login } }", "variables": {}}
        return httpx2.Response(200, json={"data": {"viewer": {"login": "octocat"}}})

    gh, _ = make_client(handler)
    assert gh.graphql("{ viewer { login } }") == {"viewer": {"login": "octocat"}}


def test_graphql_errors_raise():
    def handler(request):
        return httpx2.Response(200, json={"data": None, "errors": [{"message": "bad field"}]})

    gh, _ = make_client(handler)
    with pytest.raises(GitHubError, match="bad field"):
        gh.graphql("{ nope }")


def test_graphql_url_on_enterprise_server():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx2.Response(200, json={"data": {}})

    gh = GitHubClient(
        "t", base_url="https://ghe.example.test/api/v3", transport=httpx2.MockTransport(handler)
    )
    gh.graphql("{ viewer { login } }")
    assert seen == ["https://ghe.example.test/api/graphql"]


# -- repositories --------------------------------------------------------------


def _repo(full_name: str, private: bool) -> dict:
    return {
        "full_name": full_name,
        "name": full_name.split("/")[1],
        "html_url": f"https://github.com/{full_name}",
        "private": private,
        "visibility": "private" if private else "public",
        "default_branch": "main",
        "fork": False,
        "archived": False,
        "pushed_at": "2026-01-01T00:00:00Z",
        "size": 12,
    }


def _account_handler(viewer: str | None, calls: list[str]):
    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(f"{request.url.path}?{request.url.query.decode()}")
        path = request.url.path
        if path == "/orgs/my-org/repos":
            return httpx2.Response(200, json=[_repo("my-org/b", True), _repo("my-org/A", False)])
        if path.startswith("/orgs/"):
            return httpx2.Response(404, json={"message": "Not Found"})
        if path == "/user":
            if viewer is None:
                return httpx2.Response(401, json={"message": "Requires authentication"})
            return httpx2.Response(200, json={"login": viewer})
        if path == "/user/repos":
            return httpx2.Response(200, json=[_repo(f"{viewer}/secret", True)])
        if path.startswith("/users/"):
            owner = path.split("/")[2]
            return httpx2.Response(200, json=[_repo(f"{owner}/public", False)])
        return httpx2.Response(404, json={"message": "Not Found"})

    return handler


def test_list_repos_for_an_organisation():
    calls: list[str] = []
    gh, _ = make_client(_account_handler("someone", calls))
    repos = list_repos("my-org", gh)
    assert [r.slug for r in repos] == ["my-org/A", "my-org/b"]
    assert repos[1] == RepoInfo(
        slug="my-org/b",
        url="https://github.com/my-org/b",
        visibility="private",
        default_branch="main",
        fork=False,
        archived=False,
        pushed_at="2026-01-01T00:00:00Z",
        size_kb=12,
    )
    assert calls == ["/orgs/my-org/repos?per_page=100&type=all"]


def test_list_repos_for_the_authenticated_user_includes_private():
    calls: list[str] = []
    gh, _ = make_client(_account_handler("Octo-User", calls))
    payloads = list_account_repos("octo-user", gh)
    assert [p["full_name"] for p in payloads] == ["Octo-User/secret"]
    assert calls[-1] == "/user/repos?per_page=100&affiliation=owner&visibility=all"


def test_list_repos_for_another_user_lists_owned_public_repos():
    calls: list[str] = []
    gh, _ = make_client(_account_handler("someone-else", calls))
    assert [r.slug for r in list_repos("octo-user", gh)] == ["octo-user/public"]
    assert calls[-1] == "/users/octo-user/repos?per_page=100&type=owner"


def test_list_repos_without_a_token_lists_public_repos():
    calls: list[str] = []
    gh, _ = make_client(_account_handler(None, calls))
    assert [r.slug for r in list_repos("octo-user", gh)] == ["octo-user/public"]


def test_list_repos_reraises_non_404():
    def handler(request):
        return httpx2.Response(403, json={"message": "Forbidden"})

    gh, _ = make_client(handler)
    with pytest.raises(GitHubError) as info:
        list_repos("blocked", gh)
    assert info.value.status == 403


def test_repo_info_visibility_falls_back_to_private_flag():
    payload = {"full_name": "o/r", "private": True}
    info = RepoInfo.from_api(payload)
    assert (info.visibility, info.owner, info.name) == ("private", "o", "r")
    assert RepoInfo.from_api({"full_name": "o/r"}).visibility == "unknown"
