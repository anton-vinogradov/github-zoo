"""Snoozing and muting, stored next to the history.

Any board that cannot be quietened stops being read: one item you have decided
not to deal with this week sits at the top and slowly teaches you to skip the
whole list. Two ways to quieten one:

* **snooze** — hide until a chosen time. The point of a snooze is "not now",
  not "never", so it is cancelled the moment the item genuinely changes:
  something snoozed on Monday that gets a new comment on Tuesday comes back.
* **mute** — hide until it changes. For the pull request from 2019 that is
  never going to be merged and is not worth seeing again.

Both are keyed by item id (`owner/repo#123`), which survives restarts, unlike
GraphQL node ids.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


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

    def snooze(self, item_id: str, seconds: int, updated: float = 0.0) -> None:
        """Hide until `seconds` from now, or until the item moves."""
        with self.lock:
            self.data[item_id] = {"kind": "snooze", "until": time.time() + seconds,
                                  "at": time.time(), "updated": updated}
            self._save()

    def mute(self, item_id: str, updated: float = 0.0) -> None:
        with self.lock:
            self.data[item_id] = {"kind": "mute", "until": 0, "at": time.time(),
                                  "updated": updated}
            self._save()

    def clear(self, item_id: str) -> None:
        with self.lock:
            if self.data.pop(item_id, None) is not None:
                self._save()

    def apply(self, items: list[dict], now: float) -> int:
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
                # The escape hatch: real activity always wins over a snooze.
                if (item.get("updated") or 0) > (entry.get("updated") or 0) + 1:
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
