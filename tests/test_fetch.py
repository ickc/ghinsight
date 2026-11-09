"""Tests for the fetch layer that do not hit the network."""

from __future__ import annotations

import json
from pathlib import Path

from ghinsight import fetch


def _stub_actions_api(monkeypatch, repo_payloads):
    """Stub the network: ``repo_payloads`` maps repo name to its payload."""
    looked_up = []

    def fake_paginate(endpoint, **params):
        return []  # no runs

    def fake_get_repo(org, repo):
        looked_up.append(repo)
        return repo_payloads[repo]

    monkeypatch.setattr(fetch, "github_api_paginate", fake_paginate)
    monkeypatch.setattr(fetch, "list_account_repos", lambda org: list(repo_payloads.values()))
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
