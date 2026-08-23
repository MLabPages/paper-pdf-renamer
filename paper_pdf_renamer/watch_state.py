from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .config import app_data_dir


def path_key(value: str | Path) -> str:
    """Windowsの大文字小文字差を吸収したパスキーを返す。"""

    return os.path.normcase(os.path.abspath(os.path.expanduser(str(value))))


def _folder_key(folder: str | Path, recursive: bool) -> str:
    return f"{path_key(folder)}|recursive={int(recursive)}"


class WatchManifest:
    """監視停止中に追加されたPDFを識別するためのローカル一覧。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else app_data_dir() / "watch-manifest.json"
        self._lock = threading.RLock()

    def load(self, folder: str | Path, recursive: bool) -> tuple[set[Path], set[Path]] | None:
        """(既知ファイル, 要確認中ファイル)を返す。未作成ならNone。"""

        with self._lock:
            payload = self._read()
            records = payload.get("folders")
            if not isinstance(records, dict):
                return None
            record = records.get(_folder_key(folder, recursive))
            if not isinstance(record, dict):
                return None
            known = self._paths(record.get("known"))
            pending = self._paths(record.get("pending"))
            return known, pending

    def save(
        self,
        folder: str | Path,
        recursive: bool,
        known: set[Path],
        pending: set[Path],
    ) -> None:
        with self._lock:
            payload = self._read()
            records = payload.setdefault("folders", {})
            if not isinstance(records, dict):
                records = {}
                payload["folders"] = records
            records[_folder_key(folder, recursive)] = {
                "folder": str(Path(folder)),
                "recursive": bool(recursive),
                "known": sorted(str(path) for path in known),
                "pending": sorted(str(path) for path in pending),
            }
            self._write(payload)

    def complete(self, source: str | Path, destination: str | Path) -> None:
        """候補を実行した後、元パスを保留から外して新パスを既知にする。"""

        with self._lock:
            source_key = path_key(source)
            destination_path = Path(destination)
            payload = self._read()
            records = payload.get("folders")
            if not isinstance(records, dict):
                return
            changed = False
            for record in records.values():
                if not isinstance(record, dict):
                    continue
                known = self._paths(record.get("known"))
                pending = self._paths(record.get("pending"))
                known_keys = {path_key(path) for path in known}
                pending_keys = {path_key(path) for path in pending}
                if source_key not in known_keys and source_key not in pending_keys:
                    continue
                known = {path for path in known if path_key(path) != source_key}
                pending = {path for path in pending if path_key(path) != source_key}
                known.add(destination_path)
                record["known"] = sorted(str(path) for path in known)
                record["pending"] = sorted(str(path) for path in pending)
                changed = True
            if changed:
                self._write(payload)

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {"version": 1, "folders": {}}
        return payload if isinstance(payload, dict) else {"version": 1, "folders": {}}

    @staticmethod
    def _paths(value: object) -> set[Path]:
        if not isinstance(value, list):
            return set()
        return {Path(str(item)) for item in value if str(item).strip()}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
