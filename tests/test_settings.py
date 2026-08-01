"""Settings: what a web page is allowed to change, and what it must not break.

Everything here is enforced server-side on purpose. The page renders the same
bounds, but a page cannot be trusted to be the thing enforcing them.
"""

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))

import settings as settings_mod  # noqa: E402


@pytest.fixture
def store(tmp_path):
    return settings_mod.Settings(str(tmp_path / "settings.json"))


@pytest.fixture
def cfg():
    return {"ignore_days": 30, "done_days": 7, "poll_seconds": 300,
            "telegram": {"enabled": False, "digest_hour": 10}}


def test_an_override_reaches_the_live_config(store, cfg):
    store.apply_to(cfg)
    applied, rejected = store.update({"ignore_days": 45}, cfg)
    assert applied == {"ignore_days": 45}
    assert rejected == []
    assert cfg["ignore_days"] == 45


def test_dotted_keys_land_in_their_section(store, cfg):
    store.apply_to(cfg)
    store.update({"telegram.digest_hour": 9}, cfg)
    assert cfg["telegram"]["digest_hour"] == 9
    assert cfg["telegram"]["enabled"] is False


def test_typing_the_config_value_back_is_not_an_override(store, cfg):
    """Otherwise the page claims a difference nobody can find."""
    store.apply_to(cfg)
    store.update({"ignore_days": 45}, cfg)
    store.update({"ignore_days": 30}, cfg)
    assert store.values == {}
    assert cfg["ignore_days"] == 30


def test_dropping_an_override_restores_the_config_value(store, cfg):
    """Applying only the overrides left the old value behind for ever."""
    store.apply_to(cfg)
    store.update({"ignore_days": 90}, cfg)
    store.reset(cfg)
    store.apply_to(cfg)
    assert cfg["ignore_days"] == 30


def test_out_of_range_is_refused_with_a_reason(store, cfg):
    store.apply_to(cfg)
    applied, rejected = store.update({"poll_seconds": 5}, cfg)
    assert applied == {}
    assert "от 60 до 3600" in rejected[0]
    assert cfg["poll_seconds"] == 300


def test_a_poll_interval_of_zero_cannot_be_set(store, cfg):
    """It would spin against GitHub until the hourly budget is gone."""
    store.apply_to(cfg)
    _, rejected = store.update({"poll_seconds": 0}, cfg)
    assert rejected


def test_unknown_keys_are_refused(store, cfg):
    """Nothing outside the schema — the token and paths are not editable here."""
    store.apply_to(cfg)
    applied, rejected = store.update({"token": "ghp_whatever"}, cfg)
    assert applied == {}
    assert rejected
    assert "token" not in store.values


def test_garbage_does_not_become_a_number(store, cfg):
    store.apply_to(cfg)
    applied, rejected = store.update({"ignore_days": "хрень"}, cfg)
    assert applied == {}
    assert cfg["ignore_days"] == 30


def test_booleans_accept_strings_from_a_form(store, cfg):
    store.apply_to(cfg)
    store.update({"telegram.enabled": "true"}, cfg)
    assert cfg["telegram"]["enabled"] is True


def test_a_good_change_survives_a_bad_one_in_the_same_call(store, cfg):
    store.apply_to(cfg)
    applied, rejected = store.update({"ignore_days": 60, "poll_seconds": 1}, cfg)
    assert applied == {"ignore_days": 60}
    assert len(rejected) == 1
    assert cfg["ignore_days"] == 60


def test_overrides_survive_a_restart(tmp_path, cfg):
    path = str(tmp_path / "settings.json")
    first = settings_mod.Settings(path)
    first.apply_to(cfg)
    first.update({"ignore_days": 77}, cfg)

    fresh_cfg = dict(cfg, ignore_days=30)
    second = settings_mod.Settings(path)
    second.apply_to(fresh_cfg)
    assert fresh_cfg["ignore_days"] == 77


def test_current_reports_config_values_where_there_is_no_override(store, cfg):
    store.apply_to(cfg)
    store.update({"ignore_days": 45}, cfg)
    got = store.current(cfg)
    assert got["ignore_days"] == 45
    assert got["done_days"] == 7
    assert got["telegram.digest_hour"] == 10


def test_every_field_has_what_the_page_needs_to_render_it():
    for field in settings_mod.SCHEMA:
        assert field["section"] and field["title"] and field["hint"]
        assert field["type"] in ("int", "bool")
        if field["type"] == "int":
            assert field["min"] < field["max"]
            assert field["min"] <= field["default"] <= field["max"]


def test_a_broken_settings_file_does_not_stop_the_service(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert settings_mod.Settings(str(path)).values == {}
