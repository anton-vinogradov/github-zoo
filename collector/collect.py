"""Gathering everything that is open and involves you, from every angle.

Four kinds of thing land on the dashboard, and each needs a different query:

* **Pull requests and issues** — found by search. One search cannot express
  "everything that touches me": `involves:` covers authoring, commenting and
  mentions but not a pending review request, and neither covers an issue
  someone filed on your own repository and nobody has answered. So there are
  several searches, unioned by node id.
* **Review threads** — the conversations *inside* a pull request. These are
  where work actually gets forgotten, and they are expensive to fetch, so they
  are pulled only for pull requests whose `updatedAt` moved.
* **Discussions** — a separate GraphQL type that no issue search will return.
* **Notifications** — the inbox, which is the only source that knows what is
  unread. It is also mostly CI noise, so it arrives filtered.

The cost model matters. GraphQL bills roughly two points per fully-detailed
pull request against a 5000/hour budget, so a poll that re-fetches all of them
every five minutes would spend a third of the budget doing nothing. Search is
cheap and reports `updatedAt`; details are fetched only when that moved.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

# `involves:` is the broad net (author, commenter, mentioned, assigned).
# `user:` catches the case involves cannot: something opened on your own
# repository that you have not touched yet — which is exactly the thing most
# worth surfacing.
SEARCHES = [
    ("involves", "involves:{login} is:open"),
    ("owner", "user:{login} is:open"),
    ("review", "review-requested:{login} is:open"),
    ("assigned", "assignee:{login} is:open"),
]

# Everything search can report without paying for a pull request's internals.
LIGHT_FIELDS = """
  __typename
  ... on PullRequest {
    id number title url state isDraft createdAt updatedAt reviewDecision
    merged mergedAt closedAt
    # Who cast the deciding review. The full review list is part of the
    # expensive detail pass and is skipped entirely for closed pull requests,
    # so an approval landing minutes before a merge had no name attached.
    latestOpinionatedReviews(last: 3) { nodes { author { login } state } }
    author { login } repository { nameWithOwner isPrivate }
    labels(first:10) { nodes { name color } }
    comments(last:1) { totalCount nodes { author { login } createdAt } }
    assignees(first:5) { nodes { login } }
  }
  ... on Issue {
    id number title url state createdAt updatedAt closedAt stateReason
    author { login } repository { nameWithOwner isPrivate }
    labels(first:10) { nodes { name color } }
    comments(last:1) { totalCount nodes { author { login } createdAt } }
    assignees(first:5) { nodes { login } }
  }
"""

# The expensive half: threads and CI, fetched per pull request only on change.
PR_DETAIL = """
fragment prDetail on PullRequest {
  number updatedAt
  repository { nameWithOwner }
  reviews(last:30) { nodes { author { login } state submittedAt url } }
  reviewRequests(first:20) { nodes { requestedReviewer {
    __typename ... on User { login } ... on Team { name } } } }
  reviewThreads(first:100) {
    totalCount
    nodes {
      id isResolved isOutdated path line
      opener: comments(first:1) { nodes { author { login } bodyText createdAt url } }
      latest: comments(last:1) { totalCount nodes { author { login } createdAt } }
    }
  }
  commits(last:1) { nodes { commit {
    committedDate
    statusCheckRollup { state contexts(first:100) { totalCount nodes {
      __typename
      ... on CheckRun { name conclusion status detailsUrl }
      ... on StatusContext { context state targetUrl } } } } } } }
}
"""

DISCUSSION_FIELDS = """
  number title url createdAt updatedAt isAnswered
  author { login } category { name emoji }
  repository { nameWithOwner }
"""

# A discussion's conversation is nested: `comments` are only the top-level
# ones, and every reply under them is invisible to a query that stops there.
# One thread here had 7 comments and 59 replies, so "who spoke last" read as a
# message from weeks earlier and the dashboard called it quiet.
#
# Replies are taken per top-level comment rather than from the last few
# comments, because GitHub orders comments by when they were *started*: an
# answer posted today under the first question does not move that question to
# the end of the list.
DISCUSSION_THREAD = """
  number
  repository { nameWithOwner }
  comments(first:50) {
    totalCount
    nodes {
      author { login } createdAt bodyText url
      replies(last:20) {
        totalCount
        nodes { author { login } createdAt bodyText url }
      }
    }
  }
