from unittest.mock import patch

from paper_pdf_renamer.tray import TrayActions, _tooltip


class FakeState:
    def __init__(self, monitoring: bool = True):
        self.monitoring = monitoring
        self.started = 0
        self.stopped = 0

    def start_monitor(self) -> None:
        self.started += 1
        self.monitoring = True

    def stop_monitor(self) -> None:
        self.stopped += 1
        self.monitoring = False


class FakeIcon:
    def __init__(self):
        self.icon = None
        self.title = ""
        self.menu_updates = 0
        self.stopped = False

    def update_menu(self) -> None:
        self.menu_updates += 1

    def stop(self) -> None:
        self.stopped = True


def test_tray_pause_and_resume_update_state_and_icon() -> None:
    state = FakeState()
    icon = FakeIcon()
    actions = TrayActions(state, lambda: None, lambda: None)

    with patch("paper_pdf_renamer.tray._status_image", side_effect=lambda active: f"icon-{active}"):
        actions.pause(icon)
        assert not state.monitoring
        assert state.stopped == 1
        assert "一時停止中" in actions.status_text()
        assert "一時停止中" in icon.title

        actions.resume(icon)
    assert state.monitoring
    assert state.started == 1
    assert "作動中" in actions.status_text()
    assert "作動中" in icon.title
    assert icon.menu_updates == 2


def test_tray_open_and_exit() -> None:
    state = FakeState()
    icon = FakeIcon()
    shutdown = []
    shown = []
    actions = TrayActions(state, lambda: shown.append(True), lambda: shutdown.append(True))

    actions.open_ui()
    assert shown == [True]

    actions.exit(icon)
    assert shutdown == [True]
    assert icon.stopped


def test_tooltip_reports_current_state() -> None:
    assert _tooltip(True).endswith("作動中")
    assert _tooltip(False).endswith("一時停止中")
