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