"""

# Reasons worth a place on a dashboard. `ci_activity` is deliberately absent:
# on a busy upstream it is 200 of 280 notifications and drowns everything that
# a person actually has to answer. Build status still reaches the dashboard,
# but from the pull request itself, where it belongs.
USEFUL_REASONS = {
    "assign", "author", "comment", "manual", "mention", "review_requested",
    "security_alert", "state_change", "subscribed", "team_mention",
}


def ts(value: str | None) -> float:
    """GitHub's ISO-8601 into a unix timestamp; 0 when absent."""
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _search_query(names: list[str]) -> str:
    parts = [f'  {n}: search(query: ${n}, type: ISSUE, first: 100) {{ '
             f'issueCount nodes {{ {LIGHT_FIELDS} }} }}' for n in names]
    args = ", ".join(f"${n}: String!" for n in names)
    return ("query(" + args + ") {\n" + "\n".join(parts) +
            "\n  rateLimit { cost remaining limit resetAt }\n}")


def search_items(client, login: str, extra: list[str] | None = None,
                 done_days: int = 7) -> dict:
    """Everything that touches `login`: open, plus recently closed.

    Returns {id: item}. The union is by GraphQL node id, and each item records
    *which* searches found it — that is what later tells an authored pull
    request from one merely commented on.

    Recently closed matters for two reasons. It is the answer to "what did I
    get done", which an open-only board can never show — three pull requests
    merged on Thursday simply vanish. And it is where most of the homeless
    notifications were pointing: a notice about a merged pull request had no
    card to attach to, so it landed in a pile labelled "no card".
    """
    names = [name for name, _ in SEARCHES]
    variables = {name: tmpl.format(login=login) for name, tmpl in SEARCHES}
    if done_days > 0:
        since = _date_days_ago(done_days)
        names.append("done")
        variables["done"] = f"involves:{login} is:closed closed:>={since}"
    for i, query in enumerate(extra or []):
        key = f"extra{i}"
        names.append(key)
        variables[key] = query

    data = client.graphql(_search_query(names), variables)
    items: dict[str, dict] = {}
    for name in names:
        block = data.get(name) or {}
        for node in block.get("nodes") or []:
            if not node:
                continue
            item = _light_item(node, login)
            if not item:
                continue
            found = items.setdefault(item["id"], item)
            found["found_by"].add(name)
    for item in items.values():
        item["found_by"] = sorted(item["found_by"])
    return items


