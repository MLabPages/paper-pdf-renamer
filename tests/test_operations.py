from pathlib import Path

from paper_pdf_renamer.history import HistoryLog
from paper_pdf_renamer.models import ResolvedMetadata
from paper_pdf_renamer.operations import BatchScanner, PollingWatcher, RenameService, metadata_from_history
from paper_pdf_renamer.undo import undo_last
from paper_pdf_renamer.watch_state import WatchManifest


def good_metadata():
    return ResolvedMetadata("10.1234/good", "A Safe Paper", ("Schmitt",), 1999, "en", "crossref:doi", 0.98)


def test_auto_rename_logs_and_undoes(tmp_path: Path):
    source = tmp_path / "download.pdf"
    source.write_bytes(b"pdf")
    history = HistoryLog(tmp_path / "logs")
    service = RenameService(lambda path: good_metadata(), history=history)
    result = service.process(source)
    assert result.status == "renamed"
    assert result.destination_path and result.destination_path.exists()
    assert not source.exists()
    assert history.read()[0]["doi"] == "10.1234/good"
    undone = undo_last(history)
    assert undone["status"] == "undone"
    assert source.exists()


def test_low_confidence_is_not_renamed(tmp_path: Path):
    source = tmp_path / "download.pdf"
    source.write_bytes(b"pdf")
    held = ResolvedMetadata("10.1234/low", "Unsafe", ("A",), 2020, "en", "local", 0.50, ("low-confidence",))
    result = RenameService(lambda path: held).process(source)
    assert result.status == "held"
    assert source.exists()


def test_duplicate_request_after_success_is_idempotent(tmp_path: Path):
    source = tmp_path / "download.pdf"
    source.write_bytes(b"pdf")
    history = HistoryLog(tmp_path / "logs")
    service = RenameService(lambda path: good_metadata(), history=history)
    first = service.process(source)
    assert first.status == "renamed"

    # Simulate a second HTTP click or watcher request using the old candidate.
    duplicate = service.make_candidate_from_metadata(source, good_metadata())
    recovered = service.rename_candidate(duplicate)
    assert recovered.status == "renamed"
    assert recovered.destination_path == first.destination_path
    assert not any(item["status"] == "failed" for item in history.read())


def test_history_metadata_can_create_candidate_for_new_format(tmp_path: Path):
    source = tmp_path / "download.pdf"
    source.write_bytes(b"pdf")
    history = HistoryLog(tmp_path / "logs")
    original = RenameService(lambda path: good_metadata(), history=history)
    renamed = original.process(source)
    assert renamed.status == "renamed"

    record = history.latest_successful_renames()[0]
    restored = metadata_from_history(record)
    assert restored is not None
    updated = RenameService(
        lambda path: good_metadata(),
        format_template="{author} ({year}). - {title}.pdf",
    ).make_candidate_from_metadata(renamed.destination_path, restored)
    assert updated.status == "ready"
    assert updated.destination_path and updated.destination_path.name == "Schmitt (1999). - A Safe Paper.pdf"


def test_batch_scan_is_preview_only_until_explicit_approval(tmp_path: Path):
    first = tmp_path / "one.pdf"
    second = tmp_path / "two.pdf"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    service = RenameService(lambda path: good_metadata())
    scanner = BatchScanner(service)
    candidates = scanner.scan(tmp_path, recursive=False)
    assert len(candidates) == 2
    assert first.exists() and second.exists()
    results = scanner.execute_approved(candidates, [first])
    assert len(results) == 1
    assert not first.exists()
    assert second.exists()


def test_watcher_waits_for_two_stable_polls(tmp_path: Path):
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"existing")
    watcher = PollingWatcher(tmp_path, RenameService(lambda path: good_metadata()), stability_polls=2)
    # 監視開始前からあるPDFは既存資産として変更しない。
    assert watcher.poll() == []
    assert watcher.poll() == []
    assert watcher.poll() == []
    assert existing.exists()

    source = tmp_path / "download.pdf"
    source.write_bytes(b"pdf")
    assert watcher.poll() == []
    results = watcher.poll()
    assert len(results) == 1
    assert results[0].status == "renamed"
    assert not source.exists()
    assert watcher.poll() == []


def test_pdf_added_while_monitor_was_off_becomes_preview_candidate(tmp_path: Path):
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"existing")
    manifest = WatchManifest(tmp_path / "watch-manifest.json")
    service = RenameService(lambda path: good_metadata())

    first_session = PollingWatcher(tmp_path, service, stability_polls=2, manifest=manifest)
    assert first_session.poll() == []

    downloaded_while_off = tmp_path / "downloaded.pdf"
    downloaded_while_off.write_bytes(b"pdf")

    restarted = PollingWatcher(tmp_path, service, stability_polls=2, manifest=manifest)
    assert restarted.poll() == []
    results = restarted.poll()
    assert len(results) == 1
    assert results[0].status == "ready"
    assert "added-while-stopped" in results[0].reasons
    assert downloaded_while_off.exists()
    assert results[0].destination_path and not results[0].destination_path.exists()


def test_first_manifest_snapshot_does_not_change_existing_pdfs(tmp_path: Path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    manifest = WatchManifest(tmp_path / "watch-manifest.json")
    watcher = PollingWatcher(tmp_path, RenameService(lambda path: good_metadata()), manifest=manifest)
    assert watcher.poll() == []
    assert first.exists() and second.exists()
    known, pending = manifest.load(tmp_path, False)
    assert {path.name for path in known} == {"first.pdf", "second.pdf"}
    assert not pending
