"""Domain types for GitHub Actions analysis.

These frozen records are the typed *contract* for everything downstream. A raw
GitHub API payload (an untyped ``dict``) is parsed exactly once, at the boundary,
into a :class:`Job` or :class:`Run`. From that point on the rest of the codebase
speaks in these types instead of dictionaries, so a function signature such as
``def usage_by_repo(jobs: Sequence[Job]) -> ...`` documents precisely what it
consumes.

Design notes
------------
* Records are ``frozen`` and ``slots`` — immutable value objects with no hidden
  state. Two records with equal fields are equal.
* Every *derived* quantity (durations, billed minutes, OS, failure flags) is a
  pure :func:`property`. The derivation lives next to the data it is computed
  from, and is computed on demand rather than stored, so there is a single
  source of truth for, e.g., "how is a billed minute defined?".
* Parsing is total: malformed or incomplete payloads yield ``None`` from the
  ``from_payload`` constructors rather than raising, so a single bad record
  never aborts a whole-org load.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

__all__ = [
    "RunnerType",
    "RuntimeOS",
    "Visibility",
    "Job",
    "Run",
    "BillingUsageItem",
    "MILLISECONDS_PER_MINUTE",
]

MILLISECONDS_PER_MINUTE = 60_000

# Keyword tables used to classify free-form runner labels into a small,
# stable set of operating systems. Order is irrelevant; matches are by
# substring against the space-joined, lower-cased label set.
_OS_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "linux": ("ubuntu", "linux", "debian"),
    "macos": ("macos", "mac-os", "osx"),
    "windows": ("windows", "win"),
}

# Runner-group names that identify GitHub-hosted runners. Anything else is
# treated as self-hosted.
_HOSTED_GROUP_NAMES = frozenset({"github actions", "default"})


class RunnerType(str, Enum):
    """Whether a job ran on a GitHub-hosted or a self-hosted runner."""

    HOSTED = "hosted"
    SELF_HOSTED = "self-hosted"


class RuntimeOS(str, Enum):
    """Operating system a job ran on, classified from its runner labels."""

    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"
    UNKNOWN = "unknown"

    @classmethod
    def from_labels(cls, labels: Sequence[str]) -> "RuntimeOS":
        """Classify a runner-label set into a :class:`RuntimeOS`."""
        flat = " ".join(labels).lower()
        for os_name, keywords in _OS_KEYWORDS.items():
            if any(keyword in flat for keyword in keywords):
                return cls(os_name)
        return cls.UNKNOWN


class Visibility(str, Enum):
    """A repository's visibility, which decides whether its hosted-runner
    minutes count towards the plan's included minutes.

    Public repositories on standard GitHub-hosted runners are free; private and
    internal repositories consume the plan quota. ``UNKNOWN`` means no
    repository metadata was cached.
    """

    PUBLIC = "public"
    PRIVATE = "private"
    INTERNAL = "internal"
    UNKNOWN = "unknown"

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Visibility":
        """Read a repository payload's ``visibility`` (or legacy ``private``)."""
        value = str(payload.get("visibility") or "").lower()
        if value in {v.value for v in cls}:
            return cls(value)
        private = payload.get("private")
        if private is None:
            return cls.UNKNOWN
        return cls.PRIVATE if private else cls.PUBLIC

    @property
    def uses_quota(self) -> bool:
        """Whether minutes may count towards the plan quota.

        ``UNKNOWN`` counts, so that missing metadata overstates rather than
        understates quota use.
        """
        return self is not Visibility.PUBLIC


def _parse_dt(value: str | None) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp into a timezone-aware datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(start: datetime | None, end: datetime | None) -> float | None:
    """Milliseconds between two instants, or ``None`` if either is missing."""
    if start is None or end is None:
        return None
    return (end - start).total_seconds() * 1000.0


def _classify_runner(runner_group_name: str | None) -> RunnerType:
    if runner_group_name and runner_group_name.lower() in _HOSTED_GROUP_NAMES:
        return RunnerType.HOSTED
    return RunnerType.SELF_HOSTED


