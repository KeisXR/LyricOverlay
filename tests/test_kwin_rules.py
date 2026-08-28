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


def test_legacy_uuid_rule_is_retired_when_disabling(tmp_path, monkeypatch):
    target = tmp_path / "kwinrulesrc"
    legacy_id = "3f2b9c1a-7d0e-4a55-9b21-1c8e5f60d4aa"
    values = {
        ("General", "rules"): f"{legacy_id},user-rule",
        (legacy_id, "Description"): kwin.RULE_DESCRIPTION,
        ("user-rule", "Description"): "My own window rule",
    }
    writes = []
    deleted = []
    monkeypatch.setattr(kwin, "_find_tools", lambda: ("read", "write"))
    monkeypatch.setattr(
        kwin,
        "_read_value",
        lambda _reader, _target, group, key: values.get((group, key), ""),
    )
    monkeypatch.setattr(
        kwin,
        "_write_value",
        lambda _writer, _target, group, key, value: writes.append(
            (group, key, value)
        ),
    )
    monkeypatch.setattr(
        kwin,
        "_delete_group",
        lambda _writer, _target, group: deleted.append(group),
    )
    monkeypatch.setattr(kwin, "reload_kwin_config", lambda: True)

    rule_id, reloaded = kwin.set_rule_enabled(False, path=target)

    assert rule_id == kwin.RULE_ID
    assert reloaded
    assert (legacy_id, "Enabled", "false") in writes
    assert deleted == [legacy_id]
    assert (kwin.RULE_ID, "Enabled", "false") in writes

    general_rules = [
        value
        for group, key, value in writes
        if group == "General" and key == "rules"
    ][-1].split(",")
    assert legacy_id not in general_rules
    assert general_rules.count(kwin.RULE_ID) == 1
    # A rule the user wrote themselves is never inspected away or rewritten.
    assert "user-rule" in general_rules
    assert all(group != "user-rule" for group, _, _ in writes)
    assert "user-rule" not in deleted


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
