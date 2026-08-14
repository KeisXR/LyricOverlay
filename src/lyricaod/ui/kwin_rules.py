"""KWin Window Rule helpers for the Wayland overlay."""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4


RULE_DESCRIPTION = "Lyricaod overlay"
APP_ID = "lyricaod"
WINDOW_TITLE = "Lyricaod Overlay"


def config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "kwinrulesrc"


def read_config(path: Path | None = None) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(
        delimiters=("="),
        interpolation=None,
        strict=False,
    )
    parser.optionxform = str
    target = path or config_path()
    if target.exists():
        parser.read(target, encoding="utf-8")
    return parser


def write_config(parser: configparser.ConfigParser, path: Path | None = None) -> None:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copy2(target, backup)

    with target.open("w", encoding="utf-8") as fh:
        parser.write(fh, space_around_delimiters=False)


def general_rules(parser: configparser.ConfigParser) -> list[str]:
    if not parser.has_section("General"):
        parser.add_section("General")

    rules = parser.get("General", "rules", fallback="").strip()
    if rules:
        return [rule for rule in rules.split(",") if rule]

    count = parser.getint("General", "count", fallback=0)
    return [str(index) for index in range(1, count + 1) if parser.has_section(str(index))]


def find_existing_rule(parser: configparser.ConfigParser) -> str | None:
    for section in parser.sections():
        if section == "General":
            continue
        if parser.get(section, "Description", fallback="") == RULE_DESCRIPTION:
            return section
        if parser.get(section, "desktopfile", fallback="") == APP_ID:
            return section
    return None


def set_rule_enabled(
    enabled: bool,
    *,
    path: Path | None = None,
    reload_kwin: bool = True,
) -> tuple[str | None, bool]:
    target = path or config_path()
    parser = read_config(target)
    rules = general_rules(parser)
    rule_id = find_existing_rule(parser)

    if rule_id is None:
        if not enabled:
            return None, reload_kwin_config() if reload_kwin else False
        rule_id = str(uuid4())
        parser.add_section(rule_id)
        rules.append(rule_id)
    elif rule_id not in rules:
        rules.append(rule_id)

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
        parser.set(rule_id, key, value)

    parser.set("General", "rules", ",".join(rules))
    parser.set("General", "count", str(len(rules)))
    write_config(parser, target)
    return rule_id, reload_kwin_config() if reload_kwin else False


def reload_kwin_config() -> bool:
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if not qdbus:
        return False
    result = subprocess.run(
        [qdbus, "org.kde.KWin", "/KWin", "reconfigure"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0
