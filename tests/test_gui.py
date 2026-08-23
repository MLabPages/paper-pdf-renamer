import base64
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paper_pdf_renamer.config import Settings
from paper_pdf_renamer.gui import AppState, run_server, select_windows_folder
from paper_pdf_renamer.history import HistoryLog


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


def test_gui_reformats_pdf_from_saved_history(tmp_path: Path) -> None:
    current = tmp_path / "Schmitt et al._1999_A Safe Paper.pdf"
    current.write_bytes(b"pdf")
    settings = Settings(
        watch_folders=[str(tmp_path)],
        history_dir=str(tmp_path / "logs"),
        format_template="{author} ({year}). - {title}.pdf",
    ).validate()
    HistoryLog(settings.history_dir).append({
        "action": "rename",
        "status": "renamed",
        "original_filename": "download.pdf",
        "new_filename": current.name,
        "original_path": str(tmp_path / "download.pdf"),
        "new_path": str(current),
        "doi": "10.1234/example",
        "title": "A Safe Paper",
        "first_author": "Schmitt",
        "authors": ["Schmitt", "Lemon"],
        "year": 1999,
        "language": "en",
        "metadata_source": "crossref:doi",
        "confidence": 0.99,
    })

    state = AppState(settings)
    assert state.reformat_history() == 1
    candidate = state.snapshot()["candidates"][0]
    assert candidate["status"] == "ready"
    assert candidate["destination_path"].endswith("Schmitt & Lemon (1999). - A Safe Paper.pdf")
