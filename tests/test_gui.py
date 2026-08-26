import base64
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paper_pdf_renamer.config import Settings
from paper_pdf_renamer.gui import AppState, _warning_text, run_server, select_windows_folder
from paper_pdf_renamer.history import HistoryLog
from paper_pdf_renamer.models import ResolvedMetadata
from paper_pdf_renamer.operations import RenameService


def test_gui_uses_port_separate_from_translator() -> None:
    assert inspect.signature(run_server).parameters["port"].default == 8766


def test_openalex_warning_is_shown_in_japanese() -> None:
    text = _warning_text(("verified-by-openalex", "doi-missing-verified-by-openalex"))
    assert "OpenAlex" in text
    assert "タイトル・著者・年が一致" in text


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


def test_gui_rechecks_held_pdf_with_current_resolver(tmp_path: Path) -> None:
    source = tmp_path / "30-1-72.pdf"
    source.write_bytes(b"pdf")
    settings = Settings(
        watch_folders=[str(tmp_path)],
        history_dir=str(tmp_path / "logs"),
        format_template="{author} ({year}). - {title}.pdf",
    ).validate()
    HistoryLog(settings.history_dir).append({
        "action": "hold",
        "status": "held",
        "original_filename": source.name,
        "original_path": str(source),
        "title": "Incorrect old extraction",
        "first_author": "Incorrect",
        "year": 2003,
        "confidence": 0.2,
    })
    refreshed = ResolvedMetadata(
        "10.1234/rechecked",
        "Do Reverse-Worded Items Confound Measures",
        ("Wong", "Rindfleisch", "Burroughs"),
        2003,
        "en",
        "crossref:title",
        0.96,
        paper_type="journal-article",
    )
    calls: list[Path] = []
    service = RenameService(
        lambda path: calls.append(path) or refreshed,
        format_template=settings.format_template,
    )

    state = AppState(settings)
    with patch.object(state, "_service", return_value=service):
        assert state.reformat_history() == 1

    candidate = state.snapshot()["candidates"][0]
    assert calls == [source]
    assert candidate["status"] == "ready"
    assert candidate["source_path"] == str(source)
    assert "Wong et al. (2003)" in candidate["destination_path"]
    assert "要確認の再スキャン 1件" in state.message


def test_scan_folder_checks_only_the_chosen_folder(tmp_path: Path) -> None:
    chosen = tmp_path / "顧客経験の測定"
    chosen.mkdir()
    (chosen / "target.pdf").write_bytes(b"pdf")
    other = tmp_path / "別フォルダ"
    other.mkdir()
    (other / "untouched.pdf").write_bytes(b"pdf")
    settings = Settings(
        watch_folders=[str(tmp_path)],
        history_dir=str(tmp_path / "logs"),
        format_template="{author} ({year}). - {title}.pdf",
    ).validate()
    metadata = ResolvedMetadata(
        "10.1234/example", "A Safe Paper", ("Schmitt",), 1999, "en", "crossref:doi", 0.99,
        paper_type="journal-article",
    )
    seen: list[Path] = []
    service = RenameService(
        lambda path: seen.append(path) or metadata,
        format_template=settings.format_template,
    )

    state = AppState(settings)
    with patch.object(state, "_service", return_value=service):
        assert state.scan_folder(chosen) == 1

    assert seen == [chosen / "target.pdf"]
    candidate = state.snapshot()["candidates"][0]
    assert candidate["status"] == "ready"
    assert candidate["destination_path"].endswith("Schmitt (1999). - A Safe Paper.pdf")
    assert state.settings.watch_folders == [str(tmp_path)]


def test_scan_folder_reports_missing_folder(tmp_path: Path) -> None:
    settings = Settings(
        watch_folders=[str(tmp_path)], history_dir=str(tmp_path / "logs")
    ).validate()
    state = AppState(settings)
    try:
        state.scan_folder(tmp_path / "ない")
    except ValueError as exc:
        assert "フォルダが見つかりません" in str(exc)
    else:  # pragma: no cover - 失敗時のみ
        raise AssertionError("存在しないフォルダはエラーにする")
