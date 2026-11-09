"""ghinsight (pronounced "gin-sight"): reusable tools for GitHub analysis.

The core needs only httpx2 and typer:

- :mod:`ghinsight.client`: GitHub REST/GraphQL client and token resolution;
- :mod:`ghinsight.repos`: repositories of an organisation or user;
- :mod:`ghinsight.activity`: a user's issue and pull request activity.

:mod:`ghinsight.actions` (GitHub Actions usage and performance, with a
dashboard) needs the ``actions`` extra: ``pip install 'ghinsight[actions]'``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .client import GitHubClient, GitHubError, resolve_token
from .repos import RepoInfo, list_repos

try:
    __version__ = version("ghinsight")
except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
    __version__ = "0+unknown"

__all__ = [
    "GitHubClient",
    "GitHubError",
    "RepoInfo",
    "__version__",
    "list_repos",
    "resolve_token",
]
