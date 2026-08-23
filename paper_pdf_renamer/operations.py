from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import FORMAT_TEMPLATE, validate_format_template
from .filename import build_filename
from .history import HistoryLog
from .models import RenameCandidate, ResolvedMetadata
from .pdf_extract import extract_pdf, has_translation_marker
from .watch_state import WatchManifest, path_key

Resolver = Callable[[Path], ResolvedMetadata]


def _metadata_log(metadata: ResolvedMetadata) -> dict[str, object]:
    return {
        "doi": metadata.doi,
        "title": metadata.title,
        "first_author": metadata.first_author,
        "authors": metadata.authors,
        "year": metadata.year,
        "language": metadata.language,
        "metadata_source": metadata.source,
        "confidence": metadata.confidence,
        "reasons": metadata.reasons,
        "paper_type": metadata.paper_type,
        "document_language": metadata.document_language,
        "translated": metadata.translated,
    }


def metadata_from_history(record: dict[str, Any]) -> ResolvedMetadata | None:
    """履歴に保存した書誌情報から、再整理用のメタデータを復元する。

    古い履歴には著者配列と言語がないため、当時のファイル名に含まれる
    ``et al.`` / ``ほか`` も補助的に使う。情報が足りない履歴は無理に
    推測せず、再整理候補にしない。
    """

    title = str(record.get("title") or "").strip()
    first_author = str(record.get("first_author") or "").strip()
    if not title or not first_author:
        return None
    try:
        year = int(record.get("year"))
    except (TypeError, ValueError):
        return None
    try:
        confidence = float(record.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    raw_authors = record.get("authors")
    authors = tuple(str(value).strip() for value in raw_authors if str(value).strip()) if isinstance(raw_authors, (list, tuple)) else ()
    old_names = " ".join(str(record.get(key) or "") for key in ("original_filename", "new_filename"))
    language = str(record.get("language") or "").strip() or None
    if not authors:
        multiple = " et al." in old_names.casefold() or "ほか" in old_names
        authors = (first_author, "履歴上の著者") if multiple else (first_author,)
        if language is None and "ほか" in old_names:
            language = "ja"
    if not authors:
        return None

    reasons_value = record.get("reasons")
    if isinstance(reasons_value, str):
        reasons = tuple(value.strip() for value in reasons_value.split(";") if value.strip())
    elif isinstance(reasons_value, (list, tuple)):
        reasons = tuple(str(value) for value in reasons_value if str(value).strip())
    else:
        reasons = ()
    return ResolvedMetadata(
        doi=str(record.get("doi") or "").strip() or None,
        title=title,
        authors=authors,
        year=year,
        language=language,
        source=str(record.get("metadata_source") or "history"),
        confidence=confidence,
        reasons=reasons,
        paper_type=str(record.get("paper_type") or "") or None,
        document_language=str(record.get("document_language") or language or "") or None,
        translated=bool(record.get("translated"))
        or has_translation_marker(record.get("original_filename"))
        or has_translation_marker(record.get("new_filename")),
    )


class RenameService:
    def __init__(
        self,
        resolver: Resolver,
        history: HistoryLog | None = None,
        min_confidence: float = 0.90,
        max_title_length: int = 100,
        format_template: str = FORMAT_TEMPLATE,
    ):
        self.resolver = resolver
        self.history = history
        self.min_confidence = min_confidence
        self.max_title_length = max_title_length
        self.format_template = validate_format_template(format_template)

    def make_candidate(self, path: str | Path) -> RenameCandidate:
        source = Path(path)
        try:
            metadata = self.resolver(source)
        except Exception as exc:
            metadata = ResolvedMetadata(
                doi=None, title=None, authors=(), year=None, language=None,
                source="error", confidence=0.0,
                reasons=(f"metadata-extraction-failed:{type(exc).__name__}",),
            )
            return RenameCandidate(source, None, metadata, "failed", list(metadata.reasons))
        return self.make_candidate_from_metadata(source, metadata)

    def make_candidate_from_metadata(self, path: str | Path, metadata: ResolvedMetadata) -> RenameCandidate:
        """既存の検証済みメタデータだけで再整理候補を作る。"""

        source = Path(path)
        reasons = list(metadata.reasons)
        if metadata.confidence < self.min_confidence and "low-confidence" not in reasons:
            reasons.append("low-confidence")
        if reasons or not metadata.safe_to_rename:
            return RenameCandidate(source, None, metadata, "held", list(dict.fromkeys(reasons)))
        try:
            destination = build_filename(
                metadata, source=source, max_title_length=self.max_title_length,
                format_template=self.format_template,
            )
        except Exception as exc:
            return RenameCandidate(source, None, metadata, "held", [f"filename-generation-failed:{type(exc).__name__}"])
        if destination.resolve() == source.resolve():
            return RenameCandidate(source, destination, metadata, "held", ["already-correct-name"])
        return RenameCandidate(source, destination, metadata, "ready", [])

    def rename_candidate(self, candidate: RenameCandidate) -> RenameCandidate:
        if not candidate.ready:
            return candidate
        source, destination = candidate.source_path, candidate.destination_path
        assert destination is not None
        if not source.exists():
            recovered = self._recorded_destination(source)
            if recovered is not None:
                # A second request can arrive after another request already
                # completed this rename. Treat it as idempotent instead of
                # showing a misleading failure.
                updated = replace(candidate, destination_path=recovered, status="renamed", reasons=[])
                self._log(updated, "rename")
                return updated
            # The source disappeared without a matching successful record.
            # Do not report a false rename failure or retry it automatically.
            updated = replace(candidate, status="held", reasons=["source-file-missing"])
            self._log(updated, "hold")
            return updated
        destination = build_filename(
            candidate.metadata, directory=source.parent, source=source,
            max_title_length=self.max_title_length, format_template=self.format_template,
        )
        try:
            # Path.renameは既存先への上書きを避けるため、事前に再確認する。
            if destination.exists() and destination.resolve() != source.resolve():
                destination = build_filename(
                    candidate.metadata, directory=source.parent, source=source,
                    max_title_length=self.max_title_length, format_template=self.format_template,
                )
            if destination.exists() and destination.resolve() != source.resolve():
                raise FileExistsError(str(destination))
            source.rename(destination)
        except Exception as exc:
            updated = replace(candidate, status="failed", reasons=[f"rename-failed:{type(exc).__name__}"])
            self._log(updated, "rename", error=str(exc))
            return updated
        updated = replace(candidate, destination_path=destination, status="renamed")
        self._log(updated, "rename")
        return updated

    def _recorded_destination(self, source: Path) -> Path | None:
        if not self.history:
            return None
        source_key = _path_key(source)
        for record in reversed(self.history.read()):
            action = record.get("action")
            if action == "undo" and record.get("status") == "undone" and _path_key(record.get("new_path")) == source_key:
                return None
            if action != "rename" or record.get("status") != "renamed":
                continue
            if _path_key(record.get("original_path")) != source_key:
                continue
            destination = Path(str(record.get("new_path") or ""))
            return destination if destination.is_file() else None
        return None

    def process(self, path: str | Path, auto: bool = True) -> RenameCandidate:
        candidate = self.make_candidate(path)
        if auto and candidate.ready:
            return self.rename_candidate(candidate)
        if auto:
            self._log(candidate, "hold" if candidate.status == "held" else "failed")
        return candidate

    def _log(self, candidate: RenameCandidate, action: str, error: str | None = None) -> None:
        if not self.history:
            return
        metadata = _metadata_log(candidate.metadata)
        if candidate.reasons:
            metadata["reasons"] = tuple(candidate.reasons)
        self.history.append({
            "action": action,
            "status": candidate.status,
            "original_filename": candidate.source_path.name,
            "new_filename": candidate.destination_path.name if candidate.destination_path else None,
            "original_path": str(candidate.source_path),
            "new_path": str(candidate.destination_path) if candidate.destination_path else None,
            **metadata,
            "error": error,
        })


def _path_key(value: object) -> str:
    if not value:
        return ""
    try:
        return str(Path(str(value)).resolve()).casefold()
    except (OSError, RuntimeError, ValueError):
        return str(value).casefold()


def default_resolver(path: Path) -> ResolvedMetadata:
    from .crossref import resolve_metadata

    return resolve_metadata(extract_pdf(path))


class BatchScanner:
    def __init__(self, service: RenameService):
        self.service = service

    def scan(self, root: str | Path, recursive: bool = True) -> list[RenameCandidate]:
        root_path = Path(root)
        iterator: Iterable[Path] = root_path.rglob("*.pdf") if recursive else root_path.glob("*.pdf")
        return [self.service.make_candidate(path) for path in sorted(iterator) if path.is_file()]

    @staticmethod
    def save_plan(candidates: list[RenameCandidate], path: str | Path) -> None:
        Path(path).write_text(json.dumps({"items": [item.to_dict() for item in candidates]}, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def load_plan(path: str | Path) -> list[RenameCandidate]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        result = []
        for item in payload.get("items", []):
            data = item["metadata"]
            metadata = ResolvedMetadata(
                doi=data.get("doi"), title=data.get("title"), authors=tuple(data.get("authors", [])),
                year=data.get("year"), language=data.get("language"), source=data.get("metadata_source", "plan"),
                confidence=float(data.get("confidence", 0)), reasons=tuple(data.get("reasons", [])),
                paper_type=data.get("paper_type"),
                document_language=data.get("document_language"),
                translated=bool(data.get("translated")),
            )
            result.append(RenameCandidate(
                source_path=Path(item["source_path"]),
                destination_path=Path(item["destination_path"]) if item.get("destination_path") else None,
                metadata=metadata, status=item.get("status", "held"), reasons=list(item.get("reasons", [])),
            ))
        return result

    def execute_approved(self, candidates: list[RenameCandidate], approved_paths: Iterable[str | Path]) -> list[RenameCandidate]:
        approved = {str(Path(path).resolve()).casefold() for path in approved_paths}
        results: list[RenameCandidate] = []
        for candidate in candidates:
            if str(candidate.source_path.resolve()).casefold() not in approved:
                continue
            if candidate.ready:
                results.append(self.service.rename_candidate(candidate))
            else:
                self.service._log(candidate, "batch-apply")
                results.append(candidate)
        return results


class PollingWatcher:
    """OS依存の常駐APIを使わない、完成済みPDFだけを処理するポーラー。"""

    def __init__(
        self,
        folder: str | Path,
        service: RenameService,
        stability_polls: int = 2,
        recursive: bool = False,
        manifest: WatchManifest | None = None,
    ):
        self.folder = Path(folder)
        self.service = service
        self.stability_polls = max(2, stability_polls)
        self.recursive = recursive
        self.manifest = manifest
        self._state: dict[Path, tuple[int, int, int]] = {}
        self._processed: set[Path] = set()
        self._known: dict[str, Path] = {}
        self._startup_pending: dict[str, Path] = {}
        self._started = False

    def poll(self) -> list[RenameCandidate]:
        paths = sorted(self.folder.rglob("*.pdf") if self.recursive else self.folder.glob("*.pdf"))
        current = set(path for path in paths if path.is_file())
        current_keys = {path_key(path) for path in current}
        self._state = {path: state for path, state in self._state.items() if path in current}
        self._known = {key: path for key, path in self._known.items() if key in current_keys}
        self._startup_pending = {
            key: path for key, path in self._startup_pending.items() if key in current_keys
        }

        # 監視開始前からあるPDFは既存資産として扱い、自動変更しない。
        # 前回の一覧がある場合は、停止中に追加されたPDFだけを要確認候補にする。
        if not self._started:
            saved = self.manifest.load(self.folder, self.recursive) if self.manifest else None
            if saved is None:
                self._known = {path_key(path): path for path in current}
                self._startup_pending.clear()
                for path in current:
                    stat = path.stat()
                    self._state[path] = (stat.st_mtime_ns, stat.st_size, self.stability_polls)
                    self._processed.add(path)
                self._persist_manifest()
                self._started = True
                return []

            known, pending = saved
            known_keys = {path_key(path) for path in known}
            pending_keys = {path_key(path) for path in pending}
            self._known = {path_key(path): path for path in current if path_key(path) in known_keys}
            self._startup_pending = {
                path_key(path): path
                for path in current
                if path_key(path) not in known_keys or path_key(path) in pending_keys
            }
            for path in current:
                stat = path.stat()
                self._state[path] = (stat.st_mtime_ns, stat.st_size, self.stability_polls)
                if path_key(path) not in self._startup_pending:
                    self._processed.add(path)
            self._persist_manifest()
            self._started = True
            return []

        results: list[RenameCandidate] = []
        for path in paths:
            if not path.is_file() or path in self._processed:
                continue
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            previous = self._state.get(path)
            count = previous[2] + 1 if previous and previous[:2] == signature else 1
            self._state[path] = (*signature, count)
            if count < self.stability_polls:
                continue
            key = path_key(path)
            # 停止中に追加されたPDFは、再起動後に候補だけ作る。
            # 通常の監視中に追加されたPDFだけが従来どおり自動処理される。
            result = self.service.process(path, auto=key not in self._startup_pending)
            if key in self._startup_pending:
                result = replace(
                    result,
                    reasons=list(dict.fromkeys([*result.reasons, "added-while-stopped"])),
                )
            results.append(result)
            self._processed.add(path)
            if key in self._startup_pending:
                # ユーザーが候補を実行するまで、次回起動でも再表示できるよう
                # pendingに残す。現在のプロセスでは重複表示しない。
                pass
            else:
                self._known[key] = path
            if result.status == "renamed" and result.destination_path and result.destination_path.is_file():
                self._known.pop(key, None)
                self._known[path_key(result.destination_path)] = result.destination_path
                self._processed.add(result.destination_path)
                self._state.pop(path, None)
        self._persist_manifest()
        return results

    def mark_completed(self, source: str | Path, destination: str | Path) -> None:
        """UIから承認された候補を既知ファイルとして反映する。"""

        source_key = path_key(source)
        if source_key not in self._startup_pending and source_key not in self._known:
            return
        self._startup_pending.pop(source_key, None)
        self._known.pop(source_key, None)
        destination_path = Path(destination)
        self._known[path_key(destination_path)] = destination_path
        self._processed.add(destination_path)
        self._state.pop(Path(source), None)
        self._persist_manifest()

    def _persist_manifest(self) -> None:
        if not self.manifest:
            return
        self.manifest.save(
            self.folder,
            self.recursive,
            set(self._known.values()),
            set(self._startup_pending.values()),
        )

    def run(self, interval: float = 5.0, stop: Callable[[], bool] | None = None) -> None:
        while not (stop and stop()):
            self.poll()
            time.sleep(interval)
