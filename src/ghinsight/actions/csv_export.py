"""Export the aggregate metric tables as GitHub-UI-compatible CSVs.

These CSVs are a *projection* of the canonical dataset, kept so that generated
output can be diffed against GitHub's own metric exports (see
:mod:`ghinsight.actions.compare`). They are no longer the intermediate
representation the analysis is built from.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from .dataset import ActionsDataset
from .metrics import PERFORMANCE_TABLES, USAGE_TABLES, TableFn

log = logging.getLogger(__name__)

__all__ = ["write_reports", "Metric"]

Metric = Literal["usage", "performance", "both"]

_SUBDIRS: Mapping[str, tuple[str, Mapping[str, TableFn]]] = {
    "usage": ("actions-usage-metrics", USAGE_TABLES),
    "performance": ("actions-performance-metrics", PERFORMANCE_TABLES),
}


def _selected(metric: Metric) -> list[str]:
    if metric == "both":
        return ["usage", "performance"]
    if metric in _SUBDIRS:
        return [metric]
    raise ValueError("metric must be one of: usage, performance, both")


def write_reports(
    dataset: ActionsDataset, output_dir: Path, metric: Metric = "both"
) -> list[Path]:
    """Write the selected metric CSVs under ``output_dir`` and return the paths.

    Output layout mirrors the GitHub UI::

        {output_dir}/actions-usage-metrics/{table}.csv
        {output_dir}/actions-performance-metrics/{table}.csv
    """
    written: list[Path] = []
    for kind in _selected(metric):
        subdir, table_fns = _SUBDIRS[kind]
        target = output_dir / subdir
        target.mkdir(parents=True, exist_ok=True)
        for name, table_fn in table_fns.items():
            frame = table_fn(dataset)
            path = target / f"{name}.csv"
            frame.to_csv(path, index=False)
            log.info("Wrote %s (%d rows)", path, len(frame))
            written.append(path)
    return written
