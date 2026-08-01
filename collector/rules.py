"""Whose move is it?

This is the only question the dashboard exists to answer. A list of open pull
requests is already available on GitHub and is useless precisely because
everything on it looks equally alive: a pull request waiting on a reviewer for
three weeks and one with an unanswered question from yesterday sit next to each
other, sorted by a date that moves whenever a bot touches them.

So every item is placed in one of three buckets:

* **you** — nothing will happen until you do something. An unanswered review
  comment, a red build on your own branch, a review someone asked you for, an
  issue on your repository that nobody has replied to.
* **them** — your part is done and someone else owes the next move. Worth
  seeing, not worth acting on; the only useful signal here is age.
* **idle** — drafts, and things nobody is waiting on.

Two principles keep this honest. First, being in "you" must mean there is a
concrete action, otherwise the bucket fills with things that cannot be cleared
and stops being read. Second, `waiting_since` is when the ball landed in your
court — not when the item was last touched — because that is the number that
says which of thirty items has been ignored longest.
"""

from __future__ import annotations

DAY = 86400.0

YOU, THEM, IDLE, ARCHIVE, DONE = "you", "them", "idle", "archive", "done"

# A review that says nothing about approval should not count as one.
BLOCKING_REVIEW = "CHANGES_REQUESTED"
APPROVING_REVIEW = "APPROVED"

# Discussion categories whose whole point is that nobody has to answer. Matched
# by name because the API has no flag for it; a repository that renames the
# category simply gets its announcements treated as ordinary posts, which is
# the safer way round.
ANNOUNCEMENT_CATEGORIES = {"announcements", "announcement", "объявления"}


def classify(item: dict, login: str, cfg: dict, now: float) -> dict:
    """Decide the bucket, the reasons for it, and how long it has been so."""
    # Settled first: a merged pull request has no move left, and asking the
    # rules below whose turn it is would put "nobody is reviewing this" on
    # something that shipped on Thursday.
    settled = item.get("state") not in (None, "OPEN")
    kind = item.get("kind")
    if settled:
        verdict = _settled(item, now)
    elif kind == "pr":
        verdict = _pull_request(item, login, cfg, now)
    elif kind == "issue":
        verdict = _issue(item, login, cfg, now)
    else:
        verdict = _discussion(item, login, cfg, now)

    # An unread notification never *creates* an obligation — a subscribed-thread
    # notice about someone else's conversation is not your move — but it does
    # mark the item as carrying something you have not seen.
    unread = [n for n in item.get("notifications") or [] if n.get("unread")]
    verdict["unread"] = len(unread)
    if unread and verdict["turn"] != YOU:
        verdict.setdefault("reasons", []).append(
            _reason("unread", f"непрочитано в инбоксе ({len(unread)})"))

    if item.get("incomplete"):
        verdict.setdefault("reasons", []).append(
            _reason("incomplete", "детали не загрузились — данные неполные"))

    since = verdict.get("waiting_since") or item.get("updated") or now
    verdict["waiting_since"] = since
    verdict["waiting_days"] = max(0.0, (now - since) / DAY)

    # Anything nobody has touched for this long is not a task, whatever the
    # rules above concluded. Without it the top of the board was a pull request
    # from 2019 whose review request is technically still pending and seven
    # more nobody was ever going to look at — which is precisely the pile this
    # dashboard exists to see past. They keep their verdict under `would_be`,
    # so the archive can still be sorted and cleared deliberately rather than
    # silently dropped.
    # Something finished is never archived: the closed-search already bounds it
    # to the last few days, and "done" is the one bucket where age is the point.
    idle_days = (now - (item.get("updated") or now)) / DAY
    if (not settled and idle_days >= float(cfg.get("ignore_days", 30))
            and verdict["turn"] != IDLE):
        verdict["would_be"] = verdict["turn"]
        verdict["turn"] = ARCHIVE
        verdict["abandoned_days"] = round(idle_days)

    stale_after = float(cfg.get("stale_days", 14))
    # Only a move you are not making can go stale. An item waiting on someone
    # else is *supposed* to sit there; calling that stale would paint the board
    # red for other people's queues.
    verdict["stale"] = (verdict["turn"] == YOU and verdict["waiting_days"] >= stale_after)
    verdict["rotting"] = (verdict["turn"] == THEM
                          and verdict["waiting_days"] >= float(cfg.get("rot_days", 30)))
    verdict["priority"] = _priority(item, verdict, cfg)
    return verdict


def _reason(code: str, text: str, weight: int = 0) -> dict:
    return {"code": code, "text": text, "weight": weight}


