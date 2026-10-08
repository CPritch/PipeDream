import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication

from pipedream.director import Director
from pipedream.gui import Backend


def test_shutdown_while_waiting_for_input_does_not_hang(tmp_path, monkeypatch):
    monkeypatch.setattr(Director, "describe_scene", lambda self, text, previous_text="": None)
    monkeypatch.setattr("pipedream.cache.DEFAULT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("pipedream.navigator.DEFAULT_CACHE_DIR", str(tmp_path))

    app = QGuiApplication.instance() or QGuiApplication([])
    # Explicit URLs mean no local servers are started; the director is stubbed so they're never called
    backend = Backend([sys.executable, "-m", "pipedream.games.mock_game"],
                      llm_url="http://127.0.0.1:9", image_url="http://127.0.0.1:9")

    deadline = time.monotonic() + 15
    while "open field" not in backend.console_text and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
    assert "open field" in backend.console_text

    started = time.monotonic()
    backend.shutdown()

    assert not backend.worker.thread.is_alive()
    assert time.monotonic() - started < 2
