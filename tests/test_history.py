from pathlib import Path

from paper_pdf_renamer.history import HistoryLog


def _rename_record(source: Path, destination: Path) -> dict[str, object]:
    return {
        "action": "rename",
        "status": "renamed",
        "original_filename": source.name,
        "new_filename": destination.name,
        "original_path": str(source),
        "new_path": str(destination),
        "doi": "10.1234/example",
        "title": "A Safe Paper",
        "first_author": "Schmitt",
        "authors": ("Schmitt", "Lemon"),
        "year": 1999,
        "language": "en",
        "metadata_source": "crossref:doi",
        "confidence": 0.99,
    }


def test_latest_successful_renames_ignores_superseded_and_undone(tmp_path: Path):
    history = HistoryLog(tmp_path / "logs")
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    third = tmp_path / "third.pdf"
    history.append(_rename_record(first, second))
    history.append(_rename_record(second, third))
    latest = history.latest_successful_renames()
    assert [item["new_path"] for item in latest] == [str(third)]

    history.append({
        "action": "undo",
        "status": "undone",
        "original_path": str(third),
        "new_path": str(second),
    })
    assert history.latest_successful_renames() == []


def test_extra_metadata_is_kept_in_json_history(tmp_path: Path):
    history = HistoryLog(tmp_path / "logs")
    item = history.append(_rename_record(tmp_path / "a.pdf", tmp_path / "b.pdf"))
    assert item["authors"] == ("Schmitt", "Lemon")
    stored = history.read()[0]
    assert stored["authors"] == ["Schmitt", "Lemon"]
    assert stored["language"] == "en"


def test_latest_held_reviews_keeps_only_latest_record_per_source(tmp_path: Path):
    history = HistoryLog(tmp_path / "logs")
    source = tmp_path / "30-1-72.pdf"
    for confidence in (0.2, 0.8):
        history.append({
            "action": "hold",
            "status": "held",
            "original_filename": source.name,
            "original_path": str(source),
            "title": "Review me",
            "confidence": confidence,
        })

    latest = history.latest_held_reviews()
    assert len(latest) == 1
    assert latest[0]["original_path"] == str(source)
    assert latest[0]["confidence"] == 0.8