def _settled(item: dict, now: float) -> dict:
    """Something that is finished. What it says depends on how it ended.

    Merged, abandoned and "not planned" are three different endings, and the
    board is read partly to see what got done — so they get three different
    words rather than one grey "closed".
    """
    closed = item.get("closed_at") or item.get("updated") or now
    if item.get("merged"):
        reason = _reason("merged", "смержен", 30)
    elif item.get("kind") == "pr":
        reason = _reason("dropped", "закрыт без мержа", 10)
    elif item.get("close_reason") == "NOT_PLANNED":
        reason = _reason("wontfix", "закрыт как ненужный", 5)
    else:
        reason = _reason("closed", "закрыт", 20)
    return {"turn": DONE, "reasons": [reason], "waiting_since": closed}


def _my_threads(item: dict, login: str) -> list[dict]:
    """Unresolved threads, ignoring ones that no longer point at live code.

    An outdated thread — its lines rewritten by a later push — is usually
    already dealt with, and GitHub hides it behind a click. Counting those as
    outstanding work put six permanently unanswerable items on the board.
    """
    return [t for t in (item.get("threads") or [])
            if not t.get("resolved") and not t.get("outdated")]


def _last_review_by(item: dict, login: str) -> dict | None:
    mine = [r for r in item.get("reviews") or [] if r.get("login") == login]
    return max(mine, key=lambda r: r.get("at") or 0) if mine else None


def _pull_request(item: dict, login: str, cfg: dict, now: float) -> dict:
    reasons: list[dict] = []
    waiting: list[float] = []
    threads = _my_threads(item, login)

    # Threads someone else spoke in last are the most reliable "you owe an
    # answer" signal there is — but only where the answer is owed by *you*. On
    # your own pull request every open thread is yours to close. On someone
    # else's, a conversation between two other people is not your business, and
    # counting it put other people's arguments in your queue.
    #
    # Involvement is judged by who opened the thread and who spoke last, which
    # is what the API gives cheaply. A thread you commented in the middle of
    # and someone else has since answered is missed; fetching every comment of
    # every thread to catch it would cost more than the case is worth.
    if item.get("mine"):
        awaiting_me = [t for t in threads if t.get("last_by") != login]
    else:
        awaiting_me = [t for t in threads
                       if t.get("last_by") != login and t.get("opened_by") == login]
    if awaiting_me:
        reasons.append(_reason(
            "threads", f"{len(awaiting_me)} " + _plural(
                len(awaiting_me), "тред ждёт ответа", "треда ждут ответа",
                "тредов ждут ответа"), weight=40))
        waiting.extend(t.get("last_at") or 0 for t in awaiting_me)

    if item.get("mine"):
        if item.get("draft"):
            # A draft with an unanswered question is still your move; a draft
            # with nothing pending is simply not anyone's turn yet.
            if not awaiting_me:
                return {"turn": IDLE, "reasons": [_reason("draft", "черновик")],
                        "waiting_since": item.get("updated")}
            reasons.append(_reason("draft", "черновик"))

        ci = item.get("ci") or {}
        if ci.get("failed"):
            names = ", ".join(c["name"] for c in ci["failed"][:3])
            more = "" if len(ci["failed"]) <= 3 else f" и ещё {len(ci['failed']) - 3}"
            reasons.append(_reason("ci", f"CI красный: {names}{more}", weight=35))
            waiting.append(item.get("head_at") or item.get("updated") or now)

        if item.get("review_decision") == BLOCKING_REVIEW:
            reasons.append(_reason("changes", "запрошены правки", weight=45))
            blocking = [r for r in item.get("reviews") or []
                        if r.get("state") == BLOCKING_REVIEW]
            waiting.extend(r.get("at") or 0 for r in blocking)

        if reasons and any(r["weight"] for r in reasons):
            return {"turn": YOU, "reasons": reasons, "waiting_since": _earliest(waiting)}

        # Nothing is blocked, so it is someone else's move — but note *whose*,
        # because "waiting for review" and "nobody has been asked" need
        # different actions from you.
        if item.get("review_decision") == APPROVING_REVIEW:
            return {"turn": THEM, "reasons": reasons + [
                _reason("approved", "одобрен — ждёт мержа", 15)],
                "waiting_since": _last_approval(item) or item.get("updated")}
        if not item.get("review_requests") and not item.get("reviews"):
            age_days = (now - (item.get("created") or now)) / DAY
            if age_days >= float(cfg.get("unreviewed_days", 3)):
                return {"turn": YOU, "reasons": reasons + [_reason(
                    "no_reviewer", "никто не смотрит — стоит попросить ревью", 20)],
                    "waiting_since": item.get("created")}
        return {"turn": THEM, "reasons": reasons + [_reason("review", "ждёт ревью", 10)],
                "waiting_since": item.get("head_at") or item.get("updated")}

    # Someone else's pull request.
    asked = "review" in (item.get("found_by") or []) or login in (item.get("review_requests") or [])
    my_review = _last_review_by(item, login)
    if asked and not my_review:
        return {"turn": YOU, "reasons": reasons + [
            _reason("requested", "у тебя запросили ревью", 50)],
            "waiting_since": _earliest(waiting) or item.get("updated")}

    if my_review:
        head = item.get("head_at") or 0
        # New commits after your review mean the author has answered and is
        # waiting again — the most commonly missed hand-back there is.
        if head > (my_review.get("at") or 0) + 60:
            return {"turn": YOU, "reasons": reasons + [
                _reason("repushed", "новые коммиты после твоего ревью", 30)],
                "waiting_since": head}
        if awaiting_me:
            return {"turn": YOU, "reasons": reasons, "waiting_since": _earliest(waiting)}
        return {"turn": THEM, "reasons": reasons + [
            _reason("reviewed", "ты отревьюил — ждём автора", 8)],
            "waiting_since": my_review.get("at")}

    if awaiting_me:
        return {"turn": YOU, "reasons": reasons, "waiting_since": _earliest(waiting)}

    return {"turn": THEM, "reasons": reasons or [_reason("watching", "просто следишь", 2)],
            "waiting_since": item.get("updated")}


