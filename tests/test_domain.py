"""Unit tests for the domain records and their derived properties."""

from __future__ import annotations

from ghinsight.domain import Job, Run, RunnerType, RuntimeOS, Visibility


def _job_payload(**overrides):
    payload = {
        "id": 1,
        "run_id": 10,
        "name": "build",
        "workflow_name": "ci",
        "status": "completed",
        "conclusion": "success",
        "runner_group_name": "GitHub Actions",
        "labels": ["ubuntu-latest"],
        "created_at": "2024-01-01T00:00:00Z",
        "started_at": "2024-01-01T00:00:30Z",
        "completed_at": "2024-01-01T00:04:30Z",
    }
    payload.update(overrides)
    return payload


def test_runtime_os_classification():
    assert RuntimeOS.from_labels(["ubuntu-latest"]) is RuntimeOS.LINUX
    assert RuntimeOS.from_labels(["windows-2022"]) is RuntimeOS.WINDOWS
    assert RuntimeOS.from_labels(["macos-14"]) is RuntimeOS.MACOS
    assert RuntimeOS.from_labels(["weird-runner"]) is RuntimeOS.UNKNOWN


def test_job_derived_measures():
    job = Job.from_payload(_job_payload(), repo="api", workflow_path=".github/workflows/ci.yml")
    assert job is not None
    assert job.runner_type is RunnerType.HOSTED
    assert job.runtime_os is RuntimeOS.LINUX
    assert job.run_ms == 4 * 60_000
    assert job.queue_ms == 30_000
    assert job.billed_minutes == 4
    assert job.is_success and not job.is_failure
    assert job.month == "2024-01"


def test_billed_minutes_rounds_up():
    job = Job.from_payload(
        _job_payload(completed_at="2024-01-01T00:03:31Z"), repo="r", workflow_path="p"
    )
    assert job is not None
    # 3 min 1 s of execution rounds up to 4 billed minutes.
    assert job.billed_minutes == 4


def test_self_hosted_detection():
    job = Job.from_payload(
        _job_payload(runner_group_name="my-group", labels=["self-hosted"]), repo="r"
    )
    assert job is not None
    assert job.runner_type is RunnerType.SELF_HOSTED
    assert not job.is_hosted


def test_negative_queue_clamped_to_zero():
    job = Job.from_payload(
        _job_payload(created_at="2024-01-01T00:01:00Z", started_at="2024-01-01T00:00:30Z"),
        repo="r",
    )
    assert job is not None
    assert job.queue_ms == 0.0


def test_missing_id_returns_none():
    assert Job.from_payload({"run_id": 1}, repo="r") is None
    assert Run.from_payload({}, repo="r") is None


def test_run_wall_clock():
    run = Run.from_payload(
        {
            "id": 5,
            "path": ".github/workflows/ci.yml",
            "conclusion": "success",
            "run_started_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:10:00Z",
        },
        repo="api",
    )
    assert run is not None
    assert run.wall_ms == 10 * 60_000
    assert run.is_completed


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def test_visibility_from_payload():
    assert Visibility.from_payload({"visibility": "internal", "private": True}) is Visibility.INTERNAL
    assert Visibility.from_payload({"private": False}) is Visibility.PUBLIC
    assert Visibility.from_payload({"private": True}) is Visibility.PRIVATE
    assert Visibility.from_payload({}) is Visibility.UNKNOWN


def test_visibility_uses_quota():
    assert not Visibility.PUBLIC.uses_quota
    assert Visibility.PRIVATE.uses_quota
    assert Visibility.INTERNAL.uses_quota
    assert Visibility.UNKNOWN.uses_quota
