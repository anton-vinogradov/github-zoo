"""What "whose move is it" must mean.

Every case here came from a real item on the board that the rules got wrong at
some point, so these are regressions, not illustrations.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

import rules  # noqa: E402

ME = "anton-vinogradov"
CFG = {"stale_days": 14, "rot_days": 30, "unreviewed_days": 3, "ignore_days": 30}
NOW = 1_785_600_000.0
DAY = 86400.0


def item(**over):
    base = {
        "id": "acme/thing#1", "kind": "pr", "repo": "acme/thing", "number": 1,
        "title": "t", "url": "", "state": "OPEN", "draft": False,
        "author": "someone", "mine": False, "private": False,
        "created": NOW - 5 * DAY, "updated": NOW - 1 * DAY,
        "labels": [], "assignees": [], "comments": 0, "last_comment": None,
        "review_decision": None, "found_by": [], "threads": [], "ci": None,
        "reviews": [], "review_requests": [], "notifications": [],
        "head_at": NOW - 2 * DAY,
    }
    base.update(over)
    return base


def thread(**over):
    base = {"id": "t1", "resolved": False, "outdated": False, "path": "a.java",
            "line": 1, "url": "", "excerpt": "", "opened_by": "reviewer",
            "opened_at": NOW - 3 * DAY, "last_by": "reviewer",
            "last_at": NOW - 3 * DAY, "count": 1}
    base.update(over)
    return base


def verdict(it):
    return rules.classify(it, ME, CFG, NOW)


def codes(v):
    return {r["code"] for r in v["reasons"]}


# ---------- your own pull requests ----------

def test_unanswered_thread_is_your_move():
    v = verdict(item(mine=True, author=ME, threads=[thread()]))
    assert v["turn"] == rules.YOU
    assert "threads" in codes(v)


def reviewed_pr(**over):
    """A pull request of yours that already has a reviewer.

    Without one, the "nobody is looking at this" rule fires and every thread
    test would pass for the wrong reason.
    """
    base = {"mine": True, "author": ME, "review_requests": ["reviewer"]}
    base.update(over)
    return item(**base)


def test_thread_you_answered_last_is_not_your_move():
    v = verdict(reviewed_pr(threads=[thread(last_by=ME)]))
    assert v["turn"] == rules.THEM


def test_outdated_thread_does_not_count():
    """A thread whose lines were rewritten is stale by construction.

    Counting these put six permanently unanswerable items in "your move".
    """
    v = verdict(reviewed_pr(threads=[thread(outdated=True)]))
    assert v["turn"] == rules.THEM


def test_resolved_thread_does_not_count():
    v = verdict(reviewed_pr(threads=[thread(resolved=True)]))
    assert v["turn"] == rules.THEM


def test_a_thread_between_other_people_is_not_yours():
    """On someone else's pull request, their argument is not your queue."""
    v = verdict(item(threads=[thread(opened_by="alice", last_by="bob")]))
    assert v["turn"] == rules.THEM
    assert "threads" not in codes(v)


def test_every_open_thread_on_your_own_pull_request_is_yours():
    v = verdict(reviewed_pr(threads=[thread(opened_by="alice", last_by="bob")]))
    assert v["turn"] == rules.YOU
    assert "threads" in codes(v)


def test_red_ci_on_your_branch_is_your_move():
    v = verdict(item(mine=True, author=ME,
                     ci={"state": "FAILURE", "pending": 0, "total": 3,
                         "failed": [{"name": "build", "url": ""}]}))
    assert v["turn"] == rules.YOU
    assert "ci" in codes(v)
    assert "build" in [r["text"] for r in v["reasons"] if r["code"] == "ci"][0]


def test_pending_ci_is_not_failure():
    v = verdict(item(mine=True, author=ME,
                     ci={"state": "PENDING", "pending": 4, "total": 4, "failed": []},
                     review_requests=["reviewer"]))
    assert v["turn"] == rules.THEM


def test_changes_requested_is_your_move():
    v = verdict(item(mine=True, author=ME, review_decision="CHANGES_REQUESTED",
                     reviews=[{"login": "reviewer", "state": "CHANGES_REQUESTED",
                               "at": NOW - 2 * DAY, "url": ""}]))
    assert v["turn"] == rules.YOU
    assert v["waiting_since"] == NOW - 2 * DAY


