"""A user's issue and pull request activity over a date range, as a Markdown table.

Uses GitHub's GraphQL search. An issue or PR is listed when, within the range,
it was:

- opened by the user;
- closed or merged, and either authored or closed/merged by the user;
- authored by the user earlier and updated by them (comments, commits,
  force-pushes, draft/ready changes, review requests), even if still open;
- reviewed by the user (someone else's PR).

``authored_only`` drops the last category, and PRs closed/merged by the user
that they did not author.

Search returns at most 1,000 results per query, so date-ranged searches are
split in halves until each part fits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .client import GitHubClient, GitHubError

__all__ = ["ActivityRow", "SearchLimitExceeded", "build_rows", "format_table", "search"]

# GitHub search never returns more than this many results for one query.
SEARCH_RESULT_LIMIT = 1000
# Server errors that large pages with nested timelines can cause.
_SHRINKABLE_STATUSES = frozenset({500, 502, 503, 504})
_MIN_PAGE_SIZE = 10

SEARCH_QUERY = """
query($query: String!, $first: Int!, $after: String) {
  search(query: $query, type: ISSUE, first: $first, after: $after) {
    issueCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      __typename
      ... on Issue {
        number
        title
        repository {
          name
        }
        url
        state
        createdAt
        closedAt
        author {
          login
        }
        closedEvents: timelineItems(itemTypes: [CLOSED_EVENT], last: 20) {
          nodes {
            __typename
            ... on ClosedEvent {
              createdAt
              actor {
                login
              }
            }
          }
        }
      }
      ... on PullRequest {
        number
        title
        repository {
          name
        }
        url
        state
        createdAt
        closedAt
        merged
        mergedAt
        author {
          login
        }
        mergedBy {
          login
        }
        closeMergeEvents: timelineItems(
          itemTypes: [CLOSED_EVENT, MERGED_EVENT]
          last: 20
        ) {
          nodes {
            __typename
            ... on ClosedEvent {
              createdAt
              actor {
                login
              }
            }
            ... on MergedEvent {
              createdAt
              actor {
                login
              }
            }
          }
        }
      }
    }
  }
}
"""

ISSUE_ACTIVITY_ITEM_FIELDS = """
            __typename
            ... on IssueComment {
              createdAt
              author {
                login
              }
            }
"""

PR_ACTIVITY_ITEM_FIELDS = ISSUE_ACTIVITY_ITEM_FIELDS + """
            ... on PullRequestReview {
              submittedAt
              author {
                login
              }
            }
            ... on PullRequestCommit {
              commit {
                committedDate
                author {
                  user {
                    login
                  }
                }
              }
            }
            ... on ReadyForReviewEvent {
              createdAt
              actor {
                login
              }
            }
            ... on ConvertToDraftEvent {
              createdAt
              actor {
                login
              }
            }
            ... on ReviewRequestedEvent {
              createdAt
              actor {
                login
              }
            }
            ... on HeadRefForcePushedEvent {
              createdAt
              actor {
                login
              }
            }
