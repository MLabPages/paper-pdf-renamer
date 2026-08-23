from pathlib import Path

from paper_pdf_renamer.watch_state import WatchManifest, path_key


def test_manifest_keeps_pending_until_candidate_is_applied(tmp_path: Path):
    manifest = WatchManifest(tmp_path / "watch-manifest.json")
    folder = tmp_path / "papers"
    folder.mkdir()
    source = folder / "paper_ja.pdf"
    destination = folder / "Paper [ja].pdf"

    manifest.save(folder, False, {folder / "old.pdf"}, {source})
    known, pending = manifest.load(folder, False)
    assert source in pending
    assert folder / "old.pdf" in known

    manifest.complete(source, destination)
    known, pending = manifest.load(folder, False)
    assert destination in known
    assert source not in pending
    assert path_key(destination) in {path_key(value) for value in known}
