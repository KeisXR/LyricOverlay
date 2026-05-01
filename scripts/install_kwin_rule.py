#!/usr/bin/env python3
"""Install or disable the KDE KWin rule for the lyrics overlay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ui.kwin_rules import config_path, set_rule_enabled  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install a KWin rule that keeps Lyricaod above other windows."
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Write kwinrulesrc but do not ask KWin to reload it.",
    )
    parser.add_argument(
        "--disable",
        action="store_true",
        help="Disable the Lyricaod KWin rule instead of enabling it.",
    )
    args = parser.parse_args()

    enabled = not args.disable
    rule_id, reloaded = set_rule_enabled(enabled, reload_kwin=not args.no_reload)

    action = "Enabled" if enabled else "Disabled"
    target = config_path()
    if rule_id:
        print(f"{action} KWin rule {rule_id} in {target}")
    else:
        print(f"No Lyricaod KWin rule exists in {target}")
    if not args.no_reload:
        if reloaded:
            print("Reloaded KWin configuration.")
        else:
            print("Could not reload KWin automatically; log out/in or restart KWin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
