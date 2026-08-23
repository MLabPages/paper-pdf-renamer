from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LocalEvidence:
    """PDF内から得た、まだ外部検証されていない証拠。"""

    path: Path
    doi: str | None = None
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    language: str | None = None
    metadata_source: str = "none"
    notes: tuple[str, ...] = ()
    translation_marker: bool = False

    @property
    def first_author(self) -> str | None:
        return self.authors[0] if self.authors else None


@dataclass(frozen=True)
class ResolvedMetadata:
    """Crossref等で照合した書誌情報と安全判定。"""

    doi: str | None
    title: str | None
    authors: tuple[str, ...]
    year: int | None
    language: str | None
    source: str
    confidence: float
    reasons: tuple[str, ...] = ()
    paper_type: str | None = None
    local: LocalEvidence | None = None
    document_language: str | None = None
    translated: bool = False

    @property
    def first_author(self) -> str | None:
        return self.authors[0] if self.authors else None

    @property
    def safe_to_rename(self) -> bool:
        return not self.reasons and self.confidence >= 0.90

    def to_dict(self) -> dict[str, Any]:
        return {
            "doi": self.doi,
            "title": self.title,
            "authors": list(self.authors),
            "first_author": self.first_author,
            "year": self.year,
            "language": self.language,
            "metadata_source": self.source,
            "confidence": round(self.confidence, 4),
            "reasons": list(self.reasons),
            "paper_type": self.paper_type,
            "document_language": self.document_language,
            "translated": self.translated,
        }


@dataclass
class RenameCandidate:
    source_path: Path
    destination_path: Path | None
    metadata: ResolvedMetadata
    status: str
    reasons: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.destination_path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "destination_path": str(self.destination_path) if self.destination_path else None,
            "status": self.status,
            "reasons": list(self.reasons),
            "metadata": self.metadata.to_dict(),
        }