def _issue(item: dict, login: str, cfg: dict, now: float) -> dict:
    last = item.get("last_comment") or {}
    mine_repo = item.get("repo", "").split("/")[0] == login
    assigned = login in (item.get("assignees") or [])

    # On your own repository, silence is your problem: an issue nobody has
    # answered is the classic thing that quietly ages out.
    if mine_repo and not item.get("mine"):
        if not last:
            return {"turn": YOU, "reasons": [_reason(
                "unanswered", "никто не ответил", 45)],
                "waiting_since": item.get("created")}
        if last.get("login") != login:
            return {"turn": YOU, "reasons": [_reason(
                "reply", f"последним писал {last['login']}", 40)],
                "waiting_since": last.get("at")}
        return {"turn": THEM, "reasons": [_reason("answered", "ты ответил последним", 6)],
                "waiting_since": last.get("at")}

    if assigned and (not last or last.get("login") != login):
        return {"turn": YOU, "reasons": [_reason("assigned", "назначено на тебя", 35)],
                "waiting_since": (last or {}).get("at") or item.get("created")}

    if item.get("mine"):
        if last and last.get("login") != login:
            return {"turn": YOU, "reasons": [_reason(
                "reply", f"тебе ответил {last['login']}", 30)],
                "waiting_since": last.get("at")}
        return {"turn": THEM, "reasons": [_reason("waiting", "ждёшь ответа", 12)],
                "waiting_since": (last or {}).get("at") or item.get("created")}

    if last and last.get("login") != login and _mentions_me(item, login):
        return {"turn": YOU, "reasons": [_reason("mentioned", "тебя упомянули", 25)],
                "waiting_since": last.get("at")}

    return {"turn": THEM, "reasons": [_reason("watching", "просто следишь", 2)],
            "waiting_since": (last or {}).get("at") or item.get("updated")}


