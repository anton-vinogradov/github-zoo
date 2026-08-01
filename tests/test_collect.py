"""Parsing what GitHub actually sends back.

The shapes here are copied from real API replies. Two of them are the reason
this file exists: a check that is still queued has no `conclusion` at all, and
old-style status contexts sit in the same list as check runs under different
field names.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

import collect  # noqa: E402


# ---------- timestamps ----------

def test_iso_to_unix():
    assert collect.ts("2026-07-31T17:55:28Z") == 1785520528.0


def test_missing_timestamp_is_zero_not_an_error():
    assert collect.ts(None) == 0.0
    assert collect.ts("") == 0.0
    assert collect.ts("not a date") == 0.0


# ---------- CI rollup ----------

def test_no_rollup_is_not_a_failure():
    got = collect._ci(None)
    assert got == {"state": None, "failed": [], "pending": 0, "total": 0}


def test_queued_check_counts_as_pending_not_passed():
    """A queued run has conclusion=None; reading it alone calls that success."""
    got = collect._ci({"state": "PENDING", "contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "build", "status": "QUEUED",
         "conclusion": None, "detailsUrl": "u"},
        {"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS",
         "conclusion": None, "detailsUrl": "u"},
    ]}})
    assert got["pending"] == 2
    assert got["failed"] == []
    assert got["total"] == 2


def test_failed_check_is_named_with_its_link():
    got = collect._ci({"state": "FAILURE", "contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "Rolling Upgrade", "status": "COMPLETED",
         "conclusion": "FAILURE", "detailsUrl": "https://ci/1"},
        {"__typename": "CheckRun", "name": "ok", "status": "COMPLETED",
         "conclusion": "SUCCESS", "detailsUrl": ""},
    ]}})
    assert got["failed"] == [{"name": "Rolling Upgrade", "url": "https://ci/1"}]
    assert got["total"] == 2


def test_old_style_status_contexts_are_understood():
    got = collect._ci({"state": "FAILURE", "contexts": {"nodes": [
        {"__typename": "StatusContext", "context": "teamcity", "state": "FAILURE",
         "targetUrl": "https://tc/1"},
        {"__typename": "StatusContext", "context": "other", "state": "PENDING",
         "targetUrl": ""},
    ]}})
    assert got["failed"] == [{"name": "teamcity", "url": "https://tc/1"}]
    assert got["pending"] == 1


def test_neutral_and_skipped_are_not_failures():
    got = collect._ci({"state": "SUCCESS", "contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "a", "status": "COMPLETED",
         "conclusion": "NEUTRAL", "detailsUrl": ""},
        {"__typename": "CheckRun", "name": "b", "status": "COMPLETED",
         "conclusion": "SKIPPED", "detailsUrl": ""},
    ]}})
    assert got["failed"] == []
    assert got["pending"] == 0


def test_timed_out_and_action_required_are_failures():
    got = collect._ci({"state": "FAILURE", "contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "a", "status": "COMPLETED",
         "conclusion": "TIMED_OUT", "detailsUrl": ""},
        {"__typename": "CheckRun", "name": "b", "status": "COMPLETED",
         "conclusion": "ACTION_REQUIRED", "detailsUrl": ""},
    ]}})
    assert [f["name"] for f in got["failed"]] == ["a", "b"]


# ---------- notifications ----------

def test_number_is_taken_from_the_api_url():
    assert collect._number_from("https://api.github.com/repos/o/r/issues/42") == 42
    assert collect._number_from("https://api.github.com/repos/o/r/pulls/7") == 7


def test_a_url_without_a_number_gives_none():
    """CheckSuite notifications have url=null, and str(None) is not a number."""
    assert collect._number_from("") is None
    assert collect._number_from("https://api.github.com/repos/o/r") is None


def test_notifications_attach_to_their_item():
    items = {"o/r#42": {"notifications": []}}
    orphans = collect.attach_notifications(
        items, [{"repo": "o/r", "number": 42, "reason": "comment"}])
    assert orphans == []
    assert len(items["o/r#42"]["notifications"]) == 1


def test_a_notification_about_a_discussion_finds_it():
    items = {"o/r#d3": {"notifications": []}}
    orphans = collect.attach_notifications(
        items, [{"repo": "o/r", "number": 3, "reason": "comment"}])
    assert orphans == []
    assert len(items["o/r#d3"]["notifications"]) == 1


def test_a_notification_with_no_item_is_returned_not_dropped():
    """Usually a merged pull request. Dropping it loses the mention."""
    orphans = collect.attach_notifications(
        {}, [{"repo": "o/r", "number": 99, "reason": "mention"}])
    assert len(orphans) == 1


# ---------- light items ----------

def test_pull_request_from_search():
    node = {
        "__typename": "PullRequest", "id": "PR_1", "number": 13422,
        "title": "fix", "url": "https://github.com/apache/ignite/pull/13422",
        "state": "OPEN", "isDraft": False,
        "createdAt": "2026-07-30T10:00:00Z", "updatedAt": "2026-07-31T17:55:28Z",
        "reviewDecision": "APPROVED",
        "author": {"login": "anton-vinogradov"},
        "repository": {"nameWithOwner": "apache/ignite", "isPrivate": False},
        "labels": {"nodes": [{"name": "ise", "color": "ff0000"}]},
        "comments": {"totalCount": 3, "nodes": [
            {"author": {"login": "reviewer"}, "createdAt": "2026-07-31T17:00:00Z"}]},
        "assignees": {"nodes": []},
    }
    got = collect._light_item(node, "anton-vinogradov")
    assert got["id"] == "apache/ignite#13422"
    assert got["kind"] == "pr"
    assert got["mine"] is True
    assert got["labels"] == [{"name": "ise", "color": "ff0000"}]
    assert got["last_comment"]["login"] == "reviewer"
    assert got["review_decision"] == "APPROVED"
    assert got["threads"] is None  # not fetched yet, and must not read as empty


def test_a_deleted_author_does_not_crash():
    node = {
        "__typename": "Issue", "id": "I_1", "number": 5, "title": "t", "url": "",
        "state": "OPEN", "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z", "author": None,
        "repository": {"nameWithOwner": "o/r", "isPrivate": False},
        "labels": {"nodes": []},
        "comments": {"totalCount": 0, "nodes": []},
        "assignees": {"nodes": []},
    }
    got = collect._light_item(node, "me")
    assert got["author"] == "ghost"
    assert got["mine"] is False


def test_unknown_node_types_are_skipped():
    assert collect._light_item({"__typename": "Repository"}, "me") is None


# ---------- pull request detail ----------

def test_detail_flattens_threads_and_reviews():
    pr = {
        "number": 1, "repository": {"nameWithOwner": "o/r"},
        "reviews": {"nodes": [
            {"author": {"login": "r"}, "state": "APPROVED",
             "submittedAt": "2026-07-01T00:00:00Z", "url": "u"}]},
        "reviewRequests": {"nodes": [
            {"requestedReviewer": {"__typename": "User", "login": "someone"}},
            {"requestedReviewer": {"__typename": "Team", "name": "core"}},
            {"requestedReviewer": None}]},
        "reviewThreads": {"totalCount": 1, "nodes": [{
            "id": "T1", "isResolved": False, "isOutdated": True,
            "path": "a.java", "line": 10,
            "opener": {"nodes": [{"author": {"login": "r"}, "bodyText": "why?",
                                  "createdAt": "2026-07-01T00:00:00Z", "url": "tu"}]},
            "latest": {"totalCount": 2, "nodes": [
                {"author": {"login": "me"}, "createdAt": "2026-07-02T00:00:00Z"}]},
        }]},
        "commits": {"nodes": [{"commit": {
            "committedDate": "2026-07-03T00:00:00Z",
            "statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": []}}}}]},
    }
    got = collect._detail(pr)
    assert got["review_requests"] == ["someone", "core"]
    assert got["reviews"][0]["login"] == "r"
    thread = got["threads"][0]
    assert (thread["opened_by"], thread["last_by"], thread["count"]) == ("r", "me", 2)
    assert thread["outdated"] is True
    assert thread["excerpt"] == "why?"
    assert got["head_at"] == collect.ts("2026-07-03T00:00:00Z")


# ---------- discussions ----------

def comment(login, at, body="", replies=None, total=None):
    kids = replies or []
    return {"author": {"login": login}, "createdAt": at, "bodyText": body, "url": "u",
            "replies": {"totalCount": total if total is not None else len(kids),
                        "nodes": kids}}


def test_the_last_word_can_be_inside_a_branch():
    """The bug this whole pass exists for.

    Top-level comments are ordered by when each was started, so an answer
    posted today under June's question does not move it to the end. Reading
    only `comments(last:1)` reported June and called the thread quiet.
    """
    got = collect._conversation({"comments": {"totalCount": 2, "nodes": [
        comment("me", "2026-06-01T00:00:00Z", "вопрос", replies=[
            {"author": {"login": "stranger"}, "createdAt": "2026-07-31T00:00:00Z",
             "bodyText": "вот ответ", "url": "r"}]),
        comment("me", "2026-06-15T00:00:00Z", "ещё мысль"),
    ]}})
    assert got["last_comment"]["login"] == "stranger"
    assert got["last_comment"]["reply"] is True
    assert got["last_comment"]["excerpt"] == "вот ответ"
    assert got["comments"] == 3


def test_participants_include_everyone_from_both_levels():
    got = collect._conversation({"comments": {"totalCount": 1, "nodes": [
        comment("alice", "2026-01-01T00:00:00Z", replies=[
            {"author": {"login": "bob"}, "createdAt": "2026-01-02T00:00:00Z",
             "bodyText": "", "url": ""},
            {"author": {"login": "me"}, "createdAt": "2026-01-03T00:00:00Z",
             "bodyText": "", "url": ""}]),
    ]}})
    assert got["participants"] == ["alice", "bob", "me"]


def test_a_branch_longer_than_the_page_is_admitted():
    """Twenty replies fetched out of fifty means "last spoke" may be wrong."""
    got = collect._conversation({"comments": {"totalCount": 1, "nodes": [
        comment("alice", "2026-01-01T00:00:00Z", replies=[
            {"author": {"login": "bob"}, "createdAt": "2026-01-02T00:00:00Z",
             "bodyText": "", "url": ""}], total=50),
    ]}})
    assert got["messages_truncated"] == 49
    assert got["comments"] == 51


def test_an_empty_discussion_has_no_last_word():
    got = collect._conversation({"comments": {"totalCount": 0, "nodes": []}})
    assert got["last_comment"] is None
    assert got["participants"] == []
    assert got["comments"] == 0


def test_a_deleted_author_in_a_reply_does_not_crash():
    got = collect._conversation({"comments": {"totalCount": 1, "nodes": [
        comment("alice", "2026-01-01T00:00:00Z", replies=[
            {"author": None, "createdAt": "2026-01-02T00:00:00Z",
             "bodyText": "", "url": ""}]),
    ]}})
    assert got["last_comment"]["login"] == "ghost"


def test_a_pull_request_with_no_commits_does_not_crash():
    got = collect._detail({"number": 1, "repository": {"nameWithOwner": "o/r"},
                           "reviews": {"nodes": []}, "reviewRequests": {"nodes": []},
                           "reviewThreads": {"nodes": []}, "commits": {"nodes": []}})
    assert got["head_at"] == 0.0
    assert got["ci"]["state"] is None
