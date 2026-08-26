from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .models import LocalEvidence

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_GENERIC_TITLES = {"untitled", "microsoft word", "adobe acrobat", "pdf"}
_GENERIC_AUTHORS = {"author", "authors", "research", "unknown", "none", "n/a"}
_TRANSLATION_MARKER_RE = re.compile(
    r"(?:^|[\s._()\[\]-])(?:ja|jpn|japanese|日本語|日本語訳|翻訳|translated|translation)(?:$|[\s._()\[\]-])",
    re.IGNORECASE,
)


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    match = DOI_RE.search(value.strip())
    if not match:
        return None
    doi = match.group(0)
    # XMP/RDF strings can leave a serialized container suffix directly after
    # the DOI, for example ``10.3389/fpsyg.2021.722108)/s/uri``. It is not part
    # of the identifier and makes an otherwise valid DOI return HTTP 404.
    doi = re.sub(r"\)/s/(?:uri|li|bag|seq|alt)\b.*$", "", doi, flags=re.IGNORECASE)
    doi = doi.rstrip(".,;:)]}>'\"")
    return doi.lower()


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    value = unicodedata.normalize("NFKC", value)
    # PDFの埋め込み欄には、BOMやフォント用の制御文字が混ざることがある。
    value = "".join(" " if unicodedata.category(char) in {"Cc", "Cf"} else char for char in value)
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


def detect_document_language(text: str | None) -> str | None:
    """本文全体の文字量から、翻訳版判定に使う言語を推定する。"""

    if not text:
        return None
    japanese = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    # 著者名や所属に含まれる少数の漢字で英語論文を誤判定しない。
    if japanese >= 80 and (japanese >= latin * 0.03 or japanese >= 300):
        return "ja"
    if latin >= 80 and japanese < 80:
        return "en"
    return None


def has_translation_marker(value: str | Path | None) -> bool:
    """ファイル名に翻訳版を示す一般的な印があるか確認する。"""

    if not value:
        return False
    return bool(_TRANSLATION_MARKER_RE.search(Path(str(value)).stem))


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


_AUTHOR_NAME_STOP_WORDS = {
    "across", "and", "as", "brand", "case", "consumer", "dimensions", "experience",
    "for", "from", "in", "items", "measures", "of", "on", "organizations", "research",
    "service", "the", "this", "to", "with",
}


def _looks_like_person_name(value: str) -> bool:
    """Detect a short visible author name without classifying title lines as authors."""

    if not value or len(value) > 120:
        return False
    if re.search(r"[@]|\b(?:department|university|school of|e-mail|email)\b", value, re.IGNORECASE):
        return False
    cleaned = re.sub(r"[0-9¹²³⁴⁵⁶⁷⁸⁹⁰*†‡]+", " ", value)
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", cleaned)
    if not 2 <= len(words) <= 5:
        return False
    if any(word.casefold() in _AUTHOR_NAME_STOP_WORDS for word in words):
        return False
    return all(word[:1].isupper() for word in words)


