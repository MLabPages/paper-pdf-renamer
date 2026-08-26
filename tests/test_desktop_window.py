from pathlib import Path
from unittest.mock import patch

from paper_pdf_renamer.desktop_window import open_app_window


def test_windows_uses_edge_app_mode() -> None:
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    with (
        patch("paper_pdf_renamer.desktop_window.os.name", "nt"),
        patch("paper_pdf_renamer.desktop_window._edge_candidates", return_value=(edge,)),
        patch("pathlib.Path.is_file", return_value=True),
        patch("paper_pdf_renamer.desktop_window.subprocess.Popen") as popen,
    ):
        assert open_app_window("http://127.0.0.1:8766/")

    command = popen.call_args.args[0]
    assert command == [str(edge), "--app=http://127.0.0.1:8766/"]


def test_missing_edge_falls_back_to_default_browser() -> None:
    with (
        patch("paper_pdf_renamer.desktop_window.os.name", "nt"),
        patch("paper_pdf_renamer.desktop_window._edge_candidates", return_value=()),
        patch("paper_pdf_renamer.desktop_window.webbrowser.open", return_value=True) as opened,
    ):
        assert open_app_window("http://127.0.0.1:8766/")
    opened.assert_called_once()
