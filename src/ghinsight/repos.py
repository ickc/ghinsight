"""Repositories of a GitHub organisation or user account.

:func:`list_account_repos` returns the raw repository payloads, for callers that
cache them verbatim; :func:`list_repos` returns them as :class:`RepoInfo`.

For a user account, ``/users/{user}/repos`` lists public repositories only, even
for the token's own user. When the account is the authenticated user, the
listing therefore uses ``/user/repos?affiliation=owner``, which includes private
repositories.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .client import GitHubClient, GitHubError, default_client

__all__ = ["RepoInfo", "list_account_repos", "list_repos", "visibility_of"]

_VISIBILITIES = ("public", "private", "internal")


def visibility_of(payload: Mapping[str, Any]) -> str | None:
    """``public``, ``private`` or ``internal`` from a repository payload, if known."""
    if payload.get("visibility") in _VISIBILITIES:
        return str(payload["visibility"])
    if isinstance(payload.get("private"), bool):
        return "private" if payload["private"] else "public"
    return None


@dataclass(frozen=True, slots=True)
class RepoInfo:
    """Identity and metadata of a repository.

    ``visibility`` is ``public``, ``private`` or ``internal`` for GitHub
    repositories; callers may use other values (e.g. ``local``) for repositories
    that are not on GitHub, and ``unknown`` when it could not be determined.
    """

    slug: str
    url: str | None = None
    visibility: str = "unknown"
    default_branch: str | None = None
    fork: bool | None = None
    archived: bool | None = None
    pushed_at: str | None = None
    size_kb: int | None = None

    @property
    def owner(self) -> str:
        return self.slug.partition("/")[0]

    @property
    def name(self) -> str:
        return self.slug.partition("/")[2]

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]) -> RepoInfo:
        """Build from a REST repository payload."""
        return cls(
            slug=str(payload["full_name"]),
            url=payload.get("html_url"),
            visibility=visibility_of(payload) or "unknown",
            default_branch=payload.get("default_branch"),
            fork=payload.get("fork"),
            archived=payload.get("archived"),
            pushed_at=payload.get("pushed_at"),
            size_kb=payload.get("size"),
        )


def _authenticated_login(client: GitHubClient) -> str | None:
    try:
        user = client.get("/user")
    except GitHubError:
        return None  # no token, or a token that cannot read its own user
    return str(user.get("login")) if isinstance(user, dict) else None


def list_account_repos(owner: str, client: GitHubClient | None = None) -> list[dict[str, Any]]:
    """Raw payloads of every repository owned by an organisation or user.

    The organisation endpoint is tried first; a 404 means ``owner`` is a user.
    Which repositories are returned depends on what the token can see.
    """
    client = client or default_client()
    try:
        return client.paginate(f"/orgs/{owner}/repos", {"type": "all"})
    except GitHubError as exc:
        if exc.status != 404:
            raise
    login = _authenticated_login(client)
    if login is not None and login.casefold() == owner.casefold():
        return client.paginate("/user/repos", {"affiliation": "owner", "visibility": "all"})
    return client.paginate(f"/users/{owner}/repos", {"type": "owner"})


def list_repos(owner: str, client: GitHubClient | None = None) -> list[RepoInfo]:
    """Every repository owned by ``owner``, as :class:`RepoInfo`, sorted by slug."""
    repos = [RepoInfo.from_api(payload) for payload in list_account_repos(owner, client)]
    return sorted(repos, key=lambda repo: repo.slug.casefold())
