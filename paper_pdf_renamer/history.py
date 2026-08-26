from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG_FIELDS = [
    "timestamp", "action", "status", "original_filename", "new_filename", "original_path", "new_path",
    "doi", "title", "first_author", "year", "metadata_source", "confidence", "reasons", "error",
]


class HistoryLog:
    """JSONLとCSVの両方に、人間が確認できる履歴を追記する。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.json_path = self.directory / "history.jsonl"
        self.csv_path = self.directory / "history.csv"
        self.directory.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        item = {field: record.get(field) for field in LOG_FIELDS}
        # JSONLには、後から同じ書誌情報で再生成できる補助情報も保持する。
        # 既存CSVの列構成は変えず、古い履歴との互換性を優先する。
        for field in ("authors", "language", "paper_type", "document_language", "translated", "warnings"):
            if field in record:
                item[field] = record[field]
        item["timestamp"] = item["timestamp"] or datetime.now(timezone.utc).isoformat()
        if isinstance(item.get("reasons"), (list, tuple)):
            item["reasons"] = "; ".join(str(reason) for reason in item["reasons"])
        with self.json_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        exists = self.csv_path.exists()
        with self.csv_path.open("a", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=LOG_FIELDS, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(item)
        return item

    def read(self) -> list[dict[str, Any]]:
        if not self.json_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.json_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    def latest_successful_renames(self) -> list[dict[str, Any]]:
        """現在のファイルに対応する、最新の成功リネーム履歴だけを返す。

        同じPDFを複数回リネームした場合は古い履歴を除外し、Undo済みの
        リネームも除外する。履歴に残った書誌情報を使うため、PDF本文や
        外部APIを再処理せずにファイル名形式だけを更新できる。
        """

        records = self.read()
        undone_paths = {
            _path_key(record.get("original_path"))
            for record in records
            if record.get("action") == "undo" and record.get("status") == "undone"
        }
        superseded_sources: set[str] = set()
        latest: list[dict[str, Any]] = []
        for record in reversed(records):
            if record.get("action") != "rename" or record.get("status") != "renamed":
                continue
            original_path = record.get("original_path")
            new_path = record.get("new_path")
            if not original_path or not new_path:
                continue
            if _path_key(new_path) in undone_paths or _path_key(new_path) in superseded_sources:
                superseded_sources.add(_path_key(original_path))
                continue
            latest.append(record)
            superseded_sources.add(_path_key(original_path))
        latest.reverse()
        return latest

    def latest_held_reviews(self) -> list[dict[str, Any]]:
        """Return the latest held record for each unchanged source path.

        These records are not trusted as reusable metadata. The GUI uses only
        their source paths to run the current PDF extraction and verification
        logic again without changing any file.
        """

        latest: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for record in reversed(self.read()):
            if record.get("status") != "held":
                continue
            original_path = record.get("original_path")
            path_key = _path_key(original_path)
            if not path_key or path_key in seen_paths:
                continue
            latest.append(record)
            seen_paths.add(path_key)
        latest.reverse()
        return latest


def _path_key(value: object) -> str:
    if not value:
        return ""
    try:
        return str(Path(str(value)).resolve()).casefold()
    except (OSError, RuntimeError, ValueError):
        return str(value).casefold()
