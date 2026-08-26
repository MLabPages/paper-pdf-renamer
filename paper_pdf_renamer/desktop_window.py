from __future__ import annotations

import threading
from typing import Any


class DesktopWindow:
    """WebView2-backed application window that closes to the system tray."""

    def __init__(self, url: str):
        self.url = url
        self._window: Any | None = None
        self._exiting = threading.Event()

    def run(self) -> None:
        import webview

        self._window = webview.create_window(
            "論文PDFファイル名整理",
            self.url,
            width=1280,
            height=850,
            min_size=(820, 600),
            background_color="#f3f6fa",
        )
        self._window.events.closing += self._close_to_tray
        webview.start(gui="edgechromium")

    def show(self) -> None:
        if self._window is not None:
            self._window.show()
            self._window.restore()

    def exit(self) -> None:
        self._exiting.set()
        if self._window is not None:
            self._window.destroy()

    def _close_to_tray(self) -> bool:
        if self._exiting.is_set():
            return True
        if self._window is not None:
            self._window.hide()
        return False