def _looks_like_author_line(value: str) -> bool:
    """Detect common author-line markers without treating a wrapped title as authors."""
    if re.search(r"\b(?:department|university|school of|hertfordshire|united kingdom|e-mail|email)\b", value, re.IGNORECASE):
        return False
    if re.search(r"\bet\s+al\.?\b", value, re.IGNORECASE) and not re.search(r"[\[\]]", value):
        return True
    if _looks_like_person_name(value):
        return True
    if re.search(r"\band\b", value, re.IGNORECASE):
        capitalized_words = re.findall(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]*\b", value)
        if (
            len(value) <= 180
            and len(capitalized_words) >= 3
            and (value.count(",") >= 1 or value.count(".") <= 2)
            and not re.search(r"\b(?:of|the|for|in|to|with|across|on|from|as|by)\b", value, re.IGNORECASE)
        ):
            return True
    if (
        re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ一-龯ぁ-んァ-ン][0-9¹²³⁴⁵⁶⁷⁸⁹⁰]", value)
        and not re.search(r"[\[\]]", value)
        and len(value) <= 180
        and len(re.findall(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]*\b", value)) >= 2
    ):
        return True
    if (
        value.count(",") >= 2
        and len(value) <= 180
        and len(re.findall(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]*\b", value)) >= 3
        and not re.search(r"\b(?:19|20)\d{2}\b", value)
        and not re.search(r"\b(?:of|the|and|for|in|to|with|across|on|from|as|by)\b", value, re.IGNORECASE)
    ):
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
    if re.search(r"(?:https?://|www\.|^[\w.-]+/)", lowered):
        return False
    if re.match(
        r"^(abstract|keywords?|要旨|キーワード|received|accepted|www\.|cite this article|"
        r"subject areas|author for correspondence|electronic supplementary|downloaded from|"
        r"one contribution|part\s+[ivxlcdm]+|figure\s+|table\s+|references|copyright|license)\b",
        lowered,
    ):
        return False
    if re.match(r"^(?:[ivxlcdm]+[.)]?\s+)?(?:introduction|conclusion|discussion)\b", lowered):
        return False
    if re.search(r"\b(?:department|university|school of|e-mail|email)\b", lowered):
        return False
    letters = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", line)
    if letters and all(char.isupper() for char in letters):
        return False
    if re.fullmatch(r"[\d\W]+", line):
        return False
    return True


def _title_parts(lines: list[str], start: int) -> tuple[list[str], int]:
    """Return a wrapped title and the index immediately after it."""

    parts = [lines[start]]
    index = start + 1
    while index < len(lines) and len(parts) < 6:
        line = lines[index]
        lowered = line.casefold()
        author_line = _looks_like_author_line(line)
        if author_line:
            letters = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", line)
            all_caps_name = bool(letters) and all(char.isupper() for char in letters)
            next_line_is_author = index + 1 < len(lines) and _looks_like_author_line(lines[index + 1])
            # A title-cased final subtitle line such as "Emotional
            # Intelligence" may itself look like a two-word name. If it is
            # immediately followed by the real author line, keep it in the
            # title; all-caps name lines remain author boundaries.
            has_author_markers = bool(re.search(r"[0-9¹²³⁴⁵⁶⁷⁸⁹⁰]", line) or "," in line)
            if not all_caps_name and next_line_is_author and not has_author_markers:
                author_line = False
        continuation = _looks_like_title(line) or (
            4 <= len(line) <= 100
            and not re.search(r"[\[\]]", line)
            and not line.rstrip().endswith((".", ",", ";"))
            and not re.fullmatch(r"[\d\W]+", line)
            and not re.search(r"\b(?:department|university|school of|hertfordshire|united kingdom|e-mail|email)\b", lowered)
        )
        if (
            author_line
            or DOI_RE.search(line)
            or "@" in line
            or lowered.startswith((
                "abstract", "keywords", "要旨", "キーワード", "introduction", "received", "accepted",
                "cite this article", "downloaded from", "copyright", "journal of", "vol.", "volume",
            ))
            or not continuation
        ):
            break
        parts.append(line)
        index += 1
    return parts, index


def _title_candidate_score(lines: list[str], index: int, parts: list[str]) -> float:
    """Prefer heading-like text followed by an author line over body text."""

    line = lines[index]
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ一-龯ぁ-んァ-ン]+", line)
    score = min(len(words), 10) * 0.25 + len(parts) * 1.5
    if re.search(r"[A-ZÀ-ÖØ-Þ一-龯ぁ-んァ-ン]", line[:1]):
        score += 1.5
    if words and words[0].casefold() in {
        "while", "from", "in", "to", "the", "this", "one", "it", "when", "however",
        "we", "that", "as", "for", "and", "although", "given", "using", "based",
    }:
        score -= 5.5
    if re.search(r"\[[0-9]", line) or line.rstrip().endswith((".", ",", ";")):
        score -= 2.0
    if re.search(r"\[[0-9]", line):
        score -= 6.0
    if len(words) > 14:
        score -= 6.0
    if len(words) > 10:
        score -= 5.0
    # A real title is commonly followed by an author line, including after a
    # subtitle. This is the key signal for two-column PDFs where text order is
    # not the same as visual order.
    author_found = False
    for following in lines[index + len(parts) : index + len(parts) + 4]:
        if _looks_like_author_line(following):
            score += 7.0
            author_found = True
            break
    if not author_found:
        score -= 6.0
    if line[:1].islower():
        score -= 7.0
    return score


