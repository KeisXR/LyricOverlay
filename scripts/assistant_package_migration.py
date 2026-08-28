#!/usr/bin/env python3
"""One-shot repository migration to the src/lyricaod package layout."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PKG = SRC / "lyricaod"


def write(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def replace(path: Path, replacements: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        if old not in text:
            print(f"warning: {old!r} not found in {path}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def copy_sources() -> None:
    if PKG.exists():
        shutil.rmtree(PKG)
    PKG.mkdir(parents=True)
    for directory in ("config", "lyrics", "player", "ui"):
        shutil.copytree(SRC / directory, PKG / directory)
    for filename in (
        "main.py",
        "meta_utils.py",
        "logging_setup.py",
        "diagnostics.py",
        "runtime_status.py",
        "diagnostic_lyrics_manager.py",
    ):
        shutil.copy2(SRC / filename, PKG / filename)

    write(
        PKG / "__init__.py",
        '"""Lyricaod desktop lyrics overlay."""\n\n'
        "from ._version import __version__\n\n"
        '__all__ = ["__version__"]\n',
    )
    write(PKG / "_version.py", '__version__ = "0.1.1"\n')
    write(
        PKG / "__main__.py",
        "from .main import main\n\nraise SystemExit(main())\n",
    )


def update_package_imports() -> None:
    replace(
        PKG / "main.py",
        {
            "from diagnostics import ": "from .diagnostics import ",
            "from logging_setup import ": "from .logging_setup import ",
            "from runtime_status import ": "from .runtime_status import ",
            "from config.settings import Settings": "from .config.settings import Settings",
            "from diagnostic_lyrics_manager import DiagnosticLyricsManager": "from .diagnostic_lyrics_manager import DiagnosticLyricsManager",
            "from meta_utils import normalise_yt_meta": "from .meta_utils import normalise_yt_meta",
            "from player import BrowserWsListener, MprisListener": "from .player import BrowserWsListener, MprisListener",
            "from ui.overlay import OverlayWindow": "from .ui.overlay import OverlayWindow",
            "from ui.tray import TrayIcon": "from .ui.tray import TrayIcon",
        },
    )
    replace(
        PKG / "diagnostic_lyrics_manager.py",
        {"from lyrics.manager import ": "from .lyrics.manager import "},
    )

    diagnostics = PKG / "diagnostics.py"
    text = diagnostics.read_text(encoding="utf-8")
    text = text.replace(
        "from logging_setup import LoggingState, configure_logging, default_log_dir, get_logger",
        "from . import __version__\nfrom .logging_setup import LoggingState, configure_logging, default_log_dir, get_logger",
    )
    old_version_block = '''SOURCE_VERSION = "0.1.1+source"\n\n\ndef application_version() -> str:\n    try:\n        return importlib.metadata.version("lyricaod")\n    except importlib.metadata.PackageNotFoundError:\n        return SOURCE_VERSION\n'''
    text = text.replace(
        old_version_block,
        "def application_version() -> str:\n    return __version__\n",
    )
    for old, new in {
        '"config.settings"': '"lyricaod.config.settings"',
        '"lyrics.manager"': '"lyricaod.lyrics.manager"',
        '"lyrics.lrc_parser"': '"lyricaod.lyrics.lrc_parser"',
        '"ui.overlay"': '"lyricaod.ui.overlay"',
        '"player"': '"lyricaod.player"',
    }.items():
        text = text.replace(old, new)
    diagnostics.write_text(text, encoding="utf-8")

    replace(
        PKG / "player" / "mpris.py",
        {"from meta_utils import enrich_missing_meta": "from ..meta_utils import enrich_missing_meta"},
    )
    replace(
        PKG / "ui" / "overlay.py",
        {
            "from lyrics.lrc_parser import ": "from ..lyrics.lrc_parser import ",
            "from ui.color_utils import ": "from .color_utils import ",
        },
    )
    replace(
        PKG / "ui" / "settings_dialog.py",
        {
            "from ui.color_utils import ": "from .color_utils import ",
            "from ui.kwin_rules import ": "from .kwin_rules import ",
        },
    )
    replace(
        PKG / "ui" / "tray.py",
        {
            "from ui.kwin_rules import ": "from .kwin_rules import ",
            "from ui.settings_dialog import ": "from .settings_dialog import ",
        },
    )

    write(
        PKG / "player" / "__init__.py",
        '''"""Platform-specific player listeners."""\n\nfrom __future__ import annotations\n\nimport sys\n\nif sys.platform == "win32":\n    from .smtc import SmtcListener as MprisListener\nelif sys.platform.startswith("linux"):\n    from .mpris import MprisListener\nelse:\n    class MprisListener:\n        def __init__(self, *args, **kwargs):\n            raise RuntimeError(\n                "Native media-session integration is supported on Linux and Windows only"\n            )\n\nfrom .browser_ws import BrowserWsListener\n\n__all__ = ["MprisListener", "BrowserWsListener"]\n''',
    )


def update_tests() -> None:
    replacements = {
        "from lyrics.": "from lyricaod.lyrics.",
        "import lyrics.": "import lyricaod.lyrics.",
        "from meta_utils import ": "from lyricaod.meta_utils import ",
        "from runtime_status import ": "from lyricaod.runtime_status import ",
        "from diagnostic_lyrics_manager import ": "from lyricaod.diagnostic_lyrics_manager import ",
        "import logging_setup": "from lyricaod import logging_setup",
        "import diagnostics": "from lyricaod import diagnostics",
        "import main": "from lyricaod import main",
        '"lyrics.lrclib.': '"lyricaod.lyrics.lrclib.',
        '"lyrics.manager.': '"lyricaod.lyrics.manager.',
    }
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"\n?sys\.path\.insert\(0, [\"']src[\"']\)\n?", "\n", text)
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")

    write(
        ROOT / "tests" / "test_package_entrypoints.py",
        '''import shutil\nimport subprocess\nimport sys\n\nimport lyricaod\n\n\ndef test_version_has_single_package_source():\n    assert lyricaod.__version__ == "0.1.1"\n\n\ndef test_module_entrypoint_help():\n    result = subprocess.run(\n        [sys.executable, "-m", "lyricaod", "--help"],\n        check=False,\n        capture_output=True,\n        text=True,\n    )\n    assert result.returncode == 0, result.stderr\n    assert "Lyricaod" in result.stdout\n\n\ndef test_installed_console_entrypoint_help():\n    executable = shutil.which("lyricaod")\n    assert executable is not None\n    result = subprocess.run(\n        [executable, "--help"],\n        check=False,\n        capture_output=True,\n        text=True,\n    )\n    assert result.returncode == 0, result.stderr\n\n\ndef test_current_platform_player_package_imports():\n    import lyricaod.player\n    assert lyricaod.player.BrowserWsListener is not None\n''',
    )


def write_pyproject() -> None:
    write(
        ROOT / "pyproject.toml",
        '''[build-system]\nrequires = ["setuptools==82.0.1", "wheel==0.47.0"]\nbuild-backend = "setuptools.build_meta"\n\n[project]\nname = "lyricaod"\ndynamic = ["version"]\ndescription = "Desktop synchronized lyrics overlay for KDE Plasma and Windows"\nreadme = "README.md"\nrequires-python = ">=3.12"\nlicense = "MIT"\nlicense-files = ["LICENSE"]\nauthors = [{ name = "KeisXR" }]\nclassifiers = [\n  "Development Status :: 3 - Alpha",\n  "Environment :: X11 Applications :: Qt",\n  "Programming Language :: Python :: 3",\n  "Programming Language :: Python :: 3.12",\n  "License :: OSI Approved :: MIT License",\n  "Operating System :: Microsoft :: Windows :: Windows 10",\n  "Operating System :: POSIX :: Linux",\n]\ndependencies = [\n  "PySide6>=6.6",\n  "httpx>=0.25",\n  "websockets>=12.0",\n  "syncedlyrics>=1.0.1",\n  "dbus-python>=1.3; sys_platform == 'linux'",\n  "winrt-runtime>=2.0; sys_platform == 'win32'",\n  "winrt-Windows.Foundation>=2.0; sys_platform == 'win32'",\n  "winrt-Windows.Foundation.Collections>=2.0; sys_platform == 'win32'",\n  "winrt-Windows.ApplicationModel>=2.0; sys_platform == 'win32'",\n  "winrt-Windows.Graphics>=2.0; sys_platform == 'win32'",\n  "winrt-Windows.Media>=2.0; sys_platform == 'win32'",\n  "winrt-Windows.Media.Control>=2.0; sys_platform == 'win32'",\n  "winrt-Windows.Storage.Streams>=2.0; sys_platform == 'win32'",\n  "winrt-Windows.System>=2.0; sys_platform == 'win32'",\n]\n\n[project.optional-dependencies]\ndev = [\n  "pytest==9.0.2",\n  "pytest-qt==4.5.0",\n  "ruff==0.15.0",\n  "mypy==1.19.1",\n]\n\n[project.scripts]\nlyricaod = "lyricaod.main:main"\n\n[tool.setuptools.dynamic]\nversion = { attr = "lyricaod.__version__" }\n\n[tool.setuptools.packages.find]\nwhere = ["src"]\ninclude = ["lyricaod*"]\n\n[tool.pytest.ini_options]\nminversion = "9.0"\ntestpaths = ["tests"]\naddopts = "-ra -m 'not integration'"\nmarkers = [\n  "integration: requires an external service or a real desktop/media backend",\n  "qt: requires a Qt application and normally runs with QT_QPA_PLATFORM=offscreen",\n]\n\n[tool.ruff]\ntarget-version = "py312"\nline-length = 100\nexclude = [".venv", ".build-venv", ".build-work", "build", "dist"]\n\n[tool.ruff.lint]\nselect = ["E9", "F63", "F7", "F82"]\n\n[tool.mypy]\npython_version = "3.12"\nignore_missing_imports = true\nwarn_unused_configs = true\nshow_error_codes = true\npretty = true\n''',
    )


def update_packaging_and_scripts() -> None:
    replace(
        ROOT / "packaging" / "lyricaod.spec",
        {
            '[str(src_dir / "main.py")]': '[str(src_dir / "lyricaod" / "__main__.py")]',
            '"player.mpris"': '"lyricaod.player.mpris"',
            '"player.smtc"': '"lyricaod.player.smtc"',
        },
    )
    replace(
        ROOT / "packaging" / "runtime_logging.py",
        {"from logging_setup import ": "from lyricaod.logging_setup import "},
    )
    replace(
        ROOT / "scripts" / "install_kwin_rule.py",
        {"from ui.kwin_rules import ": "from lyricaod.ui.kwin_rules import "},
    )

    write(
        SRC / "main.py",
        '''#!/usr/bin/env python3\n"""Deprecated source-checkout wrapper; use ``python -m lyricaod``."""\n\nimport warnings\n\nwarnings.warn(\n    "python src/main.py is deprecated; install the project and use `lyricaod`",\n    DeprecationWarning,\n    stacklevel=1,\n)\n\nfrom lyricaod.main import main\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
    )

    write(
        ROOT / "setup.sh",
        '''#!/usr/bin/env bash\nset -euo pipefail\n\nSCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"\nVENV_DIR="${SCRIPT_DIR}/.venv"\n\npython3 -m venv --system-site-packages "${VENV_DIR}"\n"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check "pip==26.1.1"\n"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check -r "${SCRIPT_DIR}/requirements.lock"\n"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check --no-deps --editable "${SCRIPT_DIR}"\n\necho "Setup complete. Run: ${VENV_DIR}/bin/lyricaod"\n''',
        executable=True,
    )
    write(
        ROOT / "setup.ps1",
        '''$ErrorActionPreference = "Stop"\n\n$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path\n$VenvDir = Join-Path $ScriptDir ".venv"\n\nfunction Invoke-Native {\n    param(\n        [Parameter(Mandatory = $true)][string]$FilePath,\n        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments\n    )\n    & $FilePath @Arguments\n    if ($LASTEXITCODE -ne 0) {\n        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $Arguments"\n    }\n}\n\nInvoke-Native python --version\nif (-not (Test-Path -LiteralPath $VenvDir)) {\n    Invoke-Native python -m venv $VenvDir\n}\n$Python = Join-Path $VenvDir "Scripts\\python.exe"\nInvoke-Native $Python -m pip install --disable-pip-version-check "pip==26.1.1"\nInvoke-Native $Python -m pip install --disable-pip-version-check -r (Join-Path $ScriptDir "requirements.lock")\nInvoke-Native $Python -m pip install --disable-pip-version-check --no-deps --editable $ScriptDir\nWrite-Host "Setup complete. Run: $(Join-Path $VenvDir 'Scripts\\lyricaod.exe')"\n''',
    )


def update_ci_and_contributing() -> None:
    workflow = ROOT / ".github" / "workflows" / "build.yml"
    text = workflow.read_text(encoding="utf-8")
    marker = "python -m pip install -r requirements-dev.lock"
    text = text.replace(
        marker,
        marker + "\n          python -m pip install --no-deps --editable .",
    )
    text = text.replace(
        "mypy src/lyrics/lrc_parser.py src/meta_utils.py src/ui/color_utils.py",
        "mypy src/lyricaod/lyrics/lrc_parser.py src/lyricaod/meta_utils.py src/lyricaod/ui/color_utils.py",
    )
    text = text.replace("python src/main.py --diagnose", "python -m lyricaod --diagnose")
    workflow.write_text(text, encoding="utf-8")

    write(
        ROOT / "CONTRIBUTING.md",
        '''# Contributing to Lyricaod\n\n## Development environment\n\nUse Python 3.12 and install the exact development dependency set plus the\nproject itself in editable mode:\n\n```bash\npython -m venv .venv\nsource .venv/bin/activate       # Windows: .\\.venv\\Scripts\\Activate.ps1\npython -m pip install "pip==26.1.1"\npython -m pip install -r requirements-dev.lock\npython -m pip install --no-deps --editable .\n```\n\nLinux also needs the D-Bus/GLib and Qt runtime packages listed in `README.md`.\n\n## Running\n\n```bash\nlyricaod\npython -m lyricaod\n```\n\n`python src/main.py` remains only as a deprecated source-checkout wrapper.\n\n## Local quality gates\n\n```bash\npython -m compileall -q src tests\nruff check src tests\nmypy src/lyricaod/lyrics/lrc_parser.py src/lyricaod/meta_utils.py src/lyricaod/ui/color_utils.py\nQT_QPA_PLATFORM=offscreen pytest -q\n```\n\nLive LRClib tests are opt-in:\n\n```bash\npytest -m integration tests/test_lrclib.py\n```\n\n## Packages\n\nUse `scripts/build_linux.sh` or `scripts/build_windows.ps1`. CI runs tests and\nstatic checks before building, then executes the packaged `--self-test`.\n\n## Pull requests\n\nKeep each pull request focused on one issue. Add regression tests for behavior\nchanges. Changes to a lock file must be intentional and explained in the PR.\n''',
    )


def remove_legacy_modules() -> None:
    for directory in ("config", "lyrics", "player", "ui"):
        shutil.rmtree(SRC / directory)
    for filename in (
        "meta_utils.py",
        "logging_setup.py",
        "diagnostics.py",
        "runtime_status.py",
        "diagnostic_lyrics_manager.py",
    ):
        (SRC / filename).unlink()


def main() -> None:
    copy_sources()
    update_package_imports()
    update_tests()
    write_pyproject()
    update_packaging_and_scripts()
    update_ci_and_contributing()
    remove_legacy_modules()
    print("package migration prepared")


if __name__ == "__main__":
    main()
