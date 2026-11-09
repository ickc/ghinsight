"""A small, typed client for the GitHub REST and GraphQL APIs (via httpx2).

This is the only module that talks to the GitHub API. It provides:

- :func:`resolve_token`: ``GH_TOKEN``/``GITHUB_TOKEN``, else ``gh auth token``;
- :class:`GitHubClient`: ``get``, ``paginate`` (follows ``Link: rel="next"``)
  and ``graphql``, with rate-limit waits and retries of transient errors;
- :func:`github_api` and :func:`github_api_paginate`: the same calls on a shared
  default client, for code that does not manage a client itself.

Every failure surfaces as :class:`GitHubError`, which carries the HTTP status.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx2

__all__ = [
    "DEFAULT_API_URL",
    "GitHubClient",
    "GitHubError",
    "default_client",
    "github_api",
    "github_api_paginate",
    "resolve_token",
]

DEFAULT_API_URL = "https://api.github.com"
_API_VERSION = "2022-11-28"
_PER_PAGE = 100
# Transient server errors worth retrying, with exponential backoff.
_RETRY_STATUSES = frozenset({500, 502, 503, 504})


def _user_agent() -> str:
    try:
        return f"ghinsight/{version('ghinsight')}"
    except PackageNotFoundError:  # pragma: no cover - running from a bare checkout
        return "ghinsight"


class GitHubError(RuntimeError):
    """A failed GitHub API call.

    ``status`` is the HTTP status code, or ``None`` for transport failures and
    GraphQL errors returned with HTTP 200.
    """

    def __init__(self, status: int | None, message: str, url: str = "") -> None:
        self.status = status
        self.message = message
        self.url = url
        prefix = f"HTTP {status}" if status is not None else "GitHub API error"
        super().__init__(f"{prefix}: {message}" + (f" ({url})" if url else ""))


def _token_from_gh() -> str | None:
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def resolve_token(*, required: bool = True) -> str | None:
    """A GitHub token from ``GH_TOKEN``, ``GITHUB_TOKEN``, or ``gh auth token``.

    The environment variables take precedence in the same order as the ``gh``
    CLI. The ``gh`` fallback works wherever ``gh`` keeps its credentials
    (system keyring or ``hosts.yml``).

    Raises:
        GitHubError: if no token is found and ``required`` is true.
    """
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(name)
        if token:
            return token
    token = _token_from_gh()
    if token or not required:
        return token
    raise GitHubError(
        None,
        "No GitHub token found. Set GH_TOKEN or GITHUB_TOKEN, or run `gh auth login`.",
    )


def _error_message(response: httpx2.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    return response.reason_phrase


def _rate_limit_wait(response: httpx2.Response, now: float) -> float | None:
    """Seconds to wait if ``response`` is a rate-limit rejection, else ``None``.

    GitHub signals rate limits with 403 or 429 plus either ``Retry-After``
    (secondary limits) or ``X-RateLimit-Remaining: 0`` (primary limit). A 403
    without those headers is a permission error and is not retried.
    """
    if response.status_code not in (403, 429):
        return None
    retry_after = response.headers.get("retry-after")
    if retry_after is not None and retry_after.isdigit():
        return float(retry_after)
    if response.headers.get("x-ratelimit-remaining") == "0":
        reset = response.headers.get("x-ratelimit-reset", "")
        return max(float(reset) - now, 0.0) + 1.0 if reset.isdigit() else 60.0
    if response.status_code == 429:
        return 60.0
    return None


class GitHubClient:
    """A GitHub API client.

    Args:
        token: the token to send; ``None`` resolves one with :func:`resolve_token`.
        base_url: the REST API root; defaults to ``GITHUB_API_URL`` (set inside
            GitHub Actions, and on GitHub Enterprise Server) or ``api.github.com``.
        max_rate_limit_wait: the longest a single rate-limit wait may be, in
            seconds; a longer one raises instead of sleeping.
        retries: how many times to retry rate limits and transient server errors.
        transport: an httpx2 transport, e.g. ``httpx2.MockTransport`` in tests.
        sleep, clock: injectable for tests.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_rate_limit_wait: float = 3600.0,
        retries: int = 3,
        transport: httpx2.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.base_url = (base_url or os.environ.get("GITHUB_API_URL") or DEFAULT_API_URL).rstrip("/")
        self.max_rate_limit_wait = max_rate_limit_wait
        self.retries = retries
        self._sleep = sleep
        self._clock = clock
        resolved = token if token is not None else resolve_token()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": _user_agent(),
        }
        if resolved:
            headers["Authorization"] = f"Bearer {resolved}"
        self._http = httpx2.Client(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=transport
        )

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- transport ---------------------------------------------------------

    def _send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        retry_on_rate_limit: bool = True,
    ) -> httpx2.Response:
        for attempt in range(self.retries + 1):
            last = attempt == self.retries
            try:
                response = self._http.request(method, url, params=params, json=json)
            except httpx2.TransportError as exc:
                if last:
                    raise GitHubError(None, str(exc) or type(exc).__name__, url) from exc
                self._sleep(2.0**attempt)
                continue

            if response.is_success:
                return response
            wait = _rate_limit_wait(response, self._clock()) if retry_on_rate_limit else None
            if wait is not None and not last and wait <= self.max_rate_limit_wait:
                self._sleep(wait)
                continue
            if response.status_code in _RETRY_STATUSES and not last:
                self._sleep(2.0**attempt)
                continue
            raise GitHubError(response.status_code, _error_message(response), str(response.url))
        raise AssertionError("unreachable")  # pragma: no cover

    # -- REST --------------------------------------------------------------

    def get(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        retry_on_rate_limit: bool = True,
    ) -> Any:
        """GET one REST endpoint (``/repos/{owner}/{repo}`` or a full URL)."""
        response = self._send("GET", endpoint, params=params, retry_on_rate_limit=retry_on_rate_limit)
        return response.json()

    def paginate(
        self,
        endpoint: str,
        params: Mapping[str, Any] | None = None,
        *,
        response_key: str | None = None,
        retry_on_rate_limit: bool = True,
    ) -> list[Any]:
        """GET every page of a list endpoint, following ``Link: rel="next"``.

        Args:
            response_key: for endpoints that wrap the list in an object (e.g.
                ``workflow_runs``), the key holding it; ``None`` for bare lists.
        """
        items: list[Any] = []
        url: str | None = endpoint
        query: Mapping[str, Any] | None = {"per_page": _PER_PAGE, **(params or {})}
        while url is not None:
            response = self._send("GET", url, params=query, retry_on_rate_limit=retry_on_rate_limit)
            payload = response.json()
            if response_key is None:
                batch = payload
            elif isinstance(payload, dict):
                batch = payload.get(response_key, [])
            else:
                batch = None
            if not isinstance(batch, list):
                where = f" under {response_key!r}" if response_key else ""
                raise GitHubError(
                    response.status_code, f"expected a list{where}, got {type(batch).__name__}", url
                )
            items.extend(batch)
            url = response.links.get("next", {}).get("url")
            query = None  # the next link already carries the query string
        return items

    # -- GraphQL -----------------------------------------------------------

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Run a GraphQL query and return its ``data``.

        Raises:
            GitHubError: on HTTP failure, or when the response carries ``errors``.
        """
        # api.github.com/graphql, or {host}/api/graphql beside GHES's {host}/api/v3.
        url = f"{self.base_url.removesuffix('/v3')}/graphql"
        response = self._send("POST", url, json={"query": query, "variables": dict(variables or {})})
        payload = response.json()
        if payload.get("errors"):
            messages = "; ".join(str(e.get("message", e)) for e in payload["errors"])
            raise GitHubError(None, messages, url)
        return payload.get("data") or {}


@lru_cache(maxsize=1)
def default_client() -> GitHubClient:
    """A shared client using :func:`resolve_token`, created on first use."""
    return GitHubClient()


def github_api(endpoint: str, *, retry_on_rate_limit: bool = True, **params: str) -> Any:
    """GET one endpoint with the default client.

    Set ``retry_on_rate_limit=False`` where a 403 more likely means "no
    permission" than "rate limited", to fail fast instead of backing off.
    """
    return default_client().get(endpoint, params or None, retry_on_rate_limit=retry_on_rate_limit)


def github_api_paginate(
    endpoint: str,
    response_key: str | None = None,
    retry_on_rate_limit: bool = True,
    **params: str,
) -> list[Any]:
    """GET every page of a list endpoint with the default client."""
    return default_client().paginate(
        endpoint, params or None, response_key=response_key, retry_on_rate_limit=retry_on_rate_limit
    )