def test_approved_waits_on_them():
    v = verdict(item(mine=True, author=ME, review_decision="APPROVED",
                     reviews=[{"login": "reviewer", "state": "APPROVED",
                               "at": NOW - DAY, "url": ""}]))
    assert v["turn"] == rules.THEM
    assert "approved" in codes(v)


def test_nobody_reviewing_becomes_your_move_after_a_few_days():
    v = verdict(item(mine=True, author=ME, created=NOW - 10 * DAY))
    assert v["turn"] == rules.YOU
    assert "no_reviewer" in codes(v)


def test_fresh_pull_request_is_not_nagged_about():
    v = verdict(item(mine=True, author=ME, created=NOW - DAY))
    assert v["turn"] == rules.THEM


def test_draft_with_nothing_pending_is_idle():
    v = verdict(item(mine=True, author=ME, draft=True))
    assert v["turn"] == rules.IDLE


def test_draft_with_an_unanswered_question_is_still_your_move():
    v = verdict(item(mine=True, author=ME, draft=True, threads=[thread()]))
    assert v["turn"] == rules.YOU


# ---------- other people's pull requests ----------

def test_review_request_is_your_move():
    v = verdict(item(found_by=["review"]))
    assert v["turn"] == rules.YOU
    assert "requested" in codes(v)


def test_review_request_you_already_answered_is_theirs():
    v = verdict(item(found_by=["review"], head_at=NOW - 4 * DAY,
                     reviews=[{"login": ME, "state": "APPROVED",
                               "at": NOW - 2 * DAY, "url": ""}]))
    assert v["turn"] == rules.THEM


def test_new_commits_after_your_review_are_not_your_move_by_default():
    """Mostly a rebase or a fixup on an active branch, and re-reading every
    push is not a job anyone wants."""
    v = verdict(item(reviews=[{"login": ME, "state": "COMMENTED",
                               "at": NOW - 3 * DAY, "url": ""}],
                     head_at=NOW - DAY))
    assert v["turn"] == rules.THEM
    assert "repushed" not in codes(v)


def test_new_commits_can_be_made_your_move():
    v = rules.classify(item(reviews=[{"login": ME, "state": "COMMENTED",
                                      "at": NOW - 3 * DAY, "url": ""}],
                            head_at=NOW - DAY),
                       ME, dict(CFG, repush_is_your_move=True), NOW)
    assert v["turn"] == rules.YOU
    assert "repushed" in codes(v)
    assert v["waiting_since"] == NOW - DAY


def test_an_unanswered_thread_still_shows_through_a_repush():
    """Turning the push rule off must not hide a question you never answered."""
    v = verdict(item(reviews=[{"login": ME, "state": "COMMENTED",
                               "at": NOW - 3 * DAY, "url": ""}],
                     head_at=NOW - DAY,
                     threads=[thread(opened_by=ME, last_by="author")]))
    assert v["turn"] == rules.YOU
    assert "threads" in codes(v)


def test_your_review_then_nothing_waits_on_the_author():
    v = verdict(item(reviews=[{"login": ME, "state": "COMMENTED",
                               "at": NOW - DAY, "url": ""}],
                     head_at=NOW - 3 * DAY))
    assert v["turn"] == rules.THEM
    assert "reviewed" in codes(v)


def test_someone_answered_in_a_thread_you_opened():
    v = verdict(item(threads=[thread(opened_by=ME, last_by="author")]))
    assert v["turn"] == rules.YOU


def test_merely_watching_is_not_your_move():
    v = verdict(item(threads=[thread(opened_by="x", last_by="y")]))
    assert v["turn"] == rules.THEM
    assert "watching" in codes(v)


# ---------- issues ----------

def test_unanswered_issue_on_your_own_repository():
    v = verdict(item(kind="issue", repo=f"{ME}/tool", id=f"{ME}/tool#7",
                     author="stranger"))
    assert v["turn"] == rules.YOU
    assert "unanswered" in codes(v)
    assert v["waiting_since"] == NOW - 5 * DAY


def test_issue_on_your_repository_where_they_spoke_last():
    v = verdict(item(kind="issue", repo=f"{ME}/tool", id=f"{ME}/tool#7",
                     author="stranger", comments=2,
                     last_comment={"login": "stranger", "at": NOW - 2 * DAY}))
    assert v["turn"] == rules.YOU


def test_issue_on_your_repository_you_answered_last():
    v = verdict(item(kind="issue", repo=f"{ME}/tool", id=f"{ME}/tool#7",
                     author="stranger", comments=2,
                     last_comment={"login": ME, "at": NOW - 2 * DAY}))
    assert v["turn"] == rules.THEM