def _date_days_ago(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def _light_item(node: dict, login: str) -> dict | None:
    kind = {"PullRequest": "pr", "Issue": "issue"}.get(node.get("__typename"))
    if not kind:
        return None
    repo = (node.get("repository") or {}).get("nameWithOwner") or "?"
    author = (node.get("author") or {}).get("login") or "ghost"
    comments = node.get("comments") or {}
    last = (comments.get("nodes") or [None])[0]
    # GitHub reports a merged pull request as state MERGED, but a closed issue
    # and an abandoned pull request are both CLOSED — and those are different
    # outcomes worth different words on the card.
    state = (node.get("state") or "OPEN").upper()
    return {
        "id": f"{repo}#{node['number']}",
        "node_id": node.get("id"),
        "kind": kind,
        "repo": repo,
        "private": bool((node.get("repository") or {}).get("isPrivate")),
        "number": node["number"],
        "title": node.get("title") or "",
        "url": node.get("url") or "",
        "state": state,
        "merged": bool(node.get("merged")),
        "closed_at": ts(node.get("mergedAt") or node.get("closedAt")),
        "close_reason": node.get("stateReason"),
        "draft": bool(node.get("isDraft")),
        "author": author,
        "mine": author == login,
        "created": ts(node.get("createdAt")),
        "updated": ts(node.get("updatedAt")),
        "labels": [{"name": lab["name"], "color": lab.get("color", "")}
                   for lab in (node.get("labels") or {}).get("nodes") or []],
        "assignees": [a["login"] for a in (node.get("assignees") or {}).get("nodes") or []],
        "comments": comments.get("totalCount", 0),
        "last_comment": ({"login": (last.get("author") or {}).get("login") or "ghost",
                          "at": ts(last.get("createdAt"))} if last else None),
        "review_decision": node.get("reviewDecision"),
        "deciders": [{"login": (r.get("author") or {}).get("login") or "?",
                      "state": r.get("state")}
                     for r in (node.get("latestOpinionatedReviews") or {}).get("nodes") or []],
        "found_by": set(),
        # Filled in by the detail pass; absent means "not fetched yet".
        "threads": None,
        "ci": None,
        "reviews": [],
        "review_requests": [],
        "notifications": [],
    }


def pr_details(client, keys: list[tuple[str, int]], batch: int = 15) -> dict:
    """Threads, reviews and CI for the given pull requests.

    `keys` are (repo, number). Batched with aliases because one round trip for
    fifteen pull requests beats fifteen round trips; the billed cost is the
    same either way, but the wall clock is not.
    """
    out: dict[str, dict] = {}
    for start in range(0, len(keys), batch):
        chunk = keys[start:start + batch]
        aliases = []
        for i, (repo, number) in enumerate(chunk):
            owner, _, name = repo.partition("/")
            aliases.append(f'  p{i}: repository(owner:"{owner}", name:"{name}") '
                           f'{{ pullRequest(number:{number}) {{ ...prDetail }} }}')
        query = ("query {\n" + "\n".join(aliases) +
                 "\n  rateLimit { cost remaining limit resetAt }\n}\n" + PR_DETAIL)
        data = client.graphql(query)
        for key, value in data.items():
            if key in ("rateLimit", "_errors") or not value:
                continue
            pr = value.get("pullRequest")
            if not pr:
                continue
            repo = (pr.get("repository") or {}).get("nameWithOwner") or "?"
            out[f"{repo}#{pr['number']}"] = _detail(pr)
    return out


def _detail(pr: dict) -> dict:
    threads = []
    for node in (pr.get("reviewThreads") or {}).get("nodes") or []:
        opener = ((node.get("opener") or {}).get("nodes") or [None])[0]
        latest = ((node.get("latest") or {}).get("nodes") or [None])[0]
        threads.append({
            "id": node.get("id"),
            "resolved": bool(node.get("isResolved")),
            "outdated": bool(node.get("isOutdated")),
            "path": node.get("path") or "",
            "line": node.get("line"),
            "url": (opener or {}).get("url") or "",
            "excerpt": ((opener or {}).get("bodyText") or "")[:280],
            "opened_by": ((opener or {}).get("author") or {}).get("login") or "ghost",
            "opened_at": ts((opener or {}).get("createdAt")),
            "last_by": ((latest or {}).get("author") or {}).get("login") or "ghost",
            "last_at": ts((latest or {}).get("createdAt")),
            "count": (node.get("latest") or {}).get("totalCount", 0),
        })

    reviews = [{
        "login": (r.get("author") or {}).get("login") or "ghost",
        "state": r.get("state"),
        "at": ts(r.get("submittedAt")),
        "url": r.get("url") or "",
    } for r in (pr.get("reviews") or {}).get("nodes") or []]

    requested = []
    for node in (pr.get("reviewRequests") or {}).get("nodes") or []:
        who = node.get("requestedReviewer") or {}
        name = who.get("login") or who.get("name")
        if name:
            requested.append(name)

    commit = ((pr.get("commits") or {}).get("nodes") or [{}])[0].get("commit") or {}
    return {
        "threads": threads,
        "reviews": reviews,
        "review_requests": requested,
        "head_at": ts(commit.get("committedDate")),
        "ci": _ci(commit.get("statusCheckRollup")),
    }


def _ci(rollup: dict | None) -> dict:
    """Flatten a check rollup into "what is red, and how much is still running".

    Both check runs and old-style status contexts appear in the same list with
    different shapes, and a check that is still queued has no conclusion at
    all — reading `conclusion` alone silently counts a running build as passed.
    """
    if not rollup:
        return {"state": None, "failed": [], "pending": 0, "total": 0}
    failed, pending, total = [], 0, 0
    for node in (rollup.get("contexts") or {}).get("nodes") or []:
        total += 1
        if node.get("__typename") == "CheckRun":
            name = node.get("name") or "check"
            status, conclusion = node.get("status"), node.get("conclusion")
            if status in ("QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"):
                pending += 1
            elif conclusion in ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"):
                failed.append({"name": name, "url": node.get("detailsUrl") or ""})
        else:
            name = node.get("context") or "status"
            state = node.get("state")
            if state == "PENDING":
                pending += 1
            elif state in ("FAILURE", "ERROR"):
                failed.append({"name": name, "url": node.get("targetUrl") or ""})
    return {"state": rollup.get("state"), "failed": failed,
            "pending": pending, "total": total}


def discussions(client, login: str, repos: list[str]) -> dict:
    """Discussions you are in, plus everything on your own repositories.

    Two sources because neither is complete: the search finds threads you have
    written in anywhere on GitHub, and the per-repository listing finds the
    ones on your projects that nobody has answered — which is the half that
    matters and the half search cannot see.
    """
    parts = [f'  involved: search(query: "involves:{login}", type: DISCUSSION, first: 50) '
             f'{{ discussionCount nodes {{ ... on Discussion {{ {DISCUSSION_FIELDS} }} }} }}']
    for i, repo in enumerate(repos):
        owner, _, name = repo.partition("/")
        parts.append(
            f'  r{i}: repository(owner:"{owner}", name:"{name}") {{ '
            f'discussions(first:30, orderBy:{{field: UPDATED_AT, direction: DESC}}) '
            f'{{ nodes {{ {DISCUSSION_FIELDS} }} }} }}')
    query = "query {\n" + "\n".join(parts) + "\n  rateLimit { cost remaining limit resetAt }\n}"

    data = client.graphql(query)
    nodes = list((data.get("involved") or {}).get("nodes") or [])
    for key, value in data.items():
        if not key.startswith("r") or not value or key == "rateLimit":
            continue
        nodes.extend((value.get("discussions") or {}).get("nodes") or [])

    out: dict[str, dict] = {}
    for node in nodes:
        if not node:
            continue
        repo = (node.get("repository") or {}).get("nameWithOwner") or "?"
        author = (node.get("author") or {}).get("login") or "ghost"
        key = f"{repo}#d{node['number']}"
        out[key] = {
            "id": key,
            "kind": "discussion",
            "repo": repo,
            "private": False,
            "number": node["number"],
            "title": node.get("title") or "",
            "url": node.get("url") or "",
            "state": "OPEN",
            "merged": False,
            "closed_at": 0.0,
            "close_reason": None,
            "draft": False,
            "author": author,
            "mine": author == login,
            "created": ts(node.get("createdAt")),
            "updated": ts(node.get("updatedAt")),
            "answered": bool(node.get("isAnswered")),
            "category": (node.get("category") or {}).get("name") or "",
            "labels": [],
            "assignees": [],
            "comments": 0,
            "last_comment": None,
            # Filled by the thread pass; None means "not fetched yet", which is
            # not the same as "nobody has written anything".
            "participants": None,
            "review_decision": None,
            "deciders": [],
            "found_by": ["discussion"],
            "threads": None,
            "ci": None,
            "reviews": [],
            "review_requests": [],
            "notifications": [],
        }
    return out


def discussion_threads(client, keys: list[tuple[str, int]], batch: int = 8) -> dict:
    """Every message in the given discussions, replies included.

    `keys` are (repo, number). Batched small because each discussion asks for
    up to 50 comments with 20 replies apiece, and GraphQL bills for the lot.
    """
    out: dict[str, dict] = {}
    for start in range(0, len(keys), batch):
        chunk = keys[start:start + batch]
        aliases = []
        for i, (repo, number) in enumerate(chunk):
            owner, _, name = repo.partition("/")
            aliases.append(f'  d{i}: repository(owner:"{owner}", name:"{name}") '
                           f'{{ discussion(number:{number}) {{ ...discThread }} }}')
        query = ("query {\n" + "\n".join(aliases) +
                 "\n  rateLimit { cost remaining limit resetAt }\n}\n"
                 "fragment discThread on Discussion {" + DISCUSSION_THREAD + "}")
        data = client.graphql(query)
        for alias, value in data.items():
            if alias in ("rateLimit", "_errors") or not value:
                continue
            node = value.get("discussion")
            if not node:
                continue
            repo = (node.get("repository") or {}).get("nameWithOwner") or "?"
            out[f"{repo}#d{node['number']}"] = _conversation(node)
    return out


def _conversation(node: dict) -> dict:
    """Flatten a nested discussion into "who said what, when"."""
    block = node.get("comments") or {}
    messages: list[dict] = []
    hidden = 0
    for comment in block.get("nodes") or []:
        if not comment:
            continue
        messages.append(_message(comment, reply=False))
        replies = comment.get("replies") or {}
        shown = [r for r in replies.get("nodes") or [] if r]
        # A branch longer than the page asked for: say so rather than quietly
        # reporting a stale "last spoke".
        hidden += max(0, replies.get("totalCount", 0) - len(shown))
        for reply in shown:
            messages.append(_message(reply, reply=True))

    messages.sort(key=lambda m: m["at"])
    last = messages[-1] if messages else None
    return {
        "comments": len(messages) + hidden,
        "participants": sorted({m["login"] for m in messages}),
        "last_comment": ({"login": last["login"], "at": last["at"],
                          "excerpt": last["excerpt"], "url": last["url"],
                          "reply": last["reply"]} if last else None),
        "messages_truncated": hidden,
    }


def _message(node: dict, reply: bool) -> dict:
    return {
        "login": (node.get("author") or {}).get("login") or "ghost",
        "at": ts(node.get("createdAt")),
        "excerpt": (node.get("bodyText") or "")[:280],
        "url": node.get("url") or "",
        "reply": reply,
    }


def own_repos_with_discussions(client) -> list[str]:
    """Your repositories that have Discussions turned on.

    Kept out of the config on purpose: enabling Discussions on a repository is
    a two-click action and nobody remembers to also edit a JSON file on a
    server, so the one place that knows is GitHub.
    """
    query = """
    query($cursor: String) {
      viewer {
        repositories(first: 100, after: $cursor, ownerAffiliations: OWNER,
                     orderBy: {field: PUSHED_AT, direction: DESC}) {
          pageInfo { hasNextPage endCursor }
          nodes { nameWithOwner hasDiscussionsEnabled isArchived }
        }
      }
      rateLimit { cost remaining limit resetAt }
    }
    """
    out, cursor = [], None
    for _ in range(5):
        data = client.graphql(query, {"cursor": cursor})
        block = ((data.get("viewer") or {}).get("repositories") or {})
        for node in block.get("nodes") or []:
            if node and node.get("hasDiscussionsEnabled") and not node.get("isArchived"):
                out.append(node["nameWithOwner"])
        page = block.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return out


def own_repos_watch(client) -> dict:
    """Forks and stars across every repository you own, in one query.

    Neither is something GitHub tells you about: a fork produces no
    notification at all, and a star produces one only if you have web
    notifications turned on and happen to be looking. You find out months
    later, by accident, that somebody took your project.

    Both come from the same repository list, so they cost one point together.
    Forks and stargazers are ordered newest-last, so a repository with more of
    either than the page holds still cannot hide a new one behind old ones.
    """
    query = """
    query($cursor: String) {
      viewer {
        repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isFork: false) {
          pageInfo { hasNextPage endCursor }
          nodes {
            nameWithOwner url forkCount stargazerCount
            forks(first: 50, orderBy: {field: CREATED_AT, direction: DESC}) {
              nodes {
                nameWithOwner url createdAt stargazerCount
                owner { login }
              }
            }
            stargazers(last: 10, orderBy: {field: STARRED_AT, direction: ASC}) {
              edges { starredAt node { login url } }
            }
          }
        }
      }
      rateLimit { cost remaining limit resetAt }
    }
    """
    forks: dict[str, dict] = {}
    stars: dict[str, dict] = {}
    # GitHub's forkCount and its list of forks disagree, often badly: one
    # repository here reports 15 forks and lists 4. The missing ones belong to
    # deleted or suspended accounts — counted for ever, never listed, and the
    # same through REST. New forks still appear in the list, so detection is
    # unaffected; the count is reported separately so the dashboard does not
    # look like it lost eleven of them.
    claimed = 0
    cursor = None
    for _ in range(5):
        data = client.graphql(query, {"cursor": cursor})
        block = ((data.get("viewer") or {}).get("repositories") or {})
        for repo in block.get("nodes") or []:
            if not repo:
                continue
            name = repo["nameWithOwner"]
            claimed += repo.get("forkCount") or 0
            for fork in (repo.get("forks") or {}).get("nodes") or []:
                if not fork:
                    continue
                forks[fork["nameWithOwner"]] = {
                    "name": fork["nameWithOwner"],
                    "parent": name,
                    "url": fork.get("url") or "",
                    "owner": (fork.get("owner") or {}).get("login") or "?",
                    "created": ts(fork.get("createdAt")),
                    "stars": fork.get("stargazerCount") or 0,
                }
            stars[name] = {
                "repo": name,
                "url": repo.get("url") or "",
                "count": repo.get("stargazerCount") or 0,
                # Newest last, so the tail is who arrived most recently.
                "recent": [{"login": (e.get("node") or {}).get("login") or "?",
                            "at": ts(e.get("starredAt"))}
                           for e in (repo.get("stargazers") or {}).get("edges") or [] if e],
            }
        page = block.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return {"forks": forks, "stars": stars, "forks_claimed": claimed,
            "total_stars": sum(r["count"] for r in stars.values())}


def notifications(client, include_ci: bool = False) -> list[dict]:
    """The unread inbox, minus the noise.

    Conditional: an unchanged inbox answers 304 and costs nothing, which is
    what makes a short poll interval affordable.
    """
    payload, changed = client.rest("/notifications", conditional=True,
                                   params={"per_page": "100"})
    if not changed or not isinstance(payload, list):
        return []
    out = []
    for note in payload:
        reason = note.get("reason") or ""
        subject = note.get("subject") or {}
        kind = subject.get("type") or ""
        if not include_ci and (reason == "ci_activity" or kind == "CheckSuite"):
            continue
        if reason not in USEFUL_REASONS and not include_ci:
            continue
        out.append({
            "id": note.get("id"),
            "repo": (note.get("repository") or {}).get("full_name") or "?",
            "kind": kind,
            "title": subject.get("title") or "",
            "reason": reason,
            "unread": bool(note.get("unread")),
            "updated": ts(note.get("updated_at")),
            "api_url": subject.get("url") or "",
            "number": _number_from(subject.get("url") or ""),
        })
    return out


def _number_from(api_url: str) -> int | None:
    """`.../repos/o/r/issues/42` → 42, so a notification can find its item."""
    tail = api_url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def attach_notifications(items: dict, notes: list[dict]) -> list[dict]:
    """Hang each notification on its item; return the ones with no home.

    A notification about a closed pull request, or about a repository the
    searches do not cover, has nowhere to go — and rather than dropping it
    (the thing that loses a mention), it comes back for its own section.
    """
    orphans = []
    for note in notes:
        if note["number"] is None:
            orphans.append(note)
            continue
        key = f"{note['repo']}#{note['number']}"
        target = items.get(key) or items.get(f"{note['repo']}#d{note['number']}")
        if target is None:
            orphans.append(note)
            continue
        target["notifications"].append(note)
    return orphans


def collect(client, login: str, cfg: dict, cache: dict) -> dict:
    """One full pass. `cache` carries detail from the previous pass forward."""
    started = time.time()
    warnings: list[str] = []

    items = search_items(client, login, cfg.get("extra_searches") or [],
                         int(cfg.get("done_days", 7)))

    # Which repositories have Discussions changes about once a year, so it is
    # looked up hourly rather than every poll.
    repos = cfg.get("discussion_repos") or []
    if not repos:
        if cache.get("_repos_at", 0) < started - 3600:
            try:
                cache["_repos"] = own_repos_with_discussions(client)
                cache["_repos_at"] = started
            except Exception as err:  # noqa: BLE001
                warnings.append(f"список репозиториев не обновлён: {err}")
        repos = cache.get("_repos") or []

    try:
        items.update(discussions(client, login, repos))
    except Exception as err:  # noqa: BLE001
        warnings.append(f"обсуждения не получены: {err}")

    # The point of the cache: a pull request whose updatedAt has not moved
    # cannot have grown a new thread, so its expensive half is reused. On a
    # steady day this turns a ~120-point poll into a ~5-point one.
    stale: list[tuple[str, int]] = []
    stale_disc: list[tuple[str, int]] = []
    for key, item in items.items():
        if item["kind"] not in ("pr", "discussion"):
            continue
        # A merged pull request has no move left in it, so its threads and CI
        # are not worth two points each — and there are more merged ones than
        # open ones.
        if item["state"] != "OPEN":
            item["threads"] = []
            continue
        old = cache.get(key)
        if old and old.get("updated") == item["updated"] and old.get("detail"):
            item.update(old["detail"])
            continue
        (stale if item["kind"] == "pr" else stale_disc).append(
            (item["repo"], item["number"]))

    if stale:
        try:
            details = pr_details(client, stale, cfg.get("detail_batch", 15))
        except Exception as err:  # noqa: BLE001 — a failed detail pass must not empty the board
            details = {}
            warnings.append(f"детали PR не получены: {err}")
        for key, detail in details.items():
            if key in items:
                items[key].update(detail)
                cache[key] = {"updated": items[key]["updated"], "detail": detail}

    if stale_disc:
        try:
            convos = discussion_threads(client, stale_disc,
                                        cfg.get("discussion_batch", 8))
        except Exception as err:  # noqa: BLE001
            convos = {}
            warnings.append(f"обсуждения не раскрыты: {err}")
        for key, convo in convos.items():
            if key in items:
                items[key].update(convo)
                cache[key] = {"updated": items[key]["updated"], "detail": convo}

    # Details that never arrived would silently look like "no threads" and
    # "nobody has written" — a calm board built on missing data. Say so.
    for item in items.values():
        if item["kind"] == "pr" and item["threads"] is None:
            item["incomplete"] = True
        elif item["kind"] == "discussion" and item.get("participants") is None:
            item["incomplete"] = True
            item["participants"] = []

    watch: dict = {}
    if cfg.get("watch_forks", True) or cfg.get("watch_stars", True):
        try:
            watch = own_repos_watch(client)
        except Exception as err:  # noqa: BLE001 — nobody loses the board over this
            warnings.append(f"форки и звёзды не проверены: {err}")

    try:
        notes = notifications(client, cfg.get("notifications_include_ci", False))
    except Exception as err:  # noqa: BLE001
        notes, warnings = [], warnings + [f"уведомления не получены: {err}"]
    orphans = attach_notifications(items, notes)

    # Drop cached detail for anything no longer open, but leave the cache's own
    # bookkeeping keys alone — they are not items and must survive the sweep.
    for key in list(cache):
        if not key.startswith("_") and key not in items:
            del cache[key]

    return {
        "items": items,
        "orphan_notifications": orphans,
        "notification_count": len(notes),
        "forks": watch.get("forks") or {},
        "forks_claimed": watch.get("forks_claimed") or 0,
        "stars": watch.get("stars") or {},
        "total_stars": watch.get("total_stars") or 0,
        "warnings": warnings,
        "took": time.time() - started,
    }