def _guess_title_and_authors(text: str) -> tuple[str | None, tuple[str, ...]]:
    # The title may be at the end of a first page (two-column extraction) or
    # on page 3 after a scanned chapter cover, so do not assume it is first.
    lines = _text_lines(text)[:500]
    candidates: list[tuple[float, int, list[str], int]] = []
    for index, line in enumerate(lines):
        if not _looks_like_title(line):
            continue
        parts, after_title = _title_parts(lines, index)
        candidates.append((_title_candidate_score(lines, index, parts), index, parts, after_title))
    if not candidates:
        return None, ()
    _, _, title_parts, author_index = max(candidates, key=lambda item: (item[0], -item[1]))
    title = " ".join(title_parts)

    authors: tuple[str, ...] = ()
    for line in lines[author_index : author_index + 8]:
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


def _extract_bibliographic_hints(text: str, path: Path | None = None) -> tuple[str | None, str | None, str | None]:
    """Extract optional volume/issue/page hints without treating them as proof."""

    volume = issue = pages = None
    volume_match = re.search(r"\bVol(?:ume)?\.?\s*(\d+)", text, re.IGNORECASE)
    issue_match = re.search(r"\b(?:No\.?|Issue)\s*(\d+)", text, re.IGNORECASE)
    citation_match = re.search(
        r"\(\s*(?:19|20)\d{2}\s*\)\s*(\d+)\s*,\s*(\d{1,5}(?:\s*[-–—]\s*\d{1,5})?)",
        text,
    )
    page_match = re.search(r"\b(?:pp?\.?|pages?)\s*(\d{1,5}(?:\s*[-–—]\s*\d{1,5})?)", text, re.IGNORECASE)
    if volume_match:
        volume = volume_match.group(1)
    if issue_match:
        issue = issue_match.group(1)
    if citation_match:
        volume = volume or citation_match.group(1)
        pages = citation_match.group(2).replace(" ", "")
    if page_match:
        pages = pages or page_match.group(1).replace(" ", "")
    if path is not None:
        filename_match = re.fullmatch(r"(\d+)-(\d+)-(\d+)", path.stem)
        if filename_match:
            volume = volume or filename_match.group(1)
            issue = issue or filename_match.group(2)
            pages = pages or filename_match.group(3)
    return volume, issue, pages


def _usable_embedded_title(value: str | None) -> str | None:
    value = _clean(value)
    if not value or value.casefold() in _GENERIC_TITLES:
        return None
    return value if _looks_like_title(value) else None


def _usable_embedded_author(value: str | None) -> str | None:
    value = _clean(value)
    if not value or value.casefold() in _GENERIC_AUTHORS:
        return None
    # Some publishers put an internal account name in /Author. Prefer visible
    # author text over a lone lowercase token such as ``khenglee``.
    if len(value.split()) == 1 and value.isascii() and value.islower():
        return None
    return value


