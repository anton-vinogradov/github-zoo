"""Forks and stars: the two things GitHub never tells you about.

A fork produces no notification at all. A star produces one only if web
notifications are on and you happen to be looking. Both are found out months
later, by accident — which is the whole reason they are watched here.

The rule that matters most in this file is the first run staying silent:
someone installing this today has years of forks and stars, and greeting them
with a message per item teaches them to mute the bot on day one.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

import store as store_mod  # noqa: E402

NOW = 1_785_600_000.0


@pytest.fixture
def store(tmp_path):
    return store_mod.Store(str(tmp_path / "history.db"))


def fork(name, parent="me/tool", created=NOW):
    return {"name": name, "parent": parent, "url": f"https://github.com/{name}",
            "owner": name.split("/")[0], "created": created, "stars": 0}


def stars(repo, count, who=()):
    return {repo: {"repo": repo, "url": f"https://github.com/{repo}", "count": count,
                   "recent": [{"login": w, "at": NOW} for w in who]}}


# ---------- forks ----------

def test_the_first_run_announces_nothing(store):
    """Years of existing forks are not news on install day."""
    got = store.new_forks({f["name"]: f for f in
                           [fork("a/tool"), fork("b/tool")]}, NOW)
    assert got == []
    assert store.fork_count() == 2


def test_a_fork_that_appears_later_is_news(store):
    store.new_forks({"a/tool": fork("a/tool")}, NOW)
    got = store.new_forks({"a/tool": fork("a/tool"),
                           "b/tool": fork("b/tool")}, NOW + 300)
    assert [f["name"] for f in got] == ["b/tool"]


def test_a_known_fork_is_not_announced_twice(store):
    store.new_forks({"a/tool": fork("a/tool")}, NOW)
    store.new_forks({"a/tool": fork("a/tool"), "b/tool": fork("b/tool")}, NOW + 300)
    assert store.new_forks({"a/tool": fork("a/tool"),
                            "b/tool": fork("b/tool")}, NOW + 600) == []


def test_several_new_forks_come_newest_first(store):
    store.new_forks({"a/tool": fork("a/tool")}, NOW)
    got = store.new_forks({
        "a/tool": fork("a/tool"),
        "old/tool": fork("old/tool", created=NOW - 86400),
        "new/tool": fork("new/tool", created=NOW),
    }, NOW + 300)
    assert [f["name"] for f in got] == ["new/tool", "old/tool"]


def test_a_deleted_fork_that_returns_is_news_again(store):
    """Forgotten rather than remembered as deleted: nothing to act on while
    it is gone, and its return is a real event."""
    store.new_forks({"a/tool": fork("a/tool"), "b/tool": fork("b/tool")}, NOW)
    store.new_forks({"a/tool": fork("a/tool")}, NOW + 300)
    got = store.new_forks({"a/tool": fork("a/tool"), "b/tool": fork("b/tool")},
                          NOW + 600)
    assert [f["name"] for f in got] == ["b/tool"]


def test_no_forks_at_all_is_not_a_first_run(store):
    """An empty reply must not make the next real fork look like seeding."""
    assert store.new_forks({}, NOW) == []


# ---------- stars ----------

def test_existing_stars_are_not_announced(store):
    assert store.new_stars(stars("me/tool", 35, ["alice"]), NOW) == []


def test_a_new_star_names_who_left_it(store):
    store.new_stars(stars("me/tool", 1, ["alice"]), NOW)
    got = store.new_stars(stars("me/tool", 2, ["alice", "bob"]), NOW + 300)
    assert len(got) == 1
    assert got[0]["who"] == ["bob"]
    assert got[0]["count"] == 2
    assert got[0]["added"] == 1


def test_several_stars_at_once_name_all_of_them(store):
    store.new_stars(stars("me/tool", 1, ["alice"]), NOW)
    got = store.new_stars(stars("me/tool", 3, ["alice", "bob", "carol"]), NOW + 300)
    assert got[0]["who"] == ["bob", "carol"]
    assert got[0]["added"] == 2


def test_losing_a_star_says_nothing_but_updates_the_count(store):
    """Nothing to do about it, and the message would only sting."""
    store.new_stars(stars("me/tool", 5, ["alice"]), NOW)
    assert store.new_stars(stars("me/tool", 4, ["alice"]), NOW + 300) == []
    # and the lower count is now the baseline, so regaining it is one star
    got = store.new_stars(stars("me/tool", 5, ["alice", "bob"]), NOW + 600)
    assert got[0]["added"] == 1


def test_a_repository_seen_for_the_first_time_is_not_a_gain(store):
    """A repository created after install starts at whatever it has."""
    store.new_stars(stars("me/tool", 1, ["alice"]), NOW)
    current = stars("me/tool", 1, ["alice"])
    current.update(stars("me/fresh", 3, ["bob"]))
    assert store.new_stars(current, NOW + 300) == []


def test_more_stars_than_the_page_held_still_reports_the_right_count(store):
    """Names may be short; the number must not be wrong."""
    store.new_stars(stars("me/tool", 10, ["alice"]), NOW)
    got = store.new_stars(stars("me/tool", 25, ["x", "y"]), NOW + 300)
    assert got[0]["added"] == 15
    assert got[0]["count"] == 25
    assert got[0]["who"] == ["x", "y"]


def test_forks_and_stars_do_not_disturb_each_other(store):
    store.new_forks({"a/tool": fork("a/tool")}, NOW)
    store.new_stars(stars("me/tool", 1, ["alice"]), NOW)
    assert store.new_forks({"a/tool": fork("a/tool"), "b/tool": fork("b/tool")},
                           NOW + 300)
    assert store.new_stars(stars("me/tool", 2, ["alice", "bob"]), NOW + 300)


def test_both_survive_a_restart(tmp_path):
    path = str(tmp_path / "history.db")
    first = store_mod.Store(path)
    first.new_forks({"a/tool": fork("a/tool")}, NOW)
    first.new_stars(stars("me/tool", 1, ["alice"]), NOW)

    second = store_mod.Store(path)
    assert second.new_forks({"a/tool": fork("a/tool")}, NOW + 300) == []
    got = second.new_stars(stars("me/tool", 2, ["alice", "bob"]), NOW + 300)
    assert got[0]["who"] == ["bob"]


# ---------- approvals and comments on your own work ----------

def mine(**over):
    """A pull request of yours, as reconcile() receives it."""
    base = {"id": "acme/thing#1", "mine": True, "created": NOW, "updated": NOW,
            "comments": 0, "review_decision": None, "reviews": [], "approvals": [],
            "last_comment": None, "verdict": {"turn": "them", "waiting_since": NOW}}
    base.update(over)
    return base


def kinds(events, item_id="acme/thing#1"):
    return [e["kind"] for e in events if e["id"] == item_id]


def test_an_approval_is_an_event(store):
    """The message this whole thing exists to send: you can merge now.

    Approval moves an item towards done rather than towards you, so it never
    became "your move" and passed in complete silence.
    """
    store.reconcile([mine()], NOW)
    events = store.reconcile([mine(
        review_decision="APPROVED", approvals=["wernerdv"], updated=NOW + 60)],
        NOW + 300)
    assert "approved" in kinds(events)
    approved = [e for e in events if e["kind"] == "approved"][0]
    assert approved["detail"] == "wernerdv"


def test_an_approval_without_write_access_still_counts(store):
    """GitHub only moves reviewDecision for reviewers who can merge.

    On Apache Ignite a colleague approves and a committer merges, so a real
    approval leaves reviewDecision null — and watching that field alone missed
    the approval entirely.
    """
    store.reconcile([mine()], NOW)
    events = store.reconcile([mine(
        review_decision=None, approvals=["Vladsz83"], updated=NOW + 60)],
        NOW + 300)
    assert "approved" in kinds(events)
    assert [e for e in events if e["kind"] == "approved"][0]["detail"] == "Vladsz83"


def test_a_second_approver_is_announced_separately(store):
    store.reconcile([mine(approvals=["alice"])], NOW)
    events = store.reconcile([mine(approvals=["alice", "bob"], updated=NOW + 60)],
                             NOW + 300)
    assert [e for e in events if e["kind"] == "approved"][0]["detail"] == "bob"


def test_an_approval_is_announced_once(store):
    store.reconcile([mine()], NOW)
    approved = mine(review_decision="APPROVED", approvals=["wernerdv"],
                    updated=NOW + 60)
    store.reconcile([approved], NOW + 300)
    assert "approved" not in kinds(store.reconcile([approved], NOW + 600))


def test_a_new_comment_on_your_own_pull_request_is_an_event(store):
    store.reconcile([mine(comments=2)], NOW)
    events = store.reconcile([mine(
        comments=3, updated=NOW + 60,
        last_comment={"login": "nizhikov", "at": NOW + 60})], NOW + 300)
    assert "commented" in kinds(events)
    assert [e for e in events if e["kind"] == "commented"][0]["detail"] == "nizhikov"


def test_comments_on_other_peoples_work_are_not_announced(store):
    """Your inbox is theirs to manage; this board is about your own."""
    theirs = mine(mine=False)
    store.reconcile([theirs], NOW)
    events = store.reconcile([mine(mine=False, comments=5, updated=NOW + 60)],
                             NOW + 300)
    assert "commented" not in kinds(events)


def test_a_first_sighting_announces_nothing_but_appeared(store):
    """An item already approved when first polled is not news."""
    events = store.reconcile([mine(
        review_decision="APPROVED", comments=4, approvals=["r"])], NOW)
    assert kinds(events) == ["appeared"]


def test_changes_requested_is_recorded_separately_from_approval(store):
    store.reconcile([mine()], NOW)
    events = store.reconcile([mine(
        review_decision="CHANGES_REQUESTED", updated=NOW + 60,
        reviews=[{"login": "r", "state": "CHANGES_REQUESTED", "at": NOW + 60}])],
        NOW + 300)
    assert "changes_requested" in kinds(events)
    assert "approved" not in kinds(events)


def test_a_deleted_comment_is_not_an_event(store):
    store.reconcile([mine(comments=5)], NOW)
    events = store.reconcile([mine(comments=4, updated=NOW + 60)], NOW + 300)
    assert "commented" not in kinds(events)


def test_the_columns_are_added_to_an_older_database(tmp_path):
    """Upgrading in place must not need the history thrown away.

    The first pass after the migration seeds the new columns quietly — see
    test_adding_the_column_does_not_announce_standing_approvals — and the pass
    after that behaves normally.
    """
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE seen (id TEXT PRIMARY KEY, first_seen INTEGER NOT NULL,
          turn TEXT, turn_since INTEGER, last_seen INTEGER NOT NULL,
          last_updated INTEGER);
        INSERT INTO seen VALUES ('acme/thing#1', 1, 'them', 1, 1, 1);
    """)
    old.commit()
    old.close()

    fresh = store_mod.Store(str(path))
    assert fresh.ok
    fresh.reconcile([mine(updated=NOW)], NOW, "me")
    events = fresh.reconcile([mine(
        review_decision="APPROVED", approvals=["r"], updated=NOW + 60)],
        NOW + 300, "me")
    assert "approved" in kinds(events)


