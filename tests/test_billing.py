"""Tests for the optional billing usage report: fetch fallbacks and parsing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from github.GithubException import GithubException

from ghinsight import fetch
from ghinsight.billing import BillingUsage
from ghinsight.config import DateRange
from ghinsight.domain import BillingUsageItem, RuntimeOS, Visibility

from .synthetic import ORG, write_synthetic_billing

PERIOD = DateRange(
    since=datetime(2024, 1, 1, tzinfo=timezone.utc),
    until=datetime(2024, 4, 1, tzinfo=timezone.utc),
)


def test_billing_months_are_calendar_months_in_range():
    assert fetch.billing_months(PERIOD) == ["2024-01", "2024-02", "2024-03"]
    mid_month = DateRange(datetime(2023, 12, 20), datetime(2024, 1, 2))
    assert fetch.billing_months(mid_month) == ["2023-12", "2024-01"]


def test_billing_item_parsing():
    item = BillingUsageItem.from_payload(
        {"date": "2024-02-03T00:00:00Z", "product": "actions", "sku": "actions_macos",
         "quantity": 4, "unitType": "Minutes", "repositoryName": "owner/repo"}
    )
    assert item is not None
    assert item.repo == "repo"
    assert item.runtime_os is RuntimeOS.MACOS
    assert item.is_actions_minutes
    assert item.month == "2024-02"
    assert BillingUsageItem.from_payload({"quantity": 1}) is None


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(fetch, "utcnow", lambda: datetime(2024, 3, 10, tzinfo=timezone.utc))


def test_fetch_billing_usage_caches_each_month(monkeypatch, tmp_path: Path, frozen_now):
    calls = []

    def fake_get(endpoint, month):
        calls.append((endpoint, month))
        return [{"product": "actions", "month": month}]

    monkeypatch.setattr(fetch, "get_billing_usage", fake_get)
    months = fetch.fetch_billing_usage("some-org", tmp_path, PERIOD)

    assert months == ["2024-01", "2024-02", "2024-03"]
    assert {e for e, _ in calls} == {"/organizations/some-org/settings/billing/usage"}
    cached = json.loads((tmp_path / "_billing" / "some-org" / "2024-01.json").read_text())
    assert cached == [{"product": "actions", "month": "2024-01"}]

    # A second run reuses completed months and refreshes only the current one.
    calls.clear()
    fetch.fetch_billing_usage("some-org", tmp_path, PERIOD)
    assert [m for _, m in calls] == ["2024-03"]


def test_fetch_billing_usage_without_access_returns_none(
    monkeypatch, tmp_path: Path, frozen_now
):
    calls = []

    def fake_get(endpoint, month):
        calls.append(endpoint)
        raise GithubException(404, {"message": "Not Found"}, None)

    monkeypatch.setattr(fetch, "get_billing_usage", fake_get)

    assert fetch.fetch_billing_usage("some-org", tmp_path, PERIOD) is None
    assert calls == [
        "/organizations/some-org/settings/billing/usage",
        "/users/some-org/settings/billing/usage",
    ]
    assert not (tmp_path / "_billing").exists()
    assert BillingUsage.from_cache("some-org", tmp_path, PERIOD) is None


def test_fetch_billing_usage_reraises_other_errors(monkeypatch, tmp_path: Path, frozen_now):
    def fake_get(endpoint, month):
        raise GithubException(500, {"message": "boom"}, None)

    monkeypatch.setattr(fetch, "get_billing_usage", fake_get)
    with pytest.raises(GithubException):
        fetch.fetch_billing_usage("some-org", tmp_path, PERIOD)


def test_billing_usage_from_cache(tmp_path: Path):
    cache = write_synthetic_billing(tmp_path / "cache")
    billing = BillingUsage.from_cache(ORG, cache, PERIOD)
    assert billing is not None
    assert billing.months == ("2024-01", "2024-02")
    assert billing.missing_months == ("2024-03",)

    visibility = {"api": Visibility.PRIVATE, "web": Visibility.PUBLIC}
    frame = billing.actions_minutes_frame(visibility)
    assert len(frame) == 5  # the packages line is not Actions minutes
    assert frame.groupby("repo")["minutes"].sum().to_dict() == {"api": 19.0, "web": 9.0}
    assert set(frame.loc[frame["repo"] == "web", "visibility"]) == {"public"}


def test_billing_usage_respects_date_range(tmp_path: Path):
    cache = write_synthetic_billing(tmp_path / "cache")
    january = DateRange(datetime(2024, 1, 1), datetime(2024, 2, 1))
    billing = BillingUsage.from_cache(ORG, cache, january)
    assert billing is not None
    assert billing.months == ("2024-01",)
    assert set(billing.actions_minutes_frame({})["month"]) == {"2024-01"}


def test_config_billing_flag(tmp_path: Path):
    from ghinsight.config import load_config

    config_file = tmp_path / "analysis.toml"
    config_file.write_text('[analysis]\norg = "x"\n', encoding="utf-8")
    assert load_config(config_file).fetch_billing is False
    config_file.write_text('[analysis]\norg = "x"\nbilling = true\n', encoding="utf-8")
    assert load_config(config_file).fetch_billing is True


# ---------------------------------------------------------------------------
# CSV import (usage report downloaded from the billing pages)
# ---------------------------------------------------------------------------

_CSV = (
    "﻿"  # GitHub's CSV starts with a byte-order mark
    '"date","product","sku","quantity","unit_type","applied_cost_per_quantity",'
    '"gross_amount","discount_amount","net_amount","organization","repository",'
    '"cost_center_name"\n'
    "2024-01-05,actions,actions_linux,4.5,minutes,0.008,0.036,0.036,0,demo-org,api,\n"
    "2024-01-06,actions,actions_storage,0.1,gigabyte-hours,0.0003,3E-05,3E-05,0,demo-org,api,\n"
    "2024-02-01,actions,actions_macos,2,minutes,0.08,0.16,0.06,0.1,demo-org,web,\n"
    "2024-02-01,actions,actions_linux,9,minutes,0.008,0.072,0.072,0,other-org,x,\n"
)


def test_import_usage_csv_matches_api_cache(tmp_path: Path):
    from ghinsight.billing import import_usage_csv

    csv_file = tmp_path / "usage.csv"
    csv_file.write_text(_CSV, encoding="utf-8")
    cache = tmp_path / "cache"

    assert import_usage_csv(csv_file, ORG, cache) == ["2024-01", "2024-02"]

    cached = json.loads((cache / "_billing" / ORG / "2024-01.json").read_text())
    assert cached[0] == {
        "date": "2024-01-05",
        "product": "actions",
        "sku": "actions_linux",
        "quantity": 4.5,
        "unitType": "minutes",
        "pricePerUnit": 0.008,
        "grossAmount": 0.036,
        "discountAmount": 0.036,
        "netAmount": 0.0,
        "organizationName": ORG,
        "repositoryName": "api",
    }

    billing = BillingUsage.from_cache(ORG, cache, PERIOD)
    assert billing is not None
    frame = billing.actions_minutes_frame({})
    # Storage is not minutes; the other org's row is skipped.
    assert frame[["repo", "runtime_os", "minutes"]].values.tolist() == [
        ["api", "linux", 4.5],
        ["web", "macos", 2.0],
    ]
    assert frame["net_amount"].sum() == 0.1


def test_import_billing_cli(tmp_path: Path):
    from typer.testing import CliRunner

    from ghinsight.cli import app

    csv_file = tmp_path / "usage.csv"
    csv_file.write_text(_CSV, encoding="utf-8")
    cache = tmp_path / "cache"
    runner = CliRunner()

    result = runner.invoke(
        app, ["import-billing", str(csv_file), "--org", ORG, "--cache-dir", str(cache)]
    )
    assert result.exit_code == 0, result.output
    assert "2 months" in result.output

    result = runner.invoke(
        app, ["import-billing", str(csv_file), "--org", "nobody", "--cache-dir", str(cache)]
    )
    assert result.exit_code == 1
