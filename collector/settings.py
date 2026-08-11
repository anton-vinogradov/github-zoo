"""Settings a person can change without opening a JSON file over ssh.

The config file stays the source of defaults — it is where a fresh install
gets its values, and it holds the things that must not be editable from a web
page (the token, the Telegram chat, the paths). Anything tuned by looking at
the board instead lives here: thresholds, how often to poll, what counts as
noise. Overrides are kept in one small file and applied on top of the config,
so the config is never rewritten and an override can always be undone.

Every field declares its own bounds, and they are enforced server-side. A
`poll_seconds` of 0 would spin against GitHub until the hourly budget is gone,
and nothing in a browser can be trusted to prevent that.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

SCHEMA = [
    {
        "section": "Что показывать",
        "key": "ignore_days",
        "title": "Прятать без движения дольше",
        "unit": "дней",
        "type": "int", "min": 1, "max": 3650, "default": 30,
        "hint": "Карточка уходит в «Старее месяца». Не теряется: счётчик виден, "
                "группа раскрывается кликом. Ради этого порога всё и затевалось — "
                "без него наверху висел запрос ревью 2019 года.",
    },
    {
        "section": "Что показывать",
        "key": "done_days",
        "title": "Показывать сделанное за",
        "unit": "дней",
        "type": "int", "min": 0, "max": 365, "default": 7,
        "hint": "Смерженное и закрытое в группе «Решено». 0 — не показывать вовсе. "
                "Заодно к этим карточкам цепляются уведомления о смерженных PR.",
    },
    {
        "section": "Что показывать",
        "key": "notifications_include_ci",
        "title": "Пускать уведомления о CI",
        "type": "bool", "default": False,
        "hint": "На активном апстриме это 200 уведомлений из 280. Включать стоит, "
                "только чтобы что-то поискать.",
    },
    {
        "section": "Что показывать",
        "key": "repush_is_your_move",
        "title": "Новые коммиты после твоего ревью — твой ход",
        "type": "bool", "default": False,
        "hint": "Автор запушил после того, как вы отревьюили. Иногда это ответ на "
                "ваши замечания, но чаще ребейз или фиксап, поэтому по умолчанию "
                "выключено — карточка остаётся в «Ждём других». Неотвеченные треды "
                "в таком PR вы увидите в любом случае: это отдельное правило.",
    },
    {
        "section": "Когда торопить",
        "key": "stale_days",
        "title": "Твой ход «залежался» через",
        "unit": "дней",
        "type": "int", "min": 1, "max": 365, "default": 14,
        "hint": "Подсвечивает возраст и попадает в счётчик «залежалось».",
    },
    {
        "section": "Когда торопить",
        "key": "rot_days",
        "title": "Чужой ход «протух» через",
        "unit": "дней",
        "type": "int", "min": 1, "max": 365, "default": 30,
        "hint": "Столько дней без ответа от других — повод пнуть.",
    },
    {
        "section": "Когда торопить",
        "key": "unreviewed_days",
        "title": "Свой PR без ревьюера — просить ревью через",
        "unit": "дней",
        "type": "int", "min": 1, "max": 365, "default": 3,
        "hint": "PR, которого никто не смотрит дольше этого, переходит в «Твой ход» "
                "с подсказкой попросить ревью.",
    },
    {
        "section": "Опрос",
        "key": "poll_seconds",
        "title": "Спрашивать GitHub каждые",
        "unit": "секунд",
        "type": "int", "min": 60, "max": 3600, "default": 300,
        "hint": "Дорогая половина данных тянется только для изменившегося, "
                "поэтому обычный цикл почти бесплатный. Ниже 60 с нельзя: "
                "часовой лимит GitHub не резиновый.",
    },
    {
        "section": "Telegram",
        "key": "telegram.enabled",
        "title": "Слать уведомления",
        "type": "bool", "default": False,
        "hint": "Сообщение, когда что-то переходит в твой ход, и сводка раз в день. "
                "Токен и чат задаются только в конфиге на сервере.",
    },
    {
        "section": "Telegram",
        "key": "watch_activity",
        "title": "Сообщать об апрувах и комментариях",
        "type": "bool", "default": True,
        "hint": "«Твой PR одобрили — можно мержить» и «в твоём PR написали». Ни то, "
                "ни другое не делает карточку твоим ходом, поэтому иначе проходит "
                "молча. Запрошенные правки сюда не попадают: это уже твой ход и о нём "
                "приходит основное уведомление.",
    },
    {
        "section": "Telegram",
        "key": "watch_forks",
        "title": "Сообщать о новых форках",
        "type": "bool", "default": True,
        "hint": "Форк — единственное событие, о котором GitHub вообще не уведомляет: "
                "ни письма, ни строки в инбоксе. Первый запуск ничего не шлёт, только "
                "запоминает то, что уже есть.",
    },
    {
        "section": "Telegram",
        "key": "watch_stars",
        "title": "Сообщать о новых звёздах",
        "type": "bool", "default": True,
        "hint": "С именем того, кто поставил, и суммарным числом звёзд по всем "
                "проектам. Убыль звезды молча обновляет счётчик: сделать с этим "
                "нечего, а сообщение только расстроит.",
    },
    {
        "section": "Telegram",
        "key": "telegram.digest_hour",
        "title": "Час ежедневной сводки",
        "type": "int", "min": 0, "max": 23, "default": 10,
        "hint": "Напоминание о том, что всё ещё ждёт хода.",
    },
]

BY_KEY = {field["key"]: field for field in SCHEMA}


class Settings:
    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.lock = threading.Lock()
        self.values: dict = {}
        # What the config file said before any override touched it. Captured
        # once, because apply_to writes into the same dict: comparing against
        # the live config would mean comparing a value with itself.
        self.baseline: dict | None = None
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.values = {k: v for k, v in data.items() if k in BY_KEY}
        except (OSError, ValueError):
            self.values = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.values, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def apply_to(self, cfg: dict) -> None:
        """Write the overrides into the live config, in place.

        In place because everything downstream reads the same dict — replacing
        it would leave the poll loop holding the old one.
        """
        with self.lock:
            if self.baseline is None:
                self.baseline = {f["key"]: _read(f["key"], cfg, f) for f in SCHEMA}
            # Reset to the config's own values first, then layer the overrides
            # back on. Applying only the overrides meant a dropped one left its
            # old value behind in the live config for ever.
            for key, value in self.baseline.items():
                _write(key, cfg, value)
            for key, value in self.values.items():
                _write(key, cfg, value)

    def current(self, cfg: dict) -> dict:
        """What every field is set to right now, override or config or default."""
        out = {}
        for field in SCHEMA:
            key = field["key"]
            head, _, tail = key.partition(".")
            source = (cfg.get(head) or {}) if tail else cfg
            name = tail or head
            out[key] = source.get(name, field["default"])
        return out

    def update(self, changes: dict, cfg: dict) -> tuple[dict, list[str]]:
        """Validate and store. Returns (applied, rejected-with-reasons)."""
        applied: dict = {}
        rejected: list[str] = []
        for key, raw in (changes or {}).items():
            field = BY_KEY.get(key)
            if not field:
                rejected.append(f"{key}: нет такой настройки")
                continue
            value, why = _coerce(field, raw)
            if why:
                rejected.append(f"{field['title']}: {why}")
                continue
            applied[key] = value

        if applied:
            with self.lock:
                self.values.update(applied)
                # Typing the config's own value back in is not an override, and
                # counting it as one leaves the page claiming a difference that
                # nobody can find.
                base = self.baseline or {}
                for key, value in list(applied.items()):
                    if value == base.get(key, _read(key, cfg, BY_KEY[key])):
                        self.values.pop(key, None)
                self._save()
            self.apply_to(cfg)
        return applied, rejected

    def reset(self, cfg: dict) -> None:
        with self.lock:
            self.values = {}
            self._save()


def _write(key: str, cfg: dict, value) -> None:
    head, _, tail = key.partition(".")
    if tail:
        cfg.setdefault(head, {})[tail] = value
    else:
        cfg[head] = value


def _read(key: str, cfg: dict, field: dict):
    """This field's value in the given config, dotted keys included."""
    head, _, tail = key.partition(".")
    source = (cfg.get(head) or {}) if tail else cfg
    return source.get(tail or head, field["default"])


def _coerce(field: dict, raw) -> tuple[object, str]:
    if field["type"] == "bool":
        if isinstance(raw, bool):
            return raw, ""
        if isinstance(raw, str) and raw.lower() in ("true", "false"):
            return raw.lower() == "true", ""
        return None, "ожидается да/нет"

    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, "ожидается число"
    if value < field["min"] or value > field["max"]:
        return None, f"допустимо от {field['min']} до {field['max']}"
    return value, ""
