"""Tests for the fetch layer that do not hit the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from github.GithubException import GithubException

from ghinsight import fetch


def test_list_account_repos_uses_org_endpoint(monkeypatch):
    calls = []

    def fake_paginate(endpoint, **params):
        calls.append(endpoint)
        return [{"name": "repo-a"}, {"name": "repo-b"}]

    monkeypatch.setattr(fetch, "github_api_paginate", fake_paginate)
    repos = fetch.list_account_repos("some-org")

    assert [r["name"] for r in repos] == ["repo-a", "repo-b"]
    assert calls == ["/orgs/some-org/repos"]


def test_list_account_repos_falls_back_to_user_endpoint(monkeypatch):
    calls = []

    def fake_paginate(endpoint, **params):
        calls.append(endpoint)
        if endpoint.startswith("/orgs/"):
            raise GithubException(404, {"message": "Not Found"}, None)
        return [{"name": "user-repo"}]

    monkeypatch.setattr(fetch, "github_api_paginate", fake_paginate)
    repos = fetch.list_account_repos("example-user")

    assert [r["name"] for r in repos] == ["user-repo"]
    assert calls == ["/orgs/example-user/repos", "/users/example-user/repos"]


def test_list_account_repos_reraises_non_404(monkeypatch):
    def fake_paginate(endpoint, **params):
        raise GithubException(403, {"message": "Forbidden"}, None)

    monkeypatch.setattr(fetch, "github_api_paginate", fake_paginate)
    with pytest.raises(GithubException):
        fetch.list_account_repos("blocked")


def _stub_actions_api(monkeypatch, repo_payloads):
    """Stub the network: ``repo_payloads`` maps repo name to its payload."""
    looked_up = []

    def fake_paginate(endpoint, **params):
        if endpoint.startswith("/orgs/"):
            return list(repo_payloads.values())
        return []  # no runs

    def fake_get_repo(org, repo):
        looked_up.append(repo)
        return repo_payloads[repo]

    monkeypatch.setattr(fetch, "github_api_paginate", fake_paginate)
    monkeypatch.setattr(fetch, "get_repo", fake_get_repo)
    return looked_up


def test_fetch_org_caches_trimmed_repo_metadata(monkeypatch, tmp_path: Path):
    payload = {"name": "a", "private": True, "visibility": "private", "owner": {"login": "x"}}
    looked_up = _stub_actions_api(monkeypatch, {"a": payload})

    assert fetch.fetch_org("some-org", tmp_path) == ["a"]

    cached = json.loads((tmp_path / "some-org" / "a" / "repo.json").read_text())
    assert cached == {"name": "a", "private": True, "visibility": "private"}
    assert looked_up == []  # taken from the listing, no extra request


def test_fetch_repo_looks_up_metadata_once(monkeypatch, tmp_path: Path):
    looked_up = _stub_actions_api(monkeypatch, {"a": {"name": "a", "visibility": "public"}})

    fetch.fetch_repo("some-org", "a", tmp_path)
    fetch.fetch_repo("some-org", "a", tmp_path)

    assert looked_up == ["a"]


def test_fetch_repo_survives_missing_metadata(monkeypatch, tmp_path: Path):
    _stub_actions_api(monkeypatch, {})  # get_repo raises KeyError

    assert fetch.fetch_repo("some-org", "a", tmp_path) == []
    assert not (tmp_path / "some-org" / "a" / "repo.json").exists()
