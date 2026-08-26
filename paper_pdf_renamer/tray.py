from __future__ import annotations

import webbrowser
import threading
from collections.abc import Callable
from typing import Any


class TrayActions:
    """タスクトレーメニューからアプリ状態を安全に操作する。"""

    def __init__(self, state: Any, url: str, shutdown: Callable[[], None]):
        self.state = state
        self.url = url
        self.shutdown = shutdown

    def status_text(self, _item: object | None = None) -> str:
        return "状態：作動中" if self.state.monitoring else "状態：一時停止中"

    def resume(self, icon: Any = None, _item: object | None = None) -> None:
        self.state.start_monitor()
        self._refresh(icon)

    def pause(self, icon: Any = None, _item: object | None = None) -> None:
        self.state.stop_monitor()
        self._refresh(icon)

    def open_ui(self, _icon: Any = None, _item: object | None = None) -> None:
        webbrowser.open(self.url)

    def exit(self, icon: Any = None, _item: object | None = None) -> None:
        self.shutdown()
        if icon is not None:
            icon.stop()

    def _refresh(self, icon: Any) -> None:
        if icon is None:
            return
        icon.icon = _status_image(self.state.monitoring)
        icon.title = _tooltip(self.state.monitoring)
        icon.update_menu()


def _tooltip(monitoring: bool) -> str:
    return f"論文PDFファイル名整理 - {'作動中' if monitoring else '一時停止中'}"


def _status_image(monitoring: bool):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    fill = "#22a06b" if monitoring else "#6b7280"
    draw.rounded_rectangle((6, 6, 58, 58), radius=13, fill=fill)
    draw.rectangle((18, 17, 46, 47), fill="white")
    draw.rectangle((22, 22, 42, 27), fill=fill)
    draw.rectangle((22, 32, 39, 37), fill=fill)
    draw.rectangle((22, 42, 34, 47), fill=fill)
    return image


def run_tray(state: Any, url: str, shutdown: Callable[[], None]) -> None:
    import pystray

    actions = TrayActions(state, url, shutdown)
    menu = pystray.Menu(
        pystray.MenuItem(actions.status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "作動中（監視を再開）",
            actions.resume,
            checked=lambda _item: state.monitoring,
            radio=True,
        ),
        pystray.MenuItem(
            "一時停止",
            actions.pause,
            checked=lambda _item: not state.monitoring,
            radio=True,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("画面を開く", actions.open_ui, default=True),
        pystray.MenuItem("終了", actions.exit),
    )
    icon = pystray.Icon(
        "PaperPdfRenamer",
        _status_image(state.monitoring),
        _tooltip(state.monitoring),
        menu,
    )
    sync_stop = threading.Event()

    def sync_state() -> None:
        previous = state.monitoring
        while not sync_stop.wait(1.0):
            current = state.monitoring
            if current != previous:
                previous = current
                actions._refresh(icon)

    sync_thread = threading.Thread(target=sync_state, daemon=True)
    sync_thread.start()
    try:
        icon.run()
    finally:
        sync_stop.set()
        sync_thread.join(timeout=1.5)
