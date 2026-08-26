from unittest.mock import Mock

from paper_pdf_renamer.desktop_window import DesktopWindow


def test_close_hides_window_without_exiting() -> None:
    desktop = DesktopWindow("http://127.0.0.1:8766/")
    desktop._window = Mock()

    assert desktop._close_to_tray() is False
    desktop._window.hide.assert_called_once_with()


def test_exit_allows_window_to_close() -> None:
    desktop = DesktopWindow("http://127.0.0.1:8766/")
    desktop._window = Mock()

    desktop.exit()

    desktop._window.destroy.assert_called_once_with()
    assert desktop._close_to_tray() is True


def test_show_restores_hidden_window() -> None:
    desktop = DesktopWindow("http://127.0.0.1:8766/")
    desktop._window = Mock()

    desktop.show()

    desktop._window.show.assert_called_once_with()
    desktop._window.restore.assert_called_once_with()
