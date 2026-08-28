import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "src")

import diagnostics
import logging_setup


def test_collect_diagnostics_does_not_require_event_loop(tmp_path):
    logging_state = logging_setup.configure_logging(
        log_dir=tmp_path / "logs", console=False
    )

    data = diagnostics.collect_diagnostics(logging_state=logging_state)
    rendered = diagnostics.format_diagnostics(data)
    decoded = json.loads(rendered)

    assert decoded["application"] == "Lyricaod"
    assert decoded["python"]
    assert decoded["paths"]["logs"] == str(tmp_path / "logs")
    assert "PySide6" in decoded["dependencies"]


def test_self_test_succeeds_without_real_media_backend(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    real_import = importlib.import_module

    def import_module(name, package=None):
        if name == "player":
            return object()
        return real_import(name, package)

    monkeypatch.setattr(diagnostics.importlib, "import_module", import_module)

    success, failures = diagnostics.run_self_test()

    assert success, failures
    assert failures == []


def test_self_test_failure_report_reaches_captured_stderr(
    tmp_path, monkeypatch, capsys
):
    # Once the temporary log handlers are closed a handler-less logger falls
    # back to logging.lastResort, whose stream is the packaged capture stream
    # feeding this same logger; the failure report has to escape that loop.
    logging_setup.configure_logging(log_dir=tmp_path / "logs", console=False)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        logging_setup.capture_legacy_prints()
        diagnostics._close_lyricaod_handlers()
        diagnostics.logger.error("Self-test failure: %s", "import websockets")
        print("- import websockets", file=sys.stderr)
        sys.stderr.flush()
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        logging_setup.configure_logging(log_dir=tmp_path / "logs", console=False)

    report = capsys.readouterr().err
    assert "Self-test failure: import websockets" in report
    assert "- import websockets" in report


def test_main_self_test_exit_codes(monkeypatch, tmp_path):
    import main

    monkeypatch.setattr(
        main,
        "configure_logging",
        lambda **_kwargs: logging_setup.configure_logging(
            log_dir=tmp_path / "logs", console=False
        ),
    )
    monkeypatch.setattr(main, "run_self_test", lambda: (True, []))
    assert main.main(["--self-test"]) == 0

    monkeypatch.setattr(
        main,
        "run_self_test",
        lambda: (False, ["failure"]),
    )
    assert main.main(["--self-test"]) == 1