def _layout_title(page_dicts: list[dict]) -> str | None:
    """Find a title from prominent first-page text blocks.

    PDF text stream order is often unrelated to the visible page order,
    especially for two-column journal layouts. Font size and page coordinates
    provide a separate local signal that is much less likely to select an
    abstract paragraph or a body heading as the article title.
    """

    candidates: list[tuple[float, int, float, float, str]] = []
    for page_index, page in enumerate(page_dicts[:3]):
        height = float(page.get("height") or 1.0)
        for block in page.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            spans = [span for line in block.get("lines", []) for span in line.get("spans", [])]
            text = _clean(" ".join(str(span.get("text") or "") for span in spans))
            sizes = [float(span.get("size") or 0.0) for span in spans if span.get("text")]
            if not text or not sizes:
                continue
            words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ一-龯ぁ-んァ-ン]+", text)
            letters = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", text)
            lowered = text.casefold()
            if len(words) < 3 or len(text) > 350:
                continue
            if letters and all(char.isupper() for char in letters):
                continue
            if DOI_RE.search(text) or re.search(r"https?://|www\.", lowered):
                continue
            if re.fullmatch(r"(?:article in press|original article|research|review|methods?)", lowered):
                continue
            y0 = float((block.get("bbox") or (0, 0, 0, 0))[1])
            if y0 > height * 0.60:
                continue
            max_size = max(sizes)
            score = max_size * 2.0 + min(len(words), 16) * 0.45 - page_index * 5.0
            candidates.append((score, page_index, y0, max_size, text))

    if not candidates:
        return None
    _, page_index, y0, size, title = max(candidates, key=lambda item: item[0])

    # Some publishers store each wrapped title line as a separate block.
    # Join only immediately adjacent blocks with essentially the same font.
    page = page_dicts[page_index]
    continuations: list[tuple[float, str]] = []
    for block in page.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        bbox = block.get("bbox") or (0, 0, 0, 0)
        block_y = float(bbox[1])
        if block_y <= y0 or block_y - y0 > size * 3.0:
            continue
        spans = [span for line in block.get("lines", []) for span in line.get("spans", [])]
        text = _clean(" ".join(str(span.get("text") or "") for span in spans))
        sizes = [float(span.get("size") or 0.0) for span in spans if span.get("text")]
        if not text or not sizes or abs(max(sizes) - size) > max(1.0, size * 0.12):
            continue
        if len(text) <= 180 and not DOI_RE.search(text):
            continuations.append((block_y, text))
    for _, continuation in sorted(continuations):
        if continuation not in title:
            title = f"{title} {continuation}"
    return _clean(title)


def _visible_doi(page_dicts: list[dict]) -> str | None:
    """Read a DOI from individual visible spans before page text is merged."""

    for page in page_dicts:
        for block in page.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    candidate = normalize_doi(str(span.get("text") or ""))
                    if candidate:
                        return candidate
    return None


def extract_pdf(path: str | Path, max_pages: int = 3) -> LocalEvidence:
    """本文先頭3ページをローカル抽出し、DOI・タイトル・著者候補を得る。"""

    pdf_path = Path(path)
    if pdf_path.suffix.casefold() != ".pdf":
        raise ValueError(f"PDFではありません: {pdf_path}")
    raw = pdf_path.read_bytes()
    raw_text = raw.decode("latin-1", errors="ignore")
    raw_doi = normalize_doi(raw_text)
    doi = None
    embedded_title = embedded_author = None
    embedded_year: int | None = None
    text = ""
    page_dicts: list[dict] = []
    notes: list[str] = []

    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
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
                    text += "\n" + page.get_text("text", sort=True)
                    page_dict = page.get_text("dict", sort=True)
                    page_dicts.append(page_dict)
        except Exception as exc:  # malformed/encrypted PDFs remain safely held
            notes.append(f"PDF本文抽出失敗: {type(exc).__name__}")

    # Visible first pages are stronger evidence than arbitrary serialized PDF
    # bytes, which can include XMP markup or DOI references from elsewhere.
    doi = _visible_doi(page_dicts) or normalize_doi(text) or raw_doi
    title = _usable_embedded_title(embedded_title)
    authors = _split_authors(_usable_embedded_author(embedded_author))
    embedded_metadata_used = bool(title or authors)
    guessed_title, guessed_authors = _guess_title_and_authors(text)
    if not title:
        title = _layout_title(page_dicts) or guessed_title
    if not authors:
        authors = guessed_authors
    if not title and not authors and not doi:
        notes.append("DOI・タイトル・著者候補を検出できませんでした")
    source = "pdf-embedded" if embedded_metadata_used else "pdf-layout" if page_dicts and title else "pdf-text" if text else "pdf-bytes"
    document_language = detect_document_language(text)
    language = document_language or normalize_language(detect_language(title, *authors))
    volume, issue, pages = _extract_bibliographic_hints(text, pdf_path)
    return LocalEvidence(
        path=pdf_path,
        doi=doi,
        title=title,
        authors=authors,
        year=embedded_year,
        language=language,
        metadata_source=source,
        notes=tuple(notes),
        translation_marker=has_translation_marker(pdf_path),
        volume=volume,
        issue=issue,
        pages=pages,
    )
