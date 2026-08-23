from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .models import LocalEvidence

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_GENERIC_TITLES = {"untitled", "microsoft word", "adobe acrobat", "pdf"}


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    match = DOI_RE.search(value.strip())
    if not match:
        return None
    doi = match.group(0).rstrip(".,;:)]}>'\"")
    return doi.lower()


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    value = unicodedata.normalize("NFKC", value).replace("\x00", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def detect_language(*values: str | None) -> str | None:
    text = " ".join(value or "" for value in values)
    if not text:
        return None
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text):
        return "ja"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return None


def normalize_language(value: str | None) -> str | None:
    """CrossrefやPDFの言語コードをファイル名用の ``ja``/``en`` に揃える。"""

    if not value:
        return None
    normalized = value.strip().casefold().replace("_", "-")
    if normalized in {"ja", "jpn", "japanese"} or normalized.startswith("ja-"):
        return "ja"
    if normalized in {"en", "eng", "english"} or normalized.startswith("en-"):
        return "en"
    return None


def _split_authors(value: str | None) -> tuple[str, ...]:
    value = _clean(value)
    if not value:
        return ()
    value = re.sub(r"\s+(?:and|&|及び|、)\s+", ";", value, flags=re.IGNORECASE)
    # 「Lemon, Katherine」のような姓,名表記を二人の著者と誤認しない。
    # 区切りが明確なセミコロン／and／&／日本語読点だけを分割する。
    parts = re.split(r"\s*;\s*", value)
    parts = [part.strip() for part in parts if part.strip()]
    return tuple(parts[:20])


def _looks_like_author_line(value: str) -> bool:
    """Detect common author-line markers without treating a wrapped title as authors."""
    if re.search(r"[0-9¹²³⁴⁵⁶⁷⁸⁹⁰]", value):
        return True
    if re.search(r"[;&]", value):
        return True
    if re.search(r"\bet\s+al\.?\b", value, re.IGNORECASE):
        return True
    if re.search(r"\band\b", value, re.IGNORECASE):
        capitalized_words = re.findall(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]*\b", value)
        if len(capitalized_words) >= 3 and not re.search(r"\b(?:of|the|for|in|to|with|across|on|from|as|by)\b", value, re.IGNORECASE):
            return True
    if value.count(",") >= 2 and not re.search(r"\b(?:of|the|and|for|in|to|with|across|on|from|as|by)\b", value, re.IGNORECASE):
        return True
    return False


def _comma_separated_authors(value: str) -> tuple[str, ...]:
    """Split a comma-separated author line while leaving comma-formatted names intact."""
    parts = tuple(part.strip() for part in re.split(r"\s*,\s*", value) if part.strip())
    if len(parts) < 2:
        return ()
    if any(len(part.split()) > 6 for part in parts):
        return ()
    if any(not re.search(r"[A-ZÀ-ÖØ-Þ一-龯ぁ-んァ-ン]", part) for part in parts):
        return ()
    return parts[:20]


def _text_lines(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        line = _clean(line)
        if line and line not in lines:
            lines.append(line)
    return lines


def _looks_like_title(line: str) -> bool:
    lowered = line.casefold()
    if len(line) < 10 or len(line) > 240:
        return False
    if lowered in _GENERIC_TITLES or DOI_RE.search(line):
        return False
    if re.match(r"^(abstract|keywords?|要旨|キーワード|received|accepted|www\.|https?://)\b", lowered):
        return False
    if re.fullmatch(r"[\d\W]+", line):
        return False
    return True


def _guess_title_and_authors(text: str) -> tuple[str | None, tuple[str, ...]]:
    lines = _text_lines(text)[:80]
    title: str | None = None
    title_index = -1
    for index, line in enumerate(lines):
        if _looks_like_title(line):
            title, title_index = line, index
            break
    if not title:
        return None, ()

    # PDF text extraction often returns a centered, wrapped title as two or
    # more separate lines. Join title-looking continuation lines until the
    # author line or abstract starts, so Crossref title search can recover a
    # DOI even when the first page does not print one.
    title_parts = [title]
    author_index = title_index + 1
    while author_index < len(lines) and len(title_parts) < 4:
        line = lines[author_index]
        lowered = line.casefold()
        if (
            _looks_like_author_line(line)
            or DOI_RE.search(line)
            or "@" in line
            or lowered.startswith(("abstract", "keywords", "要旨", "キーワード", "introduction"))
            or not _looks_like_title(line)
        ):
            break
        title_parts.append(line)
        author_index += 1
    title = " ".join(title_parts)

    authors: tuple[str, ...] = ()
    for line in lines[author_index : author_index + 5]:
        if DOI_RE.search(line) or line.lower().startswith(("abstract", "keywords")):
            continue
        if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ一-龯ぁ-んァ-ン]", line) and len(line) <= 180:
            pieces = _split_authors(line)
            if len(pieces) == 1 and "," in line:
                pieces = _comma_separated_authors(line)
            if len(pieces) >= 2 or re.search(r"\b(?:and|et al)\b|、|，", line, re.I):
                authors = pieces
                break
            # A single author line is useful only when it resembles a name.
            if len(line.split()) <= 5 and not re.search(r"[.!?]", line):
                authors = pieces
                break
    return title, authors


def extract_pdf(path: str | Path, max_pages: int = 1) -> LocalEvidence:
    """PyMuPDFがあれば本文を読み、なければPDF bytesからDOIだけを試す。"""

    pdf_path = Path(path)
    if pdf_path.suffix.casefold() != ".pdf":
        raise ValueError(f"PDFではありません: {pdf_path}")
    raw = pdf_path.read_bytes()
    raw_text = raw.decode("latin-1", errors="ignore")
    doi = normalize_doi(raw_text)
    embedded_title = embedded_author = None
    embedded_year: int | None = None
    text = ""
    notes: list[str] = []

    try:
        import fitz  # type: ignore
    except ImportError:
        notes.append("PyMuPDF未導入のため本文抽出なし")
    else:
        try:
            with fitz.open(stream=raw, filetype="pdf") as document:
                metadata = document.metadata or {}
                embedded_title = _clean(metadata.get("title"))
                embedded_author = _clean(metadata.get("author"))
                date = _clean(metadata.get("creationDate"))
                year_match = re.search(r"(19|20)\d{2}", date or "")
                embedded_year = int(year_match.group(0)) if year_match else None
                for page in list(document)[:max_pages]:
                    text += "\n" + page.get_text("text")
        except Exception as exc:  # malformed/encrypted PDFs remain safely held
            notes.append(f"PDF本文抽出失敗: {type(exc).__name__}")

    if not doi:
        doi = normalize_doi(text)
    title = embedded_title if embedded_title and embedded_title.casefold() not in _GENERIC_TITLES else None
    authors = _split_authors(embedded_author)
    guessed_title, guessed_authors = _guess_title_and_authors(text)
    if not title:
        title = guessed_title
    if not authors:
        authors = guessed_authors
    if not title and not authors and not doi:
        notes.append("DOI・タイトル・著者候補を検出できませんでした")
    source = "pdf-embedded" if embedded_title or embedded_author else "pdf-text" if text else "pdf-bytes"
    return LocalEvidence(
        path=pdf_path,
        doi=doi,
        title=title,
        authors=authors,
        year=embedded_year,
        language=normalize_language(detect_language(title, *authors)),
        metadata_source=source,
        notes=tuple(notes),
    )
