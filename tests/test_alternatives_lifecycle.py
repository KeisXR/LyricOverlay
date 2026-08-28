"""Regression tests for the alternatives button lifecycle.

An alternatives fetch that is discarded because the track changed must still
leave the overlay able to request alternatives again, without ever delivering
the stale results of the previous track.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, "src")

import pytest
from PySide6.QtCore import QObject, Signal

from lyrics.lrclib import LrcLibResult
from lyrics.manager import LyricsData, LyricsManager
import lyrics.manager as manager_module


class _StubModule(types.ModuleType):
    """Import-time stand-in whose unknown attributes resolve to dummy types."""

    __path__: list = []

    def __getattr__(self, name):
        return type(name, (), {})


@pytest.fixture
def main_module():
    """Import ``main`` with its D-Bus / GLib dependencies stubbed out.

    Every ``sys.modules`` entry added while the stubs are installed is removed
    again afterwards so no stub leaks into the rest of the suite.
    """
    saved = dict(sys.modules)
    for name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository"):
        sys.modules[name] = _StubModule(name)
    sys.modules["dbus.mainloop.glib"].DBusGMainLoop = lambda *args, **kwargs: None
    try:
        import main

        yield main
    finally:
        for name in list(sys.modules):
            if name not in saved:
                del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture
def isolated_cache_dir(tmp_path, monkeypatch):
    cache_dir = tmp_path / "lyricaod-cache"
    monkeypatch.setattr(manager_module, "CACHE_DIR", Path(cache_dir))
    return cache_dir


class _SignalProxy(QObject):
    finished_ok = Signal(object)
    finished_error = Signal(str)
    finished = Signal()


class _PendingAltThread:
    """Fake ``_FetchAltThread`` that stays in flight until the test ends it."""

    instances: list = []

    def __init__(self, *args, **kwargs):
        self._proxy = _SignalProxy()
        self.finished_ok = self._proxy.finished_ok
        self.finished_error = self._proxy.finished_error
        self.finished = self._proxy.finished
        self.cancel_called = False
        _PendingAltThread.instances.append(self)

    def start(self):
        pass  # keeps running; the test drives completion explicitly

    def isRunning(self):
        return not self.cancel_called

    def cancel(self):
        self.cancel_called = True

    def deleteLater(self):
        pass


class _StubOverlay:
    """Records the overlay state that ``main.Application`` drives."""

    def __init__(self):
        self.loading = False
        self.alternatives_loading = False
        self.alternatives: list = []
        self.plain_text = ""
        self.menu_shown = 0

    def set_seek(self, position_ms, length_ms):
        pass

    def set_loading(self, loading):
        self.loading = loading

    def set_alternatives_loading(self, loading):
        self.alternatives_loading = loading

    def set_alternatives(self, alternatives):
        self.alternatives = list(alternatives)

    def set_lyrics_data(self, lrc, synced=False):
        pass

    def set_plain_text(self, text):
        self.plain_text = text

    def _show_alternatives_menu(self):
        self.menu_shown += 1

    def alternatives_button_is_dead(self) -> bool:
        """Mirror of ``OverlayWindow.mousePressEvent``: clicking the
        alternatives button does nothing when there is nothing to show and a
        fetch is still believed to be running."""
        return not self.alternatives and self.alternatives_loading


def _lyrics(artist: str, title: str, text: str) -> LyricsData:
    return LyricsData(
        artist=artist,
        title=title,
        synced=False,
        lrc=None,
        plain_text=text,
        source="cache",
    )


def _make_app(main_module, lyrics, overlay):
    """Drive ``main.Application``'s handlers against stub collaborators.

    The manager signals are connected directly (not queued), so they are
    delivered without a running Qt event loop.
    """
    application = main_module.Application
    app = types.SimpleNamespace(lyrics=lyrics, overlay=overlay, _current_meta={})
    # Application.__init__ is not run here, so collaborators the handlers reach
    # for must be supplied. _normalise_meta is the real one: normalisation
    # decides the artist/title these handlers pass on.
    from meta_utils import normalise_yt_meta

    app._normalise_meta = normalise_yt_meta
    app._set_runtime_state = lambda *args, **kwargs: None
    app._on_lyrics_error = lambda *args, **kwargs: None
    app.metadata_changed = lambda meta: application._on_metadata_changed(app, meta)
    app.click_alternatives_button = lambda: application._on_alternatives_requested(app)
    app._slots = [
        lambda result: application._on_lyrics_ready(app, result),
        lambda artist, title: application._on_lyrics_not_found(app, artist, title),
        lambda alternatives: application._on_alternatives_ready(app, alternatives),
    ]
    lyrics.lyrics_ready.connect(app._slots[0])
    lyrics.lyrics_not_found.connect(app._slots[1])
    lyrics.alternatives_ready.connect(app._slots[2])
    return app


def test_track_change_during_alternatives_fetch_keeps_button_usable(
    isolated_cache_dir, monkeypatch, main_module
):
    monkeypatch.setattr(manager_module, "_FetchAltThread", _PendingAltThread)
    _PendingAltThread.instances = []
    manager = LyricsManager()
    overlay = _StubOverlay()
    app = _make_app(main_module, manager, overlay)

    key_a = manager._make_key("artist-a", "Song A", "")
    key_b = manager._make_key("artist-b", "Song B", "")
    manager._put_cache(key_a, _lyrics("artist-a", "Song A", "A"), [])
    manager._put_cache(key_b, _lyrics("artist-b", "Song B", "B"), [])

    # Track A is playing and the user clicks the alternatives button.
    app.metadata_changed({"artist": "artist-a", "title": "Song A"})
    app.click_alternatives_button()
    stale_req = manager._req_id
    assert overlay.alternatives_loading is True
    assert len(_PendingAltThread.instances) == 1

    # The track changes while that fetch is still in flight; B is a cache hit.
    app.metadata_changed({"artist": "artist-b", "title": "Song B"})

    assert _PendingAltThread.instances[0].cancel_called is True
    assert overlay.alternatives_loading is False
    assert not overlay.alternatives_button_is_dead()

    # The discarded worker reports back anyway: its results belong to track A
    # and must not be delivered.
    manager._on_alternatives_done(
        [LrcLibResult(track_name="Song A alt", artist_name="artist-a")],
        key_a,
        stale_req,
    )

    assert overlay.alternatives == []
    assert overlay.menu_shown == 0
    assert manager._get_cached(key_a)[1] == []
    assert overlay.alternatives_loading is False
    assert not overlay.alternatives_button_is_dead()

    # The feature still works: a fresh click starts a new fetch.
    app.click_alternatives_button()
    assert len(_PendingAltThread.instances) == 2
    assert overlay.alternatives_loading is True


def test_discarded_alternatives_do_not_clear_current_track_alternatives(
    isolated_cache_dir, monkeypatch, main_module
):
    monkeypatch.setattr(manager_module, "_FetchAltThread", _PendingAltThread)
    _PendingAltThread.instances = []
    manager = LyricsManager()
    overlay = _StubOverlay()
    app = _make_app(main_module, manager, overlay)

    key_a = manager._make_key("artist-a", "Song A", "")
    key_b = manager._make_key("artist-b", "Song B", "")
    manager._put_cache(key_a, _lyrics("artist-a", "Song A", "A"), [])
    manager._put_cache(
        key_b,
        _lyrics("artist-b", "Song B", "B"),
        [_lyrics("artist-b", "Song B alt", "B alt")],
    )

    app.metadata_changed({"artist": "artist-a", "title": "Song A"})
    app.click_alternatives_button()
    stale_req = manager._req_id

    app.metadata_changed({"artist": "artist-b", "title": "Song B"})
    assert [a.title for a in overlay.alternatives] == ["Song B alt"]

    manager._on_alternatives_done(
        [LrcLibResult(track_name="Song A alt", artist_name="artist-a")],
        key_a,
        stale_req,
    )

    assert [a.title for a in overlay.alternatives] == ["Song B alt"]
    assert overlay.menu_shown == 0
    assert overlay.alternatives_loading is False
