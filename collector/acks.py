"""Snoozing and muting, stored next to the history.

Any board that cannot be quietened stops being read: one item you have decided
not to deal with this week sits at the top and slowly teaches you to skip the
whole list. Two ways to quieten one:

* **snooze** — hide until a chosen time. The point of a snooze is "not now",
  not "never", so it is cancelled when the item genuinely changes: something
  snoozed on Monday that gets a real answer on Tuesday comes back.

  "Genuinely" is the whole difficulty. GitHub's `updatedAt` moves for anything
  at all — a CI bot posting a build link, your own push, a label — so using it
  meant a snooze survived about four minutes on an active pull request. What
  counts instead is a *pulse*: threads still waiting on you, who has approved
  or blocked, and the last comment by a human who is not you. A bot cannot
  cancel a snooze, and neither can you by pushing.
* **mute** — hide until something actually happens there. For a pull request
  you have decided not to think about until someone else moves.
* **forever** — hide and keep it hidden, whatever happens. For the 2019 pull
  request that is never going to be merged: activity on it is not news, it is
  the reason it keeps coming back.

The difference matters enough to be two buttons rather than one: "muted" that
silently returns on a bot comment and "muted" that never returns are different
promises, and guessing wrong is how a board loses trust.

Both are keyed by item id (`owner/repo#123`), which survives restarts, unlike
GraphQL node ids.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


def pulse(item: dict, login: str) -> str:
    """What has to change before a hidden card is worth showing again.

    Deliberately not `updatedAt`: that moves for a CI comment, a label, or your
    own push, none of which are reasons to interrupt you. This is the shortest
    description of "somebody else did something that matters".
    """
    threads = sum(1 for t in item.get("threads") or []
                  if not t.get("resolved") and not t.get("outdated")
                  and t.get("last_by") != login)
    last = item.get("last_comment") or {}
    human = ""
    if last and not last.get("bot") and last.get("login") != login:
        human = f"{last.get('login')}@{int(last.get('at') or 0)}"
    return "|".join([
        str(threads),
        ",".join(item.get("approvals") or []),
        ",".join(item.get("blocking") or []),
        human,
    ])


class Acks:
    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.lock = threading.Lock()
        self.data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def snooze(self, item_id: str, seconds: int, pulse: str = "") -> None:
        """Hide until `seconds` from now, or until the item really moves."""
        with self.lock:
            self.data[item_id] = {"kind": "snooze", "until": time.time() + seconds,
                                  "at": time.time(), "pulse": pulse}
            self._save()

    def mute(self, item_id: str, pulse: str = "", forever: bool = False) -> None:
        with self.lock:
            self.data[item_id] = {"kind": "forever" if forever else "mute",
                                  "until": 0, "at": time.time(), "pulse": pulse}
            self._save()

    def clear(self, item_id: str) -> None:
        with self.lock:
            if self.data.pop(item_id, None) is not None:
                self._save()

    def apply(self, items: list[dict], now: float, login: str = "") -> int:
        """Mark hidden items and drop entries that have expired or moved.

        Returns how many are currently hidden, so the header can offer to show
        them — a hidden item that leaves no trace is a lost item.
        """
        hidden = 0
        changed = False
        with self.lock:
            for item in items:
                entry = self.data.get(item["id"])
                if not entry:
                    continue
                # "Forever" means exactly that: activity on a dead pull request
                # is not news, it is the reason it kept coming back.
                if entry["kind"] == "forever":
                    item["hidden"] = "forever"
                    item["hidden_until"] = 0
                    hidden += 1
                    continue
                # The escape hatch: real movement wins over a snooze.
                beat = pulse(item, login)
                if not entry.get("pulse"):
                    # Written before pulses existed. Adopt whatever it looks
                    # like now rather than treating "no baseline" as "never
                    # changes", which would quietly make every old mute
                    # permanent.
                    entry["pulse"] = beat
                    changed = True
                elif beat != entry["pulse"]:
                    self.data.pop(item["id"], None)
                    changed = True
                    continue
                if entry["kind"] == "snooze" and entry.get("until", 0) <= now:
                    self.data.pop(item["id"], None)
                    changed = True
                    continue
                item["hidden"] = entry["kind"]
                item["hidden_until"] = entry.get("until", 0)
                hidden += 1
            for key in [k for k, v in self.data.items()
                        if v["kind"] == "snooze" and v.get("until", 0) <= now - 86400]:
                self.data.pop(key, None)
                changed = True
            if changed:
                self._save()
        return hidden

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.data)
