import sys
from pathlib import Path

sys.path.insert(0, "src")

from diagnostic_lyrics_manager import DiagnosticLyricsManager
from lyrics.manager import LyricsData
import lyrics.manager as manager_module


def test_error_code_classification():
    classify = DiagnosticLyricsManager._classify_error
    assert classify("HTTP 429 Too Many Requests") == "rate_limited"
    assert classify("ReadTimeout") == "network_timeout"
    assert classify("DNS connection failed") == "network_error"
    assert classify("unexpected provider response") == "provider_error"


def test_sources_disabled_emit_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_module, "CACHE_DIR", Path(tmp_path))
    manager = DiagnosticLyricsManager(
        lrclib_enabled=False,
        syncedlyrics_enabled=False,
    )
    errors = []
    manager.lyrics_error.connect(lambda *args: errors.append(args))

    manager.fetch_lyrics("Artist", "Song")

    assert errors[-1][2] == "sources_disabled"
    assert manager.delivery_status == "sources_disabled"


def test_sources_disabled_can_still_show_cached_lyrics(tmp_path, monkeypatch):
    monkeypatch.setattr(manager_module, "CACHE_DIR", Path(tmp_path))
    manager = DiagnosticLyricsManager(
        lrclib_enabled=False,
        syncedlyrics_enabled=False,
    )
    key = manager._make_key("Artist", "Song", "")
    manager._put_cache(
        key,
        LyricsData("Artist", "Song", False, None, "lyrics", "lrclib"),
        [],
    )
    ready = []
    manager.lyrics_ready.connect(ready.append)

    manager.fetch_lyrics("Artist", "Song")

    assert ready[-1].primary.title == "Song"
    assert manager.delivery_status == "cache_hit"


def _class_signatures(path, class_name):
    """Map method name -> positional parameter names for one class."""
    import ast

    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name: [arg.arg for arg in item.args.args]
                for item in node.body
                if isinstance(item, ast.FunctionDef)
                # A method that forwards *args/**kwargs cannot drift.
                and not item.args.vararg
                and not item.args.kwarg
            }
    raise AssertionError(f"{class_name} not found in {path}")


def test_overrides_match_base_signatures():
    # DiagnosticLyricsManager subclasses LyricsManager and overrides private
    # handlers that the base invokes positionally. A parameter added to the
    # base is not a conflict in any file, so a merge cannot flag it -- the
    # override just starts raising TypeError, or worse, silently misbinds.
    # Compare the signatures directly so that breakage fails here instead.
    root = Path(__file__).resolve().parents[1]
    base = _class_signatures(root / "src" / "lyrics" / "manager.py", "LyricsManager")
    diagnostic = _class_signatures(
        root / "src" / "diagnostic_lyrics_manager.py", "DiagnosticLyricsManager"
    )

    mismatched = {
        name: (base[name], params)
        for name, params in diagnostic.items()
        if name in base and base[name] != params
    }
    assert mismatched == {}, f"override signatures drifted from the base: {mismatched}"