@dataclass(frozen=True, slots=True)
class Job:
    """A single GitHub Actions job run.

    One :class:`Job` corresponds to one row in the canonical tidy table. All
    measures used by the analysis (billed minutes, run/queue time, failure
    flag) are derived from these raw fields via properties.
    """

    job_id: int
    run_id: int
    repo: str
    name: str
    workflow_name: str
    workflow_path: str
    status: str
    conclusion: str
    runner_type: RunnerType
    runtime_os: RuntimeOS
    labels: tuple[str, ...]
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        repo: str,
        workflow_path: str = "",
    ) -> "Job | None":
        """Parse a raw GitHub job payload, or ``None`` if it lacks an id."""
        job_id = payload.get("id")
        run_id = payload.get("run_id")
        if job_id is None or run_id is None:
            return None
        labels = tuple(payload.get("labels") or ())
        return cls(
            job_id=int(job_id),
            run_id=int(run_id),
            repo=repo,
            name=str(payload.get("name", "")),
            workflow_name=str(payload.get("workflow_name") or ""),
            workflow_path=workflow_path,
            status=str(payload.get("status") or ""),
            conclusion=str(payload.get("conclusion") or ""),
            runner_type=_classify_runner(payload.get("runner_group_name")),
            runtime_os=RuntimeOS.from_labels(labels),
            labels=labels,
            created_at=_parse_dt(payload.get("created_at")),
            started_at=_parse_dt(payload.get("started_at")),
            completed_at=_parse_dt(payload.get("completed_at")),
        )

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def is_hosted(self) -> bool:
        return self.runner_type is RunnerType.HOSTED

    @property
    def is_failure(self) -> bool:
        return self.conclusion == "failure"

    @property
    def is_success(self) -> bool:
        return self.conclusion == "success"

    @property
    def run_ms(self) -> float | None:
        """Execution time: ``completed_at - started_at`` in milliseconds."""
        return _duration_ms(self.started_at, self.completed_at)

    @property
    def queue_ms(self) -> float | None:
        """Queue time: ``started_at - created_at`` (clamped at zero), in ms."""
        raw = _duration_ms(self.created_at, self.started_at)
        return None if raw is None else max(raw, 0.0)

    @property
    def billed_minutes(self) -> int:
        """Billed minutes: ``ceil(run_ms / 60000)``, minimum zero.

        This mirrors GitHub's per-job rounding (each job rounds up to the
        next whole minute).
        """
        run_ms = self.run_ms
        if run_ms is None or run_ms <= 0:
            return 0
        return math.ceil(run_ms / MILLISECONDS_PER_MINUTE)

    @property
    def runner_labels(self) -> str:
        """Sorted, comma-joined runner labels (the GitHub-export form)."""
        return ",".join(sorted(self.labels))

    @property
    def month(self) -> str | None:
        """Calendar month (``YYYY-MM``) the job started in, if known."""
        if self.started_at is None:
            return None
        return self.started_at.strftime("%Y-%m")


@dataclass(frozen=True, slots=True)
class Run:
    """A single GitHub Actions workflow run.

    Used for workflow-level (wall-clock) performance metrics, which cannot be
    reconstructed from per-job rows alone.
    """

    run_id: int
    repo: str
    workflow_path: str
    conclusion: str
    created_at: datetime | None
    run_started_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, repo: str) -> "Run | None":
        """Parse a raw GitHub workflow-run payload, or ``None`` if no id."""
        run_id = payload.get("id")
        if run_id is None:
            return None
        return cls(
            run_id=int(run_id),
            repo=repo,
            workflow_path=str(payload.get("path") or ""),
            conclusion=str(payload.get("conclusion") or ""),
            created_at=_parse_dt(payload.get("created_at")),
            run_started_at=_parse_dt(payload.get("run_started_at")),
            updated_at=_parse_dt(payload.get("updated_at")),
        )

    @property
    def is_completed(self) -> bool:
        return bool(self.conclusion)

    @property
    def wall_ms(self) -> float | None:
        """Wall-clock duration: ``updated_at - run_started_at`` in ms.

        Mirrors the duration GitHub shows in the Actions UI.
        """
        return _duration_ms(self.run_started_at, self.updated_at)


@dataclass(frozen=True, slots=True)
class BillingUsageItem:
    """One line of GitHub's billing usage report (enhanced billing platform).

    Unlike :class:`Job`, which estimates billed minutes from timestamps, these
    are the quantities GitHub itself billed, aggregated per day, repository and
    SKU. They carry no repository visibility, but the report appears to omit
    free public-repository usage (see :mod:`ghinsight.billing`).
    """

    date: datetime | None
    product: str
    sku: str
    quantity: float
    unit_type: str
    price_per_unit: float
    gross_amount: float
    discount_amount: float
    net_amount: float
    repo: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "BillingUsageItem | None":
        """Parse one ``usageItems`` entry, or ``None`` if it has no product."""
        product = payload.get("product")
        if not product:
            return None
        # ``repositoryName`` may be ``owner/repo``; keep only the repo name to
        # match the cache layout.
        repo = str(payload.get("repositoryName") or "").rsplit("/", 1)[-1]
        return cls(
            date=_parse_dt(payload.get("date")),
            product=str(product),
            sku=str(payload.get("sku") or ""),
            quantity=_to_float(payload.get("quantity")),
            unit_type=str(payload.get("unitType") or ""),
            price_per_unit=_to_float(payload.get("pricePerUnit")),
            gross_amount=_to_float(payload.get("grossAmount")),
            discount_amount=_to_float(payload.get("discountAmount")),
            net_amount=_to_float(payload.get("netAmount")),
            repo=repo,
        )

    @property
    def is_actions_minutes(self) -> bool:
        """Whether this line is GitHub Actions runner time, in minutes."""
        return self.product.lower() == "actions" and self.unit_type.lower().startswith("minute")

    @property
    def runtime_os(self) -> RuntimeOS:
        """Runner OS, classified from the SKU (e.g. ``actions_linux``)."""
        return RuntimeOS.from_labels(self.sku.replace("_", " ").split())

    @property
    def month(self) -> str | None:
        return None if self.date is None else self.date.strftime("%Y-%m")


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime (test seam)."""
    return datetime.now(tz=timezone.utc)
