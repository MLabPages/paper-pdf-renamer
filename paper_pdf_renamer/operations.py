from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable

from .config import FORMAT_TEMPLATE, validate_format_template
from .filename import build_filename
from .history import HistoryLog
from .models import RenameCandidate, ResolvedMetadata
from .pdf_extract import extract_pdf

Resolver = Callable[[Path], ResolvedMetadata]


def _metadata_log(metadata: ResolvedMetadata) -> dict[str, object]:
    return {
        "doi": metadata.doi,
        "title": metadata.title,
        "first_author": metadata.first_author,
        "year": metadata.year,
        "metadata_source": metadata.source,
        "confidence": metadata.confidence,
        "reasons": metadata.reasons,
    }


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
            updated = replace(candidate, status="failed", reasons=["source-file-missing"])
            self._log(updated, "rename")
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

    def __init__(self, folder: str | Path, service: RenameService, stability_polls: int = 2, recursive: bool = False):
        self.folder = Path(folder)
        self.service = service
        self.stability_polls = max(2, stability_polls)
        self.recursive = recursive
        self._state: dict[Path, tuple[int, int, int]] = {}
        self._processed: set[Path] = set()
        self._started = False

    def poll(self) -> list[RenameCandidate]:
        paths = sorted(self.folder.rglob("*.pdf") if self.recursive else self.folder.glob("*.pdf"))
        current = set(path for path in paths if path.is_file())
        self._state = {path: state for path, state in self._state.items() if path in current}

        # 監視開始前からあるPDFは既存資産として扱い、自動変更しない。
        # 初回スナップショット後に現れたファイルだけを「新規」として処理する。
        if not self._started:
            for path in current:
                stat = path.stat()
                self._state[path] = (stat.st_mtime_ns, stat.st_size, self.stability_polls)
                self._processed.add(path)
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
            result = self.service.process(path, auto=True)
            results.append(result)
            self._processed.add(path)
            if result.destination_path:
                self._processed.add(result.destination_path)
                self._state.pop(path, None)
        return results

    def run(self, interval: float = 5.0, stop: Callable[[], bool] | None = None) -> None:
        while not (stop and stop()):
            self.poll()
            time.sleep(interval)