def _discussion(item: dict, login: str, cfg: dict, now: float) -> dict:
    """Discussions, where "last spoke" has to mean the whole nested thread.

    Top-level comments are only half a discussion; the answers live in replies
    under them, and one thread here had 7 comments and 59 replies. collect.py
    flattens both into `last_comment` and `participants`, and this reads the
    flattened view — otherwise a reply from today reads as silence since June.
    """
    last = item.get("last_comment") or {}
    mine_repo = item.get("repo", "").split("/")[0] == login
    they_spoke_last = bool(last) and last.get("login") != login
    where = " (в ветке)" if last.get("reply") else ""

    if mine_repo and not item.get("mine"):
        if not last:
            return {"turn": YOU, "reasons": [_reason(
                "unanswered", "вопрос без ответа", 45)],
                "waiting_since": item.get("created")}
        if they_spoke_last:
            return {"turn": YOU, "reasons": [_reason(
                "reply", f"последним писал {last['login']}{where}", 40)],
                "waiting_since": last.get("at")}
        return {"turn": THEM, "reasons": [_reason("answered", "ты ответил последним", 6)],
                "waiting_since": last.get("at")}

    # Anywhere else, the question is whether this conversation is yours at all.
    # Being subscribed to someone else's thread is not an obligation; having
    # written in it and been answered is the whole point of this dashboard.
    involved = item.get("mine") or login in (item.get("participants") or [])
    if they_spoke_last and involved:
        return {"turn": YOU, "reasons": [_reason(
            "reply", f"тебе ответил {last['login']}{where}", 35)],
            "waiting_since": last.get("at")}

    if they_spoke_last:
        return {"turn": THEM, "reasons": [_reason(
            "watching", f"последним писал {last['login']}{where}", 2)],
            "waiting_since": last.get("at")}

    if item.get("mine") and not item.get("comments"):
        # Your own thread that nobody has answered yet. An announcement is not
        # waiting for anything and belongs out of the way; a post asking people
        # something is waiting for exactly the reply that has not come, and
        # burying it under a fortnight of merged work is how it gets forgotten.
        # GitHub exposes no "this is an announcement" flag — `isAnswerable` is
        # false for General too — so the category name is the only signal.
        if (item.get("category") or "").strip().lower() in ANNOUNCEMENT_CATEGORIES:
            return {"turn": IDLE, "reasons": [_reason("announcement", "объявление", 1)],
                    "waiting_since": item.get("created")}
        return {"turn": THEM, "reasons": [_reason("no_replies", "ждёшь ответа", 12)],
                "waiting_since": item.get("created")}

    return {"turn": THEM, "reasons": [_reason("quiet", "тихо", 1)],
            "waiting_since": (last or {}).get("at") or item.get("updated")}


def _mentions_me(item: dict, login: str) -> bool:
    return any(n.get("reason") in ("mention", "team_mention", "assign")
               for n in item.get("notifications") or [])


def _last_approval(item: dict) -> float | None:
    times = [r.get("at") or 0 for r in item.get("reviews") or []
             if r.get("state") == APPROVING_REVIEW]
    return max(times) if times else None


def _earliest(values: list[float]) -> float | None:
    """The oldest pending moment, because that is the one that has waited."""
    live = [v for v in values if v]
    return min(live) if live else None


def _priority(item: dict, verdict: dict, cfg: dict) -> float:
    """Sort key within a bucket: what should be looked at first.

    What the reason *is* dominates; age only breaks ties. The first version had
    it the other way round and put a five-year-old pull request above a review
    someone asked for this morning, on the grounds that it had waited longer.
    Age is capped at a month's worth of nudging for exactly that reason, and
    an item old enough to be archived is pushed to the bottom instead.
    """
    base = max((r.get("weight", 0) for r in verdict.get("reasons") or []), default=0)
    turn = verdict["turn"]
    if turn == THEM:
        # Nothing here is yours to do, so *what* it is barely matters and the
        # order is by time alone — mixing in the reason gave sixteen days below
        # fifteen. Newest first, like the project tabs: this bucket is read to
        # see what moved, not to be nagged, and anything genuinely rotting is
        # flagged as such rather than pushed to the top.
        return round(-min(verdict.get("waiting_days", 0), 3650) + base / 100.0, 2)
    if turn == YOU:
        base += 20
    elif turn == DONE:
        # The only bucket where age counts down: what shipped today is the
        # interesting part of "what got done", last week's is background.
        return round(base - verdict.get("waiting_days", 0), 2)
    elif turn == IDLE:
        base -= 30
    elif turn == ARCHIVE:
        # Sorted among themselves by what they would have been, so a forgotten
        # review request still outranks a forgotten "just watching".
        return round(max(0.0, base * 0.2 - verdict.get("abandoned_days", 0) / 365.0), 2)

    base += min(verdict.get("waiting_days", 0), 30) * 0.5
    if verdict.get("stale"):
        base += 10
    if verdict.get("unread"):
        base += 5
    if item.get("private"):
        base += 2
    return round(base, 2)


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural agreement — the dashboard is read in Russian."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def summarise(items: list[dict]) -> dict:
    """Counts the header shows, and the alerting compares against."""
    buckets = {YOU: 0, THEM: 0, IDLE: 0, ARCHIVE: 0, DONE: 0}
    stale = rotting = unread = 0
    for item in items:
        verdict = item.get("verdict") or {}
        turn = verdict.get("turn", IDLE)
        buckets[turn] = buckets.get(turn, 0) + 1
        stale += bool(verdict.get("stale"))
        rotting += bool(verdict.get("rotting"))
        unread += verdict.get("unread") or 0
    return {"you": buckets[YOU], "them": buckets[THEM], "idle": buckets[IDLE],
            "archive": buckets[ARCHIVE], "done": buckets[DONE], "stale": stale,
            "rotting": rotting, "unread": unread, "total": len(items)}
