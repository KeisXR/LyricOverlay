# Contributing to Lyricaod

## Development environment

Use Python 3.12. Runtime, build, and test dependencies are deliberately split:

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install "pip==26.1.1"
python -m pip install -r requirements-dev.lock
```

Linux also needs the D-Bus/GLib and Qt runtime packages listed in `README.md`.

## Local quality gates

The default test run does not access external APIs:

```bash
python -m compileall -q src tests
ruff check src tests
mypy src/lyrics/lrc_parser.py src/meta_utils.py src/ui/color_utils.py
QT_QPA_PLATFORM=offscreen pytest -q
```

Live LRClib tests are opt-in:

```bash
pytest -m integration tests/test_lrclib.py
```

## Packages

Use the repository scripts; they install the version-locked build toolchain:

```bash
bash scripts/build_linux.sh
```

```powershell
.\scripts\build_windows.ps1
```

The CI workflow runs tests and static checks before creating artifacts, then
starts each packaged executable with `--help` as a dependency/startup smoke
test. Do not upload or publish artifacts from a failed quality-gate run.

## Pull requests

Keep each pull request focused on one issue. Add regression tests for behavior
changes and keep external-service tests behind the `integration` marker.
Changes to a lock file must be intentional and should explain the dependency or
toolchain update in the pull-request description.