"""

# Items authored earlier that may have seen activity in the date range.
UPDATED_QUERY = """
query($query: String!, $first: Int!, $after: String, $since: DateTime!) {
  search(query: $query, type: ISSUE, first: $first, after: $after) {
    issueCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      __typename
      ... on Issue {
        number
        title
        repository {
          name
        }
        url
        state
        author {
          login
        }
        activity: timelineItems(since: $since, first: 100, itemTypes: [ISSUE_COMMENT]) {
          nodes {""" + ISSUE_ACTIVITY_ITEM_FIELDS + """          }
        }
      }
      ... on PullRequest {
        number
        title
        repository {
          name
        }
        url
        state
        merged
        mergedAt
        author {
          login
        }
        activity: timelineItems(
          since: $since
          first: 100
          itemTypes: [
            ISSUE_COMMENT
            PULL_REQUEST_REVIEW
            PULL_REQUEST_COMMIT
            READY_FOR_REVIEW_EVENT
            CONVERT_TO_DRAFT_EVENT
            REVIEW_REQUESTED_EVENT
            HEAD_REF_FORCE_PUSHED_EVENT
          ]
        ) {
          nodes {""" + PR_ACTIVITY_ITEM_FIELDS + """          }
        }
      }
    }
  }
}
"""

# PRs by other people that --user reviewed.
REVIEWED_QUERY = """
query($query: String!, $first: Int!, $after: String, $user: String!) {
  search(query: $query, type: ISSUE, first: $first, after: $after) {
    issueCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      __typename
      ... on PullRequest {
        number
        title
        repository {
          name
        }
        url
        state
        merged
        mergedAt
        reviews(author: $user, last: 100) {
          nodes {
            state
            submittedAt
          }
        }
      }
    }
  }
}
"""

@dataclass(frozen=True)
class ActivityRow:
    event_at: str
    repo: str
    item_kind: str
    number: int
    activity_type: str
    url: str
    title: str
    status: str


def normalize_login(login: str | None) -> str:
    return (login or "").casefold()


def author_login(node: dict[str, Any]) -> str | None:
    author = node.get("author")
    if isinstance(author, dict):
        return author.get("login")
    return None


def is_authored_by(node: dict[str, Any], user: str) -> bool:
    return normalize_login(author_login(node)) == normalize_login(user)


def timeline_events(node: dict[str, Any]) -> list[dict[str, Any]]:
    issue_events = (node.get("closedEvents") or {}).get("nodes", [])
    pr_events = (node.get("closeMergeEvents") or {}).get("nodes", [])
    return [event for event in issue_events + pr_events if isinstance(event, dict)]


def event_actor_login(event: dict[str, Any]) -> str | None:
    actor = event.get("actor")
    if isinstance(actor, dict):
        return actor.get("login")
    return None


def was_closed_or_merged_by(node: dict[str, Any], user: str) -> bool:
    user_key = normalize_login(user)

    merged_by = node.get("mergedBy")
    if isinstance(merged_by, dict) and normalize_login(merged_by.get("login")) == user_key:
        return True

    return any(
        normalize_login(event_actor_login(event)) == user_key for event in timeline_events(node)
    )


def status_for(node: dict[str, Any]) -> str:
    if node.get("__typename") == "PullRequest" and (
        node.get("merged") or node.get("mergedAt") or node.get("state") == "MERGED"
    ):
        return "Merged"

    state = str(node.get("state") or "unknown")
    return state.lower().replace("_", " ").title()


def repo_for(node: dict[str, Any]) -> str:
    return str((node.get("repository") or {}).get("name", ""))


def kind_for(node: dict[str, Any]) -> str:
    return "PR" if node.get("__typename") == "PullRequest" else "Issue"


def closed_type_for(node: dict[str, Any]) -> str:
    kind = kind_for(node)
    if kind == "PR" and (node.get("merged") or node.get("mergedAt")):
        return "PR merged"
    return f"{kind} closed"


def event_time_for_closed(node: dict[str, Any]) -> str:
    if node.get("__typename") == "PullRequest" and node.get("mergedAt"):
        return str(node["mergedAt"])
    return str(node.get("closedAt") or node.get("mergedAt") or node.get("createdAt"))


def window_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(date_to, datetime.max.time(), tzinfo=timezone.utc)
    return start, end


def iso_sort_key(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def in_window(value: str | None, window: tuple[datetime, datetime]) -> bool:
    if not value:
        return False
    return window[0] <= iso_sort_key(value) <= window[1]


def activity_time_and_actor(item: dict[str, Any]) -> tuple[str | None, str | None]:
    if item.get("__typename") == "PullRequestCommit":
        commit = item.get("commit") or {}
        user = ((commit.get("author") or {}).get("user")) or {}
        return commit.get("committedDate"), user.get("login")
    when = item.get("submittedAt") or item.get("createdAt")
    actor = item.get("author") or item.get("actor") or {}
    return when, actor.get("login")


def latest_user_activity(
    node: dict[str, Any], user: str, window: tuple[datetime, datetime]
) -> str | None:
    user_key = normalize_login(user)
    times = [
        when
        for when, actor in map(activity_time_and_actor, (node.get("activity") or {}).get("nodes", []))
        if normalize_login(actor) == user_key and in_window(when, window)
    ]
    return max(times, key=iso_sort_key, default=None)


def latest_review_in_window(
    node: dict[str, Any], window: tuple[datetime, datetime]
) -> dict[str, Any] | None:
    reviews = [
        review
        for review in (node.get("reviews") or {}).get("nodes", [])
        if isinstance(review, dict) and in_window(review.get("submittedAt"), window)
    ]
    return max(reviews, key=lambda review: iso_sort_key(review["submittedAt"]), default=None)


def reviewed_type_for(review: dict[str, Any]) -> str:
    return {
        "APPROVED": "PR approved",
        "CHANGES_REQUESTED": "PR changes requested",
    }.get(str(review.get("state")), "PR reviewed")


class SearchLimitExceeded(RuntimeError):
    pass


def search(
    client: GitHubClient,
    query: str,
    *,
    graphql: str = SEARCH_QUERY,
    variables: dict[str, Any] | None = None,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    """Every node a GraphQL issue search returns, across pages.

    Large pages with nested timelines can time out server-side, so a server
    error halves the page size (down to 10) before the client's usual retries.

    Raises:
        SearchLimitExceeded: if the query matches more than 1,000 results.
    """
    nodes: list[dict[str, Any]] = []
    after: str | None = None
    page_size = 100

    for _ in range(max_pages):
        while True:
            request_variables = {**(variables or {}), "query": query, "first": page_size, "after": after}
            shrinkable = page_size > _MIN_PAGE_SIZE
            try:
                data = client.graphql(graphql, request_variables, retries=0 if shrinkable else None)
                break
            except GitHubError as exc:
                if not shrinkable or exc.status not in _SHRINKABLE_STATUSES:
                    raise
                page_size //= 2

        search_data = data["search"]
        if search_data["issueCount"] > SEARCH_RESULT_LIMIT:
            raise SearchLimitExceeded(
                f"{search_data['issueCount']} results (limit {SEARCH_RESULT_LIMIT}) "
                f"for query: {query}"
            )
        nodes.extend(node for node in search_data["nodes"] if isinstance(node, dict))
        page_info = search_data["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        after = page_info["endCursor"]
    else:
        raise RuntimeError(f"Search exceeded {max_pages} pages for query: {query}")

    return nodes


def search_by_date(
    client: GitHubClient,
    template: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    """Search with ``{range}`` filled in, splitting the range when it has too many results."""
    date_range = f"{date_from.isoformat()}..{date_to.isoformat()}"
    try:
        return search(client, template.format(range=date_range))
    except SearchLimitExceeded:
        if date_from == date_to:
            raise
    middle = date_from + (date_to - date_from) // 2
    return search_by_date(client, template, date_from, middle) + search_by_date(
        client, template, middle + timedelta(days=1), date_to
    )


def markdown_escape(value: str) -> str:
    return value.replace("|", r"\|").replace("\n", " ")


def format_table(rows: list[ActivityRow], *, show_repo: bool) -> str:
    """The rows as a Markdown table, oldest first.

    ``show_repo`` labels items ``repo#123`` instead of ``#123``, for org-wide tables.
    """
    lines = [
        "| Type | No. | Title | Status |",
        "|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda item: (iso_sort_key(item.event_at), item.repo, item.number)):
        label = f"{row.repo}#{row.number}" if show_repo else f"#{row.number}"
        lines.append(
            f"| {row.activity_type} | "
            f"[{label}]({row.url}) | "
            f"{markdown_escape(row.title)} | "
            f"{row.status} |"
        )
    return "\n".join(lines) + "\n"


def _row(node: dict[str, Any], *, event_at: str, kind: str, activity_type: str) -> ActivityRow:
    return ActivityRow(
        event_at=event_at,
        repo=repo_for(node),
        item_kind=kind,
        number=int(node["number"]),
        activity_type=activity_type,
        url=str(node["url"]),
        title=str(node["title"]),
        status=status_for(node),
    )


def build_rows(
    client: GitHubClient,
    *,
    scope: str,
    user: str,
    date_from: date,
    date_to: date,
    authored_only: bool = False,
) -> list[ActivityRow]:
    """The activity rows for ``user`` within ``scope`` (``repo:O/N`` or ``org:O``).

    Each item appears once, under the first category that matches: opened,
    closed/merged, updated, reviewed.
    """
    created_query = f"{scope} author:{user} created:{{range}}"
    closed_query = f"{scope} closed:{{range}}"
    updated_query = (
        f"{scope} author:{user} created:<{date_from.isoformat()} "
        f"updated:>={date_from.isoformat()}"
    )
    reviewed_query = (
        f"{scope} is:pr reviewed-by:{user} -author:{user} "
        f"created:<={date_to.isoformat()} updated:>={date_from.isoformat()}"
    )
    window = window_bounds(date_from, date_to)

    created_nodes = search_by_date(client, created_query, date_from, date_to)
    closed_nodes = search_by_date(client, closed_query, date_from, date_to)
    updated_nodes = search(
        client, updated_query, graphql=UPDATED_QUERY, variables={"since": window[0].isoformat()}
    )
    reviewed_nodes = (
        []
        if authored_only
        else search(client, reviewed_query, graphql=REVIEWED_QUERY, variables={"user": user})
    )

    rows: list[ActivityRow] = []
    seen: set[tuple[str, str, int]] = set()

    def add(node: dict[str, Any], kind: str, event_at: str, activity_type: str) -> None:
        rows.append(_row(node, event_at=event_at, kind=kind, activity_type=activity_type))
        seen.add((repo_for(node), kind, int(node["number"])))

    def key(node: dict[str, Any], kind: str) -> tuple[str, str, int]:
        return (repo_for(node), kind, int(node["number"]))

    for node in created_nodes:
        if is_authored_by(node, user):
            kind = kind_for(node)
            add(node, kind, str(node["createdAt"]), f"{kind} opened")

    for node in closed_nodes:
        kind = kind_for(node)
        if key(node, kind) in seen:
            continue
        include = is_authored_by(node, user)
        if not authored_only:
            include = include or was_closed_or_merged_by(node, user)
        if include:
            add(node, kind, event_time_for_closed(node), closed_type_for(node))

    for node in updated_nodes:
        kind = kind_for(node)
        if key(node, kind) in seen or not is_authored_by(node, user):
            continue
        event_at = latest_user_activity(node, user, window)
        if event_at is not None:
            add(node, kind, event_at, f"{kind} updated")

    for node in reviewed_nodes:
        if node.get("__typename") != "PullRequest" or key(node, "PR") in seen:
            continue
        review = latest_review_in_window(node, window)
        if review is not None:
            add(node, "PR", str(review["submittedAt"]), reviewed_type_for(review))

    return rows
