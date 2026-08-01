"""Telegram alerting for github-zoo.

The dashboard only helps while someone is looking at it, and the whole point of
this project is the things you stopped looking at. So the interesting events
get pushed:

* something new landed in your court — a review request, an answer to your
  question, a red build on your branch;
* a morning digest of what is still waiting, because a single item that has sat
  in "your move" for a week never re-announces itself otherwise.

Delivery reuses telegram.sh: on this host Telegram is only reachable through a
local SOCKS proxy, and telegram.sh already holds that setting plus a
store-and-forward queue for when the proxy is down.

Alerting is edge-triggered per item. A pull request that has been waiting for
your answer since Tuesday must not send a message every poll cycle, so state
lives on disk and only transitions are announced.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime

import secrets

KIND_ICON = {"pr": "🔀", "issue": "🐛", "discussion": "💬"}


class Alerts:
    def __init__(self, cfg: dict):
        self.cfg = cfg.get("telegram") or {}
        self.enabled = bool(self.cfg.get("enabled") and self.cfg.get("chats"))
        self.binary = self.cfg.get("telegram_bin", "/opt/telegram.sh-repo/telegram")
        self.spool = self.cfg.get("spool", "")
        self.dashboard_url = (self.cfg.get("dashboard_url") or "").rstrip("/")
        self.digest_hour = self.cfg.get("digest_hour", 10)
        self.state_path = self.cfg.get("state_file",
                                       "/var/lib/github-zoo/alerts-state.json")
        # The first poll after a restart knows nothing about what was already
        # announced, and would otherwise report every open item at once.
        # Seeding means the first pass records, and announcing starts after it.
        self.seeded = False
        self.lock = threading.Lock()
        self.announced: dict[str, str] = {}
        self.last_digest = 0.0
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(open(self.state_path, encoding="utf-8").read())
            self.announced = data.get("announced") or {}
            self.last_digest = data.get("last_digest") or 0
            self.seeded = bool(data.get("seeded"))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"announced": self.announced, "seeded": self.seeded,
                           "last_digest": self.last_digest}, fh, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    # ---------- delivery ----------

    def _send(self, text: str) -> bool:
        token = secrets.load(self.cfg, "token") or os.environ.get("GITHUB_ZOO_TG_TOKEN", "")
        chats = self.cfg.get("chats") or []
        if not token or not chats or not text or not os.path.exists(self.binary):
            return False
        cmd = [self.binary, "-t", token, "-a", "3", "-p"]
        for chat in chats:
            cmd += ["-c", str(chat)]
        if self.spool:
            cmd += ["-q", self.spool]
        # Plain text on purpose: repository and branch names are full of
        # underscores and brackets, and one unbalanced Markdown entity makes
        # Telegram reject the whole message.
        cmd += [text]
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=120, text=True)
            return done.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False  # a failed notification must never disturb polling

    # ---------- edge detection ----------

    def process(self, items: list[dict], summary: dict) -> None:
        """Announce items that have just become your move."""
        if not self.enabled:
            return

        current = {}
        for item in items:
            verdict = item.get("verdict") or {}
            if verdict.get("turn") == "you" and not item.get("hidden"):
                current[item["id"]] = _fingerprint(item, verdict)

        with self.lock:
            fresh = [i for i in items
                     if i["id"] in current
                     and self.announced.get(i["id"]) != current[i["id"]]]
            cleared = [key for key in self.announced if key not in current]

            for key in cleared:
                self.announced.pop(key, None)
            for item in items:
                if item["id"] in current:
                    self.announced[item["id"]] = current[item["id"]]

            first_run = not self.seeded
            self.seeded = True
            self._save()

        if first_run:
            self._send(f"🐙 github-zoo запущен. Сейчас твоего хода ждут: "
                       f"{summary.get('you', 0)}." + self._footer())
            return

        if fresh:
            self._send(self._text(fresh, summary))

        self._maybe_digest(items, summary)

    def _text(self, fresh: list[dict], summary: dict) -> str:
        head = ("🐙 Твой ход" if len(fresh) == 1
                else f"🐙 Твой ход: {len(fresh)} новых")
        lines = [head, ""]
        for item in sorted(fresh, key=lambda i: -(i.get("verdict") or {}).get("priority", 0))[:10]:
            lines.extend(self._line(item))
        if len(fresh) > 10:
            lines.append(f"…и ещё {len(fresh) - 10}")
        lines.append("")
        lines.append(f"всего ждёт тебя: {summary.get('you', 0)}")
        return "\n".join(lines) + self._footer()

    def _line(self, item: dict) -> list[str]:
        verdict = item.get("verdict") or {}
        why = "; ".join(r["text"] for r in (verdict.get("reasons") or [])[:2])
        icon = KIND_ICON.get(item.get("kind"), "•")
        return [f"{icon} {item['repo']}#{item['number']} — {item['title'][:70]}",
                f"   {why}",
                f"   {item['url']}", ""]

    def forks(self, forks: list[dict]) -> None:
        """Announce forks of your own repositories.

        Sent unconditionally rather than edge-tracked like the rest: the store
        only ever hands over forks it has not seen before, and a fork happens
        once. There is nothing to act on, which is why it is a message and not
        a card — somebody took your project, and that is worth knowing the day
        it happens rather than months later by accident.
        """
        if not self.enabled or not forks:
            return
        head = ("🍴 Твой проект форкнули" if len(forks) == 1
                else f"🍴 Новых форков: {len(forks)}")
        lines = [head, ""]
        for fork in forks[:10]:
            lines.append(f"{fork['name']} — форк {fork['parent']}")
            stars = f" · ★{fork['stars']}" if fork.get("stars") else ""
            lines.append(f"   автор {fork['owner']}{stars}")
            lines.append(f"   {fork['url']}")
            lines.append("")
        if len(forks) > 10:
            lines.append(f"…и ещё {len(forks) - 10}")
        self._send("\n".join(lines) + self._footer())

    def stars(self, gained: list[dict], total: int) -> None:
        """Announce new stars, with who and the running total.

        The total is the point: one more star means little on its own, and
        "36 across your projects" is the number that says whether anything is
        actually growing.
        """
        if not self.enabled or not gained:
            return
        added = sum(g["added"] for g in gained)
        lines = ["⭐ Новая звезда" if added == 1 else f"⭐ Новых звёзд: {added}", ""]
        for hit in gained[:10]:
            who = ", ".join(hit["who"]) if hit.get("who") else "кто-то"
            plus = "" if hit["added"] == 1 else f" (+{hit['added']})"
            lines.append(f"{hit['repo']} — теперь {hit['count']}{plus}")
            lines.append(f"   поставил {who}")
            if hit.get("url"):
                lines.append(f"   {hit['url']}/stargazers")
            lines.append("")
        if len(gained) > 10:
            lines.append(f"…и ещё {len(gained) - 10} репозиториев")
        lines.append(f"Всего по проектам: {total}")
        self._send("\n".join(lines) + self._footer())

    def _maybe_digest(self, items: list[dict], summary: dict) -> None:
        """One reminder a day about what is still sitting there."""
        now = time.time()
        hour = datetime.fromtimestamp(now).hour
        if hour != self.digest_hour or now - self.last_digest < 20 * 3600:
            return
        waiting = [i for i in items
                   if (i.get("verdict") or {}).get("turn") == "you" and not i.get("hidden")]
        if not waiting:
            self.last_digest = now
            self._save()
            return
        waiting.sort(key=lambda i: -(i.get("verdict") or {}).get("waiting_days", 0))
        lines = [f"🐙 Ждут твоего хода: {len(waiting)}", ""]
        for item in waiting[:12]:
            verdict = item["verdict"]
            lines.append(f"{KIND_ICON.get(item['kind'], '•')} {item['repo']}#{item['number']}"
                         f" — {int(verdict.get('waiting_days', 0))} д — {item['title'][:60]}")
        if len(waiting) > 12:
            lines.append(f"…и ещё {len(waiting) - 12}")
        if summary.get("stale"):
            lines.append("")
            lines.append(f"из них залежались: {summary['stale']}")
        self._send("\n".join(lines) + self._footer())
        with self.lock:
            self.last_digest = now
            self._save()

    def _footer(self) -> str:
        return f"\n\nдашборд: {self.dashboard_url}" if self.dashboard_url else ""

    # ---------- diagnostics ----------

    def test(self) -> tuple[bool, str]:
        if not self.cfg.get("chats"):
            return False, "не задан chat в конфиге"
        if not os.path.exists(self.binary):
            return False, f"нет {self.binary}"
        if not secrets.load(self.cfg, "token"):
            return False, f"токен: {secrets.describe(self.cfg, 'token')}"
        sent = self._send("🐙 github-zoo: проверка связи — алерты работают" + self._footer())
        return (True, "отправлено") if sent else (False, "telegram.sh вернул ошибку")


def _fingerprint(item: dict, verdict: dict) -> str:
    """What must change before this counts as a new thing to announce.

    Just "is it my move" is too coarse — a pull request that goes from one
    unanswered thread to three has genuinely changed and deserves a nudge —
    while including the timestamp would re-announce on every poll.
    """
    codes = ",".join(sorted(r["code"] for r in verdict.get("reasons") or []))
    threads = sum(1 for t in item.get("threads") or []
                  if not t.get("resolved") and not t.get("outdated"))
    return f"{codes}|{threads}|{item.get('comments', 0)}"