def test_your_issue_elsewhere_that_got_an_answer():
    v = verdict(item(kind="issue", mine=True, author=ME, comments=1,
                     last_comment={"login": "maintainer", "at": NOW - DAY}))
    assert v["turn"] == rules.YOU
    assert "maintainer" in [r["text"] for r in v["reasons"]][0]


def test_your_issue_elsewhere_with_no_reply_waits_on_them():
    v = verdict(item(kind="issue", mine=True, author=ME))
    assert v["turn"] == rules.THEM


def test_issue_assigned_to_you():
    v = verdict(item(kind="issue", assignees=[ME], author="stranger"))
    assert v["turn"] == rules.YOU
    assert "assigned" in codes(v)


# ---------- discussions ----------

def test_question_on_your_repository_with_no_answer():
    v = verdict(item(kind="discussion", repo=f"{ME}/tool", id=f"{ME}/tool#d3",
                     author="stranger"))
    assert v["turn"] == rules.YOU


def test_your_own_announcement_nobody_replied_to_is_idle():
    v = verdict(item(kind="discussion", repo=f"{ME}/tool", id=f"{ME}/tool#d3",
                     mine=True, author=ME, comments=0, category="Announcements"))
    assert v["turn"] == rules.IDLE


def test_a_post_you_wrote_asking_something_waits_on_them():
    """A post to the people who forked your project is waiting for a reply.

    It used to land in "quiet" beside a permanent welcome banner, under a
    fortnight of merged work, which is how it got forgotten.
    """
    v = verdict(item(kind="discussion", repo=f"{ME}/tool", id=f"{ME}/tool#d104",
                     mine=True, author=ME, comments=0, category="General"))
    assert v["turn"] == rules.THEM
    assert "no_replies" in codes(v)


def test_an_unanswered_post_outranks_equally_old_chatter():
    """Weight only separates items of the same age — time comes first."""
    asking = verdict(item(kind="discussion", mine=True, author=ME, comments=0,
                          category="General", created=NOW - DAY))
    watching = verdict(item(kind="discussion", comments=3,
                            last_comment={"login": "alice", "at": NOW - DAY}))
    assert asking["priority"] > watching["priority"]
    assert abs(asking["priority"] - watching["priority"]) < 1


def test_a_renamed_announcement_category_is_treated_as_an_ordinary_post():
    """Safer direction: an extra card beats a silently hidden question."""
    v = verdict(item(kind="discussion", mine=True, author=ME, comments=0,
                     category="Новости проекта"))
    assert v["turn"] == rules.THEM


def discussion(**over):
    base = {"kind": "discussion", "repo": "acme/tool", "id": "acme/tool#d3",
            "author": "stranger", "participants": []}
    base.update(over)
    return item(**base)


def test_a_reply_to_you_elsewhere_is_your_move():
    """The case the first version missed entirely.

    You answer a question in someone's discussion, they come back a week later,
    and nothing on GitHub tells you — the thread is not "yours" by any field
    the search returns.
    """
    v = verdict(discussion(participants=[ME, "stranger"],
                           comments=4,
                           last_comment={"login": "stranger", "at": NOW - DAY}))
    assert v["turn"] == rules.YOU
    assert "stranger" in [r["text"] for r in v["reasons"]][0]


def test_a_reply_inside_a_branch_counts_and_says_so():
    """Replies live under comments; reading only the top level called this quiet."""
    v = verdict(discussion(participants=[ME, "stranger"], comments=66,
                           last_comment={"login": "stranger", "at": NOW - DAY,
                                         "reply": True}))
    assert v["turn"] == rules.YOU
    assert "в ветке" in [r["text"] for r in v["reasons"]][0]


def test_a_discussion_you_never_wrote_in_is_not_your_move():
    v = verdict(discussion(participants=["alice", "bob"], comments=4,
                           last_comment={"login": "bob", "at": NOW - DAY}))
    assert v["turn"] == rules.THEM


def test_a_reply_to_your_own_discussion_is_your_move():
    v = verdict(discussion(mine=True, author=ME, participants=[ME, "stranger"],
                           comments=3,
                           last_comment={"login": "stranger", "at": NOW - DAY}))
    assert v["turn"] == rules.YOU


