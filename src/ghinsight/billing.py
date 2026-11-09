"""GitHub's billing usage report, as an optional companion to the dataset.

The :class:`~ghinsight.dataset.ActionsDataset` *estimates* billed minutes
from job timestamps. When the account's billing usage report has been cached
(see :func:`ghinsight.fetch.fetch_billing_usage`), :class:`BillingUsage`
provides the minutes GitHub actually billed, per day, repository and SKU.

The report is optional. :meth:`BillingUsage.from_cache` returns ``None`` when
nothing was cached, and every consumer must treat that as "use the estimates
only". There are two ways to cache it:

* :func:`ghinsight.fetch.fetch_billing_usage`, via the REST API, which
  appears to admit organisation owners only (billing managers get a 404); or
* :func:`import_usage_csv`, from the usage report CSV that billing managers can
  download from the organisation's billing pages (the *summarized* report
  covers up to a year and has the per-repository breakdown needed here).

The report appears to list only usage that counted towards the plan quota:
public-repository usage on standard runners, which is free, is omitted. So the
billed minutes are used as they are, with no visibility filter, and they
reflect each repository's visibility at the time of use.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .config import DateRange
from .domain import BillingUsageItem, Visibility
from .fetch import CachePaths, billing_months

__all__ = ["BillingUsage", "BILLING_COLUMNS", "CSV_TO_API_FIELDS", "import_usage_csv"]

BILLING_COLUMNS: tuple[str, ...] = (
    "date",
    "month",
    "repo",
    "visibility",
    "sku",
    "runtime_os",
    "minutes",
    "gross_amount",
    "discount_amount",
    "net_amount",
)


def _item_to_row(item: BillingUsageItem, visibility: Visibility) -> dict[str, Any]:
    return {
        "date": item.date,
        "month": item.month,
        "repo": item.repo,
        "visibility": visibility.value,
        "sku": item.sku,
        "runtime_os": item.runtime_os.value,
        "minutes": item.quantity,
        "gross_amount": item.gross_amount,
        "discount_amount": item.discount_amount,
        "net_amount": item.net_amount,
    }


def _in_range(item: BillingUsageItem, date_range: DateRange) -> bool:
    if item.date is None:
        return False
    date = item.date if item.date.tzinfo else item.date.replace(tzinfo=timezone.utc)
    since, until = (
        d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        for d in (date_range.since, date_range.until)
    )
    return since <= date < until


@dataclass(frozen=True)
class BillingUsage:
    """Cached billing usage items for one account, plus the months they cover."""

    org: str
    items: tuple[BillingUsageItem, ...]
    months: tuple[str, ...]
    expected_months: tuple[str, ...] = ()

    @classmethod
    def from_cache(
        cls, org: str, cache_dir: Path, date_range: DateRange | None = None
    ) -> "BillingUsage | None":
        """Load the cached report, or ``None`` if no month was cached.

        With ``date_range``, only the months and items inside it are kept and
        :attr:`expected_months` lists every month the range covers.
        """
        paths = CachePaths(cache_dir, org)
        expected = tuple(billing_months(date_range)) if date_range else ()
        files = sorted(paths.billing_dir.glob("*.json")) if paths.billing_dir.exists() else []
        if expected:
            files = [f for f in files if f.stem in expected]
        loaded = dict(_load_months(files))
        if not loaded:
            return None
        items = tuple(item for month_items in loaded.values() for item in month_items)
        if date_range is not None:
            items = tuple(item for item in items if _in_range(item, date_range))
        return cls(org=org, items=items, months=tuple(sorted(loaded)), expected_months=expected)

    @property
    def missing_months(self) -> tuple[str, ...]:
        """Months in the requested range with no cached report."""
        return tuple(m for m in self.expected_months if m not in self.months)

    def actions_minutes_frame(self, visibility: Mapping[str, Visibility]) -> pd.DataFrame:
        """One row per Actions-minutes usage item, tagged with repo visibility."""
        rows = [
            _item_to_row(item, visibility.get(item.repo, Visibility.UNKNOWN))
            for item in self.items
            if item.is_actions_minutes
        ]
        if not rows:
            return pd.DataFrame(columns=BILLING_COLUMNS)
        return pd.DataFrame.from_records(rows, columns=BILLING_COLUMNS)


def _load_months(files: Iterable[Path]) -> Iterable[tuple[str, list[BillingUsageItem]]]:
    for path in files:
        try:
            payloads = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payloads, list):
            continue
        items = [BillingUsageItem.from_payload(p) for p in payloads if isinstance(p, dict)]
        yield path.stem, [item for item in items if item is not None]


# Usage-report CSV columns and the REST API ``usageItems`` fields they map to.
CSV_TO_API_FIELDS: Mapping[str, str] = {
    "date": "date",
    "product": "product",
    "sku": "sku",
    "quantity": "quantity",
    "unit_type": "unitType",
    "applied_cost_per_quantity": "pricePerUnit",
    "gross_amount": "grossAmount",
    "discount_amount": "discountAmount",
    "net_amount": "netAmount",
    "organization": "organizationName",
    "repository": "repositoryName",
}


def _csv_row_to_item(row: Mapping[str, str]) -> dict[str, Any]:
    item: dict[str, Any] = {
        api: row[column] for column, api in CSV_TO_API_FIELDS.items() if column in row
    }
    for key in ("quantity", "pricePerUnit", "grossAmount", "discountAmount", "netAmount"):
        if key in item:
            try:
                item[key] = float(item[key])
            except ValueError:
                item[key] = 0.0
    return item


def import_usage_csv(csv_path: Path, org: str, cache_dir: Path) -> list[str]:
    """Cache a billing usage report CSV as if it had been fetched from the API.

    Rows are converted to the API's ``usageItems`` shape and written one file
    per month under ``{cache_dir}/_billing/{org}/``, replacing any cached
    month the CSV covers. Rows for other organisations are skipped. A month
    the CSV only partly covers is cached as partial, so export whole months.

    Returns the months written.
    """
    by_month: dict[str, list[dict[str, Any]]] = {}
    # ``utf-8-sig`` drops the byte-order mark GitHub puts before the header.
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("organization", org).lower() != org.lower():
                continue
            month = (row.get("date") or "")[:7]
            if len(month) == 7:
                by_month.setdefault(month, []).append(_csv_row_to_item(row))

    paths = CachePaths(cache_dir, org)
    paths.billing_dir.mkdir(parents=True, exist_ok=True)
    for month, items in by_month.items():
        paths.billing_file(month).write_text(json.dumps(items), encoding="utf-8")
    return sorted(by_month)
