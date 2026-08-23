import base64
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from paper_pdf_renamer.gui import run_server, select_windows_folder


def test_gui_uses_port_separate_from_translator() -> None:
    assert inspect.signature(run_server).parameters["port"].default == 8766


def test_select_windows_folder_decodes_unicode_path() -> None:
    selected = r"C:\Users\mkn09\マイドライブ\論文"
    encoded = base64.b64encode(selected.encode("utf-8")).decode("ascii")

    with (
        patch("paper_pdf_renamer.gui.os.name", "nt"),
        patch(
            "paper_pdf_renamer.gui.subprocess.run",
            return_value=SimpleNamespace(stdout=f"{encoded}\n"),
        ) as run,
    ):
        assert select_windows_folder() == selected

    command = run.call_args.args[0]
    assert command[0] == "powershell.exe"
    assert "-Sta" in command
    assert "-EncodedCommand" in command


def test_select_windows_folder_cancel_returns_none() -> None:
    with (
        patch("paper_pdf_renamer.gui.os.name", "nt"),
        patch(
            "paper_pdf_renamer.gui.subprocess.run",
            return_value=SimpleNamespace(stdout="\n"),
        ),
    ):
        assert select_windows_folder() is None
