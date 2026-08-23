from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from typing import Any

from .models import LocalEvidence, ResolvedMetadata
from .pdf_extract import detect_language, normalize_doi, normalize_language


def _norm_text(value: str | None) -> str:
    value = (value or "").casefold()
    value = re.sub(r"[^\w\u3040-\u30ff\u3400-\u9fff]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def title_similarity(left: str | None, right: str | None) -> float:
    a, b = _norm_text(left), _norm_text(right)
    if not a or not b:
        return 0.0
    sequence = SequenceMatcher(None, a, b).ratio()
    a_words, b_words = set(a.split()), set(b.split())
    overlap = len(a_words & b_words) / max(len(a_words | b_words), 1)
    return max(sequence, overlap)


def _family(value: str | None) -> str:
    raw = (value or "").strip()
    raw = re.sub(r"\s+(?:et\s+al\.?|and\s+others|ほか)\b.*$", "", raw, flags=re.IGNORECASE)
    if "," in raw:
        raw = raw.split(",", 1)[0]
    raw = re.sub(r"[0-9¹²³⁴⁵⁶⁷⁸⁹⁰*†‡]+$", "", raw).strip()
    value = _norm_text(raw)
    return value.split()[-1] if value else ""


def _author_match(local: str | None, authors: tuple[str, ...]) -> bool:
    if not local or not authors:
        return False
    local_family = _family(local)
    return bool(local_family) and any(local_family in _family(author) or _family(author) in local_family for author in authors)


def _date_year(item: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "issued", "created"):
        parts = item.get(key, {}).get("date-parts", [])
        if parts and parts[0] and isinstance(parts[0][0], int):
            return parts[0][0]
    return None


def _item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    authors: list[str] = []
    for author in item.get("author", []) or []:
        name = author.get("family") or author.get("name") or author.get("literal")
        if name:
            # ファイル名規則と第一著者照合には姓だけを使う。
            authors.append(str(name))
    title = (item.get("title") or [None])[0]
    subtitle = (item.get("subtitle") or [None])[0]
    if title and subtitle:
        title = f"{title}: {subtitle}"
    return {
        "doi": normalize_doi(item.get("DOI")),
        "title": title,
        "authors": tuple(authors),
        "year": _date_year(item),
        "language": item.get("language"),
        "type": item.get("type"),
    }


class CrossrefClient:
    """APIキー不要のCrossrefクライアント。PDF bytesは送信しない。"""

    def __init__(self, mailto: str | None = None, timeout: float = 10.0):
        self.mailto = mailto
        self.timeout = timeout

    def _get(self, url: str) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "paper-pdf-renamer/0.1"}
        if self.mailto:
            headers["User-Agent"] += f" (mailto:{self.mailto})"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def lookup_doi(self, doi: str) -> dict[str, Any] | None:
        encoded = urllib.parse.quote(doi, safe="")
        payload = self._get(f"https://api.crossref.org/works/{encoded}")
        item = payload.get("message")
        return item if isinstance(item, dict) else None

    def search_title(self, title: str, rows: int = 5) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"query.bibliographic": title, "rows": rows})
        payload = self._get(f"https://api.crossref.org/works?{query}")
        items = payload.get("message", {}).get("items", [])
        return [item for item in items if isinstance(item, dict)]


def resolve_metadata(evidence: LocalEvidence, client: CrossrefClient | None = None) -> ResolvedMetadata:
    client = client or CrossrefClient()
    item: dict[str, Any] | None = None
    source = evidence.metadata_source
    reasons: list[str] = []
    query_doi = evidence.doi

    if query_doi:
        try:
            item = client.lookup_doi(query_doi)
            source = "crossref:doi" if item else source
        except Exception as exc:
            reasons.append(f"crossref-lookup-failed:{type(exc).__name__}")
    elif evidence.title:
        try:
            results = client.search_title(evidence.title)
            ranked = sorted(results, key=lambda candidate: title_similarity(evidence.title, _item_metadata(candidate)["title"]), reverse=True)
            if ranked and title_similarity(evidence.title, _item_metadata(ranked[0])["title"]) >= 0.90:
                item = ranked[0]
                source = "crossref:title"
            else:
                reasons.append("title-search-match-too-low")
        except Exception as exc:
            reasons.append(f"crossref-search-failed:{type(exc).__name__}")

    if item:
        data = _item_metadata(item)
        doi = data["doi"] or evidence.doi
        title = data["title"] or evidence.title
        authors = data["authors"] or evidence.authors
        year = data["year"] or evidence.year
        metadata_language = normalize_language(data["language"])
        document_language = normalize_language(evidence.language)
        language = metadata_language or detect_language(title, *authors) or document_language
        paper_type = data["type"]
        similarity = title_similarity(evidence.title, title) if evidence.title else 1.0
        author_ok = _author_match(evidence.first_author, authors) if evidence.first_author else True
        translated = bool(
            source == "crossref:doi"
            and (evidence.translation_marker or (document_language == "ja" and metadata_language != "ja"))
        )
        if evidence.title and similarity < 0.85 and not translated:
            reasons.append("title-mismatch")
        if evidence.first_author and not author_ok and not translated:
            reasons.append("author-mismatch")
        if not doi:
            reasons.append("doi-missing")
        if paper_type and paper_type not in {"journal-article", "proceedings-article", "book-chapter", "book-part", "posted-content"}:
            reasons.append("non-paper-type")
        if not paper_type:
            reasons.append("paper-type-unconfirmed")
        confidence = 0.92
        if evidence.title and similarity >= 0.95:
            confidence += 0.04
        if evidence.first_author and author_ok:
            confidence += 0.03
        if evidence.first_author and not author_ok and not translated:
            confidence -= 0.45
        if evidence.title and similarity < 0.85 and not translated:
            confidence -= 0.35
        if source == "crossref:title":
            confidence -= 0.03
    else:
        doi, title, authors, year = evidence.doi, evidence.title, evidence.authors, evidence.year
        language, paper_type = evidence.language, None
        document_language = evidence.language
        translated = evidence.translation_marker
        confidence = 0.55 if doi else 0.20
        if not doi:
            reasons.append("doi-missing")
        if not title:
            reasons.append("title-missing")
        if not authors:
            reasons.append("author-missing")
        reasons.append("verified-metadata-unavailable")
        reasons.append("paper-type-unconfirmed")

    if not title:
        reasons.append("title-missing")
    if not authors:
        reasons.append("author-missing")
    if not year:
        reasons.append("year-missing")
    if confidence < 0.90:
        reasons.append("low-confidence")
    # Preserve order while avoiding duplicate UI messages.
    unique_reasons = tuple(dict.fromkeys(reasons))
    return ResolvedMetadata(
        doi=doi,
        title=title,
        authors=tuple(authors),
        year=year,
        language=language,
        source=source,
        confidence=max(0.0, min(1.0, confidence)),
        reasons=unique_reasons,
        paper_type=paper_type,
        local=evidence,
        document_language=document_language,
        translated=translated,
    )
