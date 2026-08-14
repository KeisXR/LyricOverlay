"""Safely manage the single KWin rule owned by Lyricaod.

The module uses KDE's ``kreadconfig``/``kwriteconfig`` commands instead of
parsing and serialising the user's entire ``kwinrulesrc`` file. Only the fixed
Lyricaod-owned group is modified; unrelated rules and comments remain under
KConfig's control.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path


logger = logging.getLogger(__name__)

RULE_DESCRIPTION = "Lyricaod overlay"
RULE_ID = "lyricaod-overlay-v1"
APP_ID = "lyricaod"
WINDOW_TITLE = "Lyricaod Overlay"


def config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "kwinrulesrc"


def _find_tools() -> tuple[str | None, str | None]:
    reader = shutil.which("kreadconfig6") or shutil.which("kreadconfig5")
    writer = shutil.which("kwriteconfig6") or shutil.which("kwriteconfig5")
    return reader, writer


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _read_value(reader: str, target: Path, group: str, key: str) -> str:
    result = _run(
        [reader, "--file", str(target), "--group", group, "--key", key]
    )
    return result.stdout.strip()


def _write_value(
    writer: str, target: Path, group: str, key: str, value: str
) -> None:
    _run(
        [
            writer,
            "--file",
            str(target),
            "--group",
            group,
            "--key",
            key,
            str(value),
        ]
    )


def _parse_rules(value: str) -> list[str]:
    rules: list[str] = []
    for rule in value.split(","):
        stripped = rule.strip()
        if stripped and stripped not in rules:
            rules.append(stripped)
    return rules


def set_rule_enabled(
    enabled: bool,
    *,
    path: Path | None = None,
    reload_kwin: bool = True,
) -> tuple[str | None, bool]:
    """Create or update only Lyricaod's dedicated KWin rule."""
    target = Path(path) if path is not None else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    reader, writer = _find_tools()
    if not reader or not writer:
        logger.warning("KDE kreadconfig/kwriteconfig tools are unavailable")
        return None, False

    backup = target.with_name(target.name + ".lyricaod-backup")
    target_existed = target.exists()
    try:
        if target_existed:
            shutil.copy2(target, backup)

        raw_rules = _read_value(reader, target, "General", "rules")
        rules = _parse_rules(raw_rules)
        if RULE_ID not in rules:
            rules.append(RULE_ID)

        values = {
            "Description": RULE_DESCRIPTION,
            "Enabled": "true" if enabled else "false",
            "above": "true",
            "aboverule": "2",
            "layer": "above",
            "layerrule": "2",
            "skiptaskbar": "true",
            "skiptaskbarrule": "2",
            "skippager": "true",
            "skippagerrule": "2",
            "skipswitcher": "true",
            "skipswitcherrule": "2",
            "title": WINDOW_TITLE,
            "titlematch": "1",
            "wmclass": APP_ID,
            "wmclassmatch": "1",
            "desktopfile": APP_ID,
            "desktopfilerule": "2",
        }
        for key, value in values.items():
            _write_value(writer, target, RULE_ID, key, value)

        _write_value(writer, target, "General", "rules", ",".join(rules))
        _write_value(writer, target, "General", "count", str(len(rules)))
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("KWin rule update failed: %s", exc)
        try:
            if backup.exists():
                os.replace(backup, target)
            elif not target_existed:
                target.unlink(missing_ok=True)
        except OSError as restore_error:
            logger.error("Unable to restore KWin rules backup: %s", restore_error)
        return None, False
    finally:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass

    reloaded = reload_kwin_config() if reload_kwin else False
    return RULE_ID, reloaded


def reload_kwin_config() -> bool:
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if not qdbus:
        return False
    try:
        result = subprocess.run(
            [qdbus, "org.kde.KWin", "/KWin", "reconfigure"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return result.returncode == 0
