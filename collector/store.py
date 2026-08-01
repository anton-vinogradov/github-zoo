"""Persistence: the last snapshot, and what changed between snapshots.

Three jobs, all of which exist because a poller with no memory is annoying to
live with:

* **Survive a restart.** Without the last snapshot on disk, every deploy shows
  an empty dashboard for however long the first poll takes.
* **Say what is new.** "Three things changed since you last looked" is the
  reason to open the page at all, and it cannot be derived from a single
  snapshot.
* **Remember when the ball landed.** GitHub reports when an item was last
  touched, which is not the same as how long it has been your move. A pull
  request commented on by a bot yesterday can still have been waiting on you
  for three weeks, and only a local record knows that.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  ts     INTEGER PRIMARY KEY,
  body   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen (
  id           TEXT PRIMARY KEY,
  first_seen   INTEGER NOT NULL,
  turn         TEXT,
  turn_since   INTEGER,
  last_seen    INTEGER NOT NULL,
  last_updated INTEGER
);

CREATE TABLE IF NOT EXISTS events (
  ts     INTEGER NOT NULL,
  id     TEXT NOT NULL,
  kind   TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_id ON events (id, ts);
"""


class Store:
    def __init__(self, path: str, retention_days: int = 180):
        self.path = Path(path).expanduser()
        self.retention = retention_days
        self.lock = threading.Lock()
        self.ok = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                db.executescript(SCHEMA)
            self.ok = True
        except (OSError, sqlite3.Error) as err:
            # A dashboard that cannot write history is still a useful dashboard;
            # one that refuses to start because of it is not.
            self.error = str(err)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    # ---------- snapshots ----------

    def save_snapshot(self, snapshot: dict) -> None:
        if not self.ok:
            return
        body = json.dumps(snapshot, ensure_ascii=False)
        cutoff = int(time.time() - self.retention * 86400)
        try:
            with self.lock, self._db() as db:
                db.execute("INSERT OR REPLACE INTO snapshots (ts, body) VALUES (?, ?)",
                           (int(snapshot.get("generated") or time.time()), body))
                # Only the newest snapshot is ever read — it exists so a restart
                # shows the board immediately instead of an empty page. A full
                # snapshot is ~140 KB, so keeping a day of them at one every
                # five minutes meant a 40 MB database holding 287 copies nobody
                # would ever look at. Two spares are for the case where the
                # newest one is truncated by a crash mid-write.
                db.execute(
                    "DELETE FROM snapshots WHERE ts NOT IN "
                    "(SELECT ts FROM snapshots ORDER BY ts DESC LIMIT 3)")
                db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        except sqlite3.Error:
            pass

    def last_snapshot(self) -> dict | None:
        if not self.ok:
            return None
        try:
            with self.lock, self._db() as db:
                row = db.execute(
                    "SELECT body FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        try:
            return json.loads(row["body"])
        except ValueError:
            return None

    # ---------- per-item memory ----------

    def reconcile(self, items: list[dict], now: float) -> list[dict]:
        """Record what each item is now, and return the changes worth telling.

        Called once per poll with the classified items. It mutates each item to
        carry `first_seen` and the true `turn_since`, and returns the events —
        appeared, turn changed, gone — that the feed and the alerting use.
        """
        if not self.ok:
            for item in items:
                item.setdefault("first_seen", item.get("created") or now)
                item.setdefault("turn_since", (item.get("verdict") or {}).get("waiting_since"))
            return []

        stamp = int(now)
        events: list[dict] = []
        try:
            with self.lock, self._db() as db:
                known = {r["id"]: r for r in db.execute(
                    "SELECT id, first_seen, turn, turn_since, last_updated FROM seen")}
                live = set()

                for item in items:
                    key = item["id"]
                    live.add(key)
                    verdict = item.get("verdict") or {}
                    turn = verdict.get("turn")
                    row = known.get(key)

                    if row is None:
                        first_seen = int(item.get("created") or now)
                        turn_since = stamp
                        events.append({"ts": stamp, "id": key, "kind": "appeared",
                                       "detail": turn})
                    else:
                        first_seen = row["first_seen"]
                        turn_since = row["turn_since"] or stamp
                        if row["turn"] != turn:
                            turn_since = stamp
                            events.append({"ts": stamp, "id": key, "kind": "turn",
                                           "detail": f"{row['turn']}→{turn}"})
                        elif (row["last_updated"] or 0) < int(item.get("updated") or 0):
                            events.append({"ts": stamp, "id": key, "kind": "activity",
                                           "detail": turn})

                    item["first_seen"] = first_seen
                    # GitHub's own timestamp is the better answer when it is the
                    # *older* one: it knows a thread has been open since March,
                    # while this table only knows since it was first polled.
                    github_since = verdict.get("waiting_since") or 0
                    item["turn_since"] = (min(turn_since, github_since)
                                          if github_since else turn_since)

                    db.execute(
                        "INSERT INTO seen (id, first_seen, turn, turn_since, last_seen, last_updated) "
                        "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                        "turn=excluded.turn, turn_since=excluded.turn_since, "
                        "last_seen=excluded.last_seen, last_updated=excluded.last_updated",
                        (key, first_seen, turn, turn_since, stamp,
                         int(item.get("updated") or 0)))

                for key, row in known.items():
                    if key in live:
                        continue
                    events.append({"ts": stamp, "id": key, "kind": "gone",
                                   "detail": row["turn"]})
                    db.execute("DELETE FROM seen WHERE id = ?", (key,))

                for event in events:
                    db.execute("INSERT INTO events (ts, id, kind, detail) VALUES (?,?,?,?)",
                               (event["ts"], event["id"], event["kind"], event["detail"]))
        except sqlite3.Error:
            return []
        return events

    def recent_events(self, since: float, limit: int = 200) -> list[dict]:
        if not self.ok:
            return []
        try:
            with self.lock, self._db() as db:
                rows = db.execute(
                    "SELECT ts, id, kind, detail FROM events WHERE ts >= ? "
                    "ORDER BY ts DESC LIMIT ?", (int(since), limit)).fetchall()
        except sqlite3.Error:
            return []
        return [dict(r) for r in rows]