def test_your_own_comment_is_not_news(store):
    """Your comment raises the count too; being told what you just wrote is noise."""
    store.reconcile([mine(comments=2)], NOW, "me")
    events = store.reconcile([mine(
        comments=3, updated=NOW + 60,
        last_comment={"login": "me", "at": NOW + 60})], NOW + 300, "me")
    assert "commented" not in kinds(events)


def test_the_approver_is_named_even_after_the_merge(store):
    """Approved and merged between two polls: closed pull requests skip the
    detail pass, so the full review list is empty exactly when the name is
    wanted. `deciders` comes from the cheap search fields instead."""
    store.reconcile([mine()], NOW, "me")
    events = store.reconcile([mine(
        review_decision="APPROVED", state="MERGED", updated=NOW + 60,
        reviews=[],  # detail pass skipped for closed items
        approvals=["wernerdv"])], NOW + 300, "me")
    approved = [e for e in events if e["kind"] == "approved"]
    assert approved and approved[0]["detail"] == "wernerdv"


def test_adding_the_column_does_not_announce_standing_approvals(tmp_path):
    """Migrating an existing database announced every approval it had ever
    seen — eight messages at once, mostly about merged pull requests."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE seen (id TEXT PRIMARY KEY, first_seen INTEGER NOT NULL,
          turn TEXT, turn_since INTEGER, last_seen INTEGER NOT NULL,
          last_updated INTEGER, decision TEXT, comments INTEGER);
        INSERT INTO seen VALUES ('acme/thing#1', 1, 'them', 1, 1, 1, 'APPROVED', 0);
    """)
    old.commit()
    old.close()

    fresh = store_mod.Store(str(path))
    approved = mine(review_decision="APPROVED", approvals=["wernerdv"], updated=NOW)
    assert "approved" not in kinds(fresh.reconcile([approved], NOW, "me"))
    # ...but a new approver after that is still news
    events = fresh.reconcile([mine(review_decision="APPROVED", updated=NOW + 60,
                                   approvals=["wernerdv", "Vladsz83"])], NOW + 300, "me")
    assert [e for e in events if e["kind"] == "approved"][0]["detail"] == "Vladsz83"
