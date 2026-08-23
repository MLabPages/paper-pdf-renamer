from pathlib import Path

from paper_pdf_renamer.instance import SingleInstanceLock


def test_single_instance_lock_is_exclusive(tmp_path: Path):
    path = tmp_path / "gui.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()
