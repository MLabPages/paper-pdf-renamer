from __future__ import annotations

from pathlib import Path

from .history import HistoryLog


def undo_last(history: HistoryLog) -> dict[str, object]:
    """直近の成功したrenameを、元ファイルが空いている場合だけ戻す。"""

    undone_paths = {
        str(record.get("original_path"))
        for record in history.read()
        if record.get("action") == "undo" and record.get("status") == "undone"
    }
    for record in reversed(history.read()):
        if record.get("action") != "rename" or record.get("status") != "renamed":
            continue
        new_path = Path(str(record.get("new_path")))
        old_path = Path(str(record.get("original_path")))
        if str(new_path) in undone_paths:
            continue
        if not new_path.exists():
            return {"status": "failed", "reason": "new-file-missing", "new_path": str(new_path)}
        if old_path.exists():
            return {"status": "failed", "reason": "original-path-exists", "original_path": str(old_path)}
        try:
            new_path.rename(old_path)
        except Exception as exc:
            return {"status": "failed", "reason": f"undo-failed:{type(exc).__name__}", "error": str(exc)}
        history.append({
            "action": "undo", "status": "undone",
            "original_filename": new_path.name, "new_filename": old_path.name,
            "original_path": str(new_path), "new_path": str(old_path),
            "doi": record.get("doi"), "title": record.get("title"),
            "first_author": record.get("first_author"), "year": record.get("year"),
            "metadata_source": record.get("metadata_source"), "confidence": record.get("confidence"),
            "reasons": [],
        })
        return {"status": "undone", "original_path": str(new_path), "new_path": str(old_path)}
    return {"status": "failed", "reason": "no-successful-rename"}
