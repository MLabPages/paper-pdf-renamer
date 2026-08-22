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
        item["timestamp"] = item["timestamp"] or datetime.now(timezone.utc).isoformat()
        if isinstance(item.get("reasons"), (list, tuple)):
            item["reasons"] = "; ".join(str(reason) for reason in item["reasons"])
        with self.json_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        exists = self.csv_path.exists()
        with self.csv_path.open("a", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=LOG_FIELDS)
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