def test_you_replied_last_so_it_is_not_your_move():
    v = verdict(discussion(participants=[ME, "stranger"], comments=5,
                           last_comment={"login": ME, "at": NOW - DAY}))
    assert v["turn"] != rules.YOU


def test_a_branch_reply_on_your_own_repository_is_your_move():
    v = verdict(discussion(repo=f"{ME}/tool", id=f"{ME}/tool#d3",
                           participants=["stranger"], comments=9,
                           last_comment={"login": "stranger", "at": NOW - DAY,
                                         "reply": True}))
    assert v["turn"] == rules.YOU
    assert "в ветке" in [r["text"] for r in v["reasons"]][0]


# ---------- settled ----------

def test_a_merged_pull_request_is_done_not_a_task():
    """Before this, a merged pull request either vanished or, if the searches
    still returned it, collected "nobody is reviewing this"."""
    v = verdict(item(mine=True, author=ME, state="MERGED", merged=True,
                     closed_at=NOW - 2 * DAY, created=NOW - 40 * DAY))
    assert v["turn"] == rules.DONE
    assert "merged" in codes(v)
    assert v["waiting_since"] == NOW - 2 * DAY


def test_a_closed_pull_request_is_not_called_merged():
    v = verdict(item(mine=True, author=ME, state="CLOSED", merged=False,
                     closed_at=NOW - DAY))
    assert v["turn"] == rules.DONE
    assert "dropped" in codes(v)


def test_a_closed_issue_is_done():
    v = verdict(item(kind="issue", state="CLOSED", closed_at=NOW - DAY,
                     close_reason="COMPLETED"))
    assert v["turn"] == rules.DONE
    assert "closed" in codes(v)


def test_not_planned_reads_differently_from_fixed():
    v = verdict(item(kind="issue", state="CLOSED", closed_at=NOW - DAY,
                     close_reason="NOT_PLANNED"))
    assert "wontfix" in codes(v)


def test_settled_items_are_never_archived():
    """The closed-search already bounds these; age is the point, not a reason
    to hide them."""
    v = verdict(item(mine=True, author=ME, state="MERGED", merged=True,
                     closed_at=NOW - 3 * DAY, updated=NOW - 3 * DAY,
                     created=NOW - 900 * DAY))
    assert v["turn"] == rules.DONE


def test_fresher_work_sorts_above_older_work():
    """In "done", and only there, age counts down."""
    today = verdict(item(state="MERGED", merged=True, closed_at=NOW - 3600))
    last_week = verdict(item(state="MERGED", merged=True, closed_at=NOW - 6 * DAY))
    assert today["priority"] > last_week["priority"]


def test_done_never_outranks_a_live_obligation():
    done = verdict(item(state="MERGED", merged=True, closed_at=NOW - 3600))
    live = verdict(item(found_by=["review"]))
    assert live["priority"] > done["priority"]


def test_summary_counts_done():
    got = rules.summarise([
        {"verdict": verdict(item(state="MERGED", merged=True, closed_at=NOW - DAY))},
        {"verdict": verdict(item(mine=True, author=ME, threads=[thread()]))},
    ])
    assert got["done"] == 1
    assert got["you"] == 1


# ---------- archive ----------

def test_years_old_review_request_is_archived_not_urgent():
    """The board opened on a 2019 review request until this existed."""
    old = NOW - 900 * DAY
    v = verdict(item(found_by=["review"], created=old, updated=old, head_at=old))
    assert v["turn"] == rules.ARCHIVE
    assert v["would_be"] == rules.YOU
    assert v["abandoned_days"] == 900


def test_archived_items_sort_below_live_ones():
    old = NOW - 900 * DAY
    archived = verdict(item(found_by=["review"], created=old, updated=old, head_at=old))
    live = verdict(item(kind="issue", mine=True, author=ME, comments=1,
                        last_comment={"login": "someone", "at": NOW - DAY}))
    assert archived["priority"] < live["priority"]


def test_recent_activity_keeps_an_old_item_out_of_the_archive():
    v = verdict(item(mine=True, author=ME, created=NOW - 900 * DAY,
                     updated=NOW - 2 * DAY, threads=[thread()]))
    assert v["turn"] == rules.YOU


# ---------- ordering ----------

