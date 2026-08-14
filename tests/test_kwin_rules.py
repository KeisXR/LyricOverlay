"""Tests for safely scoped KWin rule updates."""

import importlib.util
import subprocess
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "ui" / "kwin_rules.py"
spec = importlib.util.spec_from_file_location("kwin_under_test", MODULE_PATH)
kwin = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(kwin)


def test_rule_update_is_idempotent_and_scoped(tmp_path, monkeypatch):
    target = tmp_path / "kwinrulesrc"
    target.write_text("# keep\n", encoding="utf-8")
    writes = []
    monkeypatch.setattr(kwin, "_find_tools", lambda: ("read", "write"))
    monkeypatch.setattr(
        kwin,
        "_read_value",
        lambda *_args: "other-rule,lyricaod-overlay-v1",
    )
    monkeypatch.setattr(
        kwin,
        "_write_value",
        lambda _writer, _target, group, key, value: writes.append(
            (group, key, value)
        ),
    )
    monkeypatch.setattr(kwin, "reload_kwin_config", lambda: True)

    rule_id, reloaded = kwin.set_rule_enabled(True, path=target)

    assert rule_id == kwin.RULE_ID
    assert reloaded
    assert all(group in {kwin.RULE_ID, "General"} for group, _, _ in writes)
    general_rules = [
        value
        for group, key, value in writes
        if group == "General" and key == "rules"
    ][-1]
    assert general_rules.split(",").count(kwin.RULE_ID) == 1
    assert target.read_text(encoding="utf-8") == "# keep\n"


def test_missing_kconfig_tools_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(kwin, "_find_tools", lambda: (None, None))
    assert kwin.set_rule_enabled(True, path=tmp_path / "kwinrulesrc") == (
        None,
        False,
    )


def test_failure_restores_original_file(tmp_path, monkeypatch):
    target = tmp_path / "kwinrulesrc"
    target.write_text("original", encoding="utf-8")
    monkeypatch.setattr(kwin, "_find_tools", lambda: ("read", "write"))
    monkeypatch.setattr(kwin, "_read_value", lambda *_args: "other")

    def fail(_writer, _target, _group, _key, _value):
        target.write_text("partial", encoding="utf-8")
        raise subprocess.CalledProcessError(1, ["kwriteconfig"])

    monkeypatch.setattr(kwin, "_write_value", fail)

    assert kwin.set_rule_enabled(True, path=target) == (None, False)
    assert target.read_text(encoding="utf-8") == "original"
