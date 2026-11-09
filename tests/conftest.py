"""Shared pytest fixtures backed by the synthetic cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghinsight.dataset import ActionsDataset

from .synthetic import ORG, write_synthetic_cache


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return write_synthetic_cache(tmp_path / "cache")


@pytest.fixture
def dataset(cache_dir: Path) -> ActionsDataset:
    return ActionsDataset.from_cache(ORG, cache_dir)
