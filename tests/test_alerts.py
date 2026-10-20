"""Which "your move" cards are worth a Telegram message.

Only ones where somebody else did something. A pull request of yours that
nobody is reviewing is your move by the board's rules, but nobody moved — the
message would say "still nothing happened", once a day, about work you are in
the middle of.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

import alerts as alerts_mod  # noqa: E402


def make(tmp_path, **root):
    cfg = dict(root, telegram={"enabled": True, "chats": [1],
                               "state_file": str(tmp_path / "state.json")})
    a = alerts_mod.Alerts(cfg)
    a.seeded = True
    a.sent = []
    a._send = lambda text: a.sent.append(text) or True
    return a


def card(number, *reasons):
    return {"id": f"apache/ignite#{number}", "repo": "apache/ignite", "number": number,
            "title": "t", "url": "", "kind": "pr",
            "verdict": {"turn": "you", "priority": 50,
                        "reasons": [{"code": c, "text": c, "weight": w} for c, w in reasons]}}


def test_nobody_reviewing_your_own_pull_request_is_not_a_message(tmp_path):
    """The exact card that prompted this: IGNITE-28867, nobody looking."""
    a = make(tmp_path)
    a.process([card(13335, ("no_reviewer", 20))], {"you": 1})
    assert a.sent == []


def test_red_ci_on_your_own_branch_is_not_a_message(tmp_path):
    a = make(tmp_path)
    a.process([card(1, ("ci", 35))], {"you": 1})
    assert a.sent == []


def test_somebody_answering_you_still_is(tmp_path):
    a = make(tmp_path)
    a.process([card(1, ("threads", 40))], {"you": 1})
    assert len(a.sent) == 1


def test_a_card_with_both_kinds_is_sent_once(tmp_path):
    """Red CI does not cancel the answer that arrived next to it."""
    a = make(tmp_path)
    a.process([card(1, ("ci", 35), ("threads", 40))], {"you": 1})
    assert len(a.sent) == 1


def test_own_work_turning_into_somebody_elses_move_is_announced(tmp_path):
    a = make(tmp_path)
    a.process([card(1, ("no_reviewer", 20))], {"you": 1})
    a.process([card(1, ("changes", 45))], {"you": 1})
    assert len(a.sent) == 1


def test_own_work_can_be_switched_back_on(tmp_path):
    a = make(tmp_path, notify_own_work=True)
    a.process([card(13335, ("no_reviewer", 20))], {"you": 1})
    assert len(a.sent) == 1


def test_weightless_extras_do_not_make_it_somebody_elses(tmp_path):
    """"Unread in the inbox" sits next to the real reason with no weight; it
    must not turn a nobody-is-reviewing card into a message."""
    a = make(tmp_path)
    a.process([card(1, ("no_reviewer", 20), ("unread", 0))], {"you": 1})
    assert a.sent == []


def test_the_morning_digest_leaves_own_work_out(tmp_path):
    import time
    from datetime import datetime
    a = make(tmp_path)
    a.digest_hour = datetime.fromtimestamp(time.time()).hour
    a.last_digest = 0
    a._maybe_digest([card(13335, ("no_reviewer", 20)), card(2, ("requested", 50))],
                    {"you": 2})
    assert len(a.sent) == 1
    assert "#2" in a.sent[0] and "#13335" not in a.sent[0]