def test_a_fresh_request_outranks_an_old_nag():
    """Age breaks ties; it does not decide them.

    The first cut multiplied age into the score and put a five-year-old pull
    request above a review requested this morning.
    """
    fresh = verdict(item(found_by=["review"], created=NOW - DAY, updated=NOW - DAY))
    old_nag = verdict(item(mine=True, author=ME, created=NOW - 100 * DAY,
                           updated=NOW - 20 * DAY))
    assert old_nag["turn"] == rules.YOU
    assert fresh["priority"] > old_nag["priority"]


def test_waiting_on_them_is_ordered_by_time_alone():
    """The board showed 21, 16, 15, 16, 15 days — sixteen below fifteen.

    Nothing in this bucket is yours to do, so what each item is barely matters
    and time is the whole signal; mixing the two gave an order that was neither.
    Newest first, like the project tabs.
    """
    def waited(days, **over):
        return verdict(item(mine=True, author=ME, review_requests=["r"],
                            head_at=NOW - days * DAY, created=NOW - 200 * DAY,
                            updated=NOW - 1 * DAY, **over))

    ages = [21, 16, 15, 3, 1]
    scored = [(d, waited(d)["priority"]) for d in ages]
    assert [d for d, _ in sorted(scored, key=lambda p: -p[1])] == [1, 3, 15, 16, 21]


def test_the_reason_only_breaks_ties_between_equal_ages():
    same_age = {"created": NOW - 200 * DAY, "updated": NOW - DAY,
                "head_at": NOW - 10 * DAY}
    review = verdict(item(mine=True, author=ME, review_requests=["r"], **same_age))
    approved = verdict(item(mine=True, author=ME, review_decision="APPROVED",
                            reviews=[{"login": "r", "state": "APPROVED",
                                      "at": NOW - 10 * DAY, "url": ""}],
                            **same_age))
    assert approved["priority"] > review["priority"]      # closer to done
    assert abs(approved["priority"] - review["priority"]) < 1  # but only just


def test_your_move_outranks_their_move():
    yours = verdict(item(kind="issue", repo=f"{ME}/tool", author="stranger"))
    theirs = verdict(item(kind="issue", mine=True, author=ME))
    assert yours["priority"] > theirs["priority"]


# ---------- staleness and unread ----------

def test_only_your_own_move_can_go_stale():
    yours = verdict(item(mine=True, author=ME,
                         threads=[thread(last_at=NOW - 30 * DAY)]))
    theirs = verdict(item(mine=True, author=ME, created=NOW - 60 * DAY,
                          updated=NOW - 2 * DAY, head_at=NOW - 40 * DAY,
                          review_requests=["reviewer"]))
    assert yours["stale"] is True
    assert theirs["stale"] is False
    assert theirs["rotting"] is True


def test_unread_notification_does_not_invent_an_obligation():
    v = verdict(item(kind="issue", mine=True, author=ME,
                     notifications=[{"reason": "subscribed", "unread": True}]))
    assert v["turn"] == rules.THEM
    assert v["unread"] == 1
    assert "unread" in codes(v)


def test_missing_details_are_admitted():
    v = verdict(item(mine=True, author=ME, incomplete=True))
    assert "incomplete" in codes(v)


# ---------- summary ----------

def test_summary_counts_each_bucket():
    items = [
        {"verdict": verdict(item(mine=True, author=ME, threads=[thread()]))},
        {"verdict": verdict(item(kind="issue", mine=True, author=ME))},
        {"verdict": verdict(item(mine=True, author=ME, draft=True))},
        {"verdict": verdict(item(found_by=["review"], created=NOW - 900 * DAY,
                                 updated=NOW - 900 * DAY))},
    ]
    got = rules.summarise(items)
    assert (got["you"], got["them"], got["idle"], got["archive"]) == (1, 1, 1, 1)
    assert got["total"] == 4


@pytest.mark.parametrize("n,expected", [
    (1, "тред ждёт ответа"), (2, "треда ждут ответа"), (5, "тредов ждут ответа"),
    (11, "тредов ждут ответа"), (21, "тред ждёт ответа"), (22, "треда ждут ответа"),
])
def test_russian_plurals(n, expected):
    threads = [thread(id=f"t{i}") for i in range(n)]
    v = verdict(item(mine=True, author=ME, threads=threads))
    assert expected in [r["text"] for r in v["reasons"] if r["code"] == "threads"][0]


def test_classification_does_not_mutate_the_item():
    original = item(mine=True, author=ME, threads=[thread()])
    copy = dict(original)
    rules.classify(original, ME, CFG, NOW)
    assert {k: original[k] for k in copy} == copy
