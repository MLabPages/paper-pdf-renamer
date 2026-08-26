from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from typing import Any

from .models import LocalEvidence, ResolvedMetadata
from .pdf_extract import (
    detect_language,
    normalize_doi,
    normalize_language,
    strip_translation_marker,
)


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
    container_titles = item.get("container-title") or []
    return {
        "doi": normalize_doi(item.get("DOI")),
        "title": title,
        "authors": tuple(authors),
        "year": _date_year(item),
        "language": item.get("language"),
        "type": item.get("type"),
        "container_title": container_titles[0] if container_titles else None,
        "volume": str(item.get("volume")) if item.get("volume") is not None else None,
        "issue": str(item.get("issue")) if item.get("issue") is not None else None,
        "pages": str(item.get("page")) if item.get("page") else None,
    }


def _first_page(value: str | None) -> str | None:
    match = re.search(r"\d+", value or "")
    return match.group(0) if match else None


def _bibliographic_matches(evidence: LocalEvidence, data: dict[str, Any]) -> int:
    """Count independent local-vs-Crossref hints used to rescue imperfect titles."""

    matches = 0
    if evidence.year and data.get("year") and evidence.year == data["year"]:
        matches += 1
    if evidence.volume and data.get("volume") and str(evidence.volume) == str(data["volume"]):
        matches += 1
    if evidence.issue and data.get("issue") and str(evidence.issue) == str(data["issue"]):
        matches += 1
    local_page = _first_page(evidence.pages)
    remote_page = _first_page(data.get("pages"))
    if local_page and remote_page and local_page == remote_page:
        matches += 1
    return matches


def _candidate_rank(evidence: LocalEvidence, item: dict[str, Any]) -> tuple[float, int]:
    data = _item_metadata(item)
    matches = _bibliographic_matches(evidence, data)
    return title_similarity(evidence.title, data["title"]) + matches * 0.02, matches


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


class OpenAlexClient:
    """DOIなし・Crossref未収録時だけ使う補助書誌クライアント。"""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    def search_title(self, title: str, rows: int = 5) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({
            "search": title,
            "per_page": rows,
            "select": "id,display_name,publication_year,authorships,doi,type,language",
        })
        request = urllib.request.Request(
            f"https://api.openalex.org/works?{query}",
            headers={"Accept": "application/json", "User-Agent": "paper-pdf-renamer/0.1"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.load(response)
        return [item for item in payload.get("results", []) if isinstance(item, dict)]


def _openalex_item(item: dict[str, Any]) -> dict[str, Any]:
    type_map = {
        "article": "journal-article",
        "book-chapter": "book-chapter",
        "book": "book",
        "preprint": "posted-content",
    }
    authors = []
    for authorship in item.get("authorships", []) or []:
        display_name = (authorship.get("author") or {}).get("display_name")
        if display_name:
            raw_name = str(display_name).strip()
            family = raw_name.split(",", 1)[0] if "," in raw_name else raw_name.split()[-1]
            authors.append({"family": family})
    year = item.get("publication_year")
    return {
        "DOI": item.get("doi"),
        "title": [item.get("display_name")],
        "author": authors,
        "issued": {"date-parts": [[year]]} if isinstance(year, int) else {},
        "type": type_map.get(item.get("type"), item.get("type")),
        "language": item.get("language"),
    }


def _filename_evidence(evidence: LocalEvidence) -> tuple[str | None, int | None]:
    # 先頭の ``[ja]`` などの翻訳印は著者名ではないので、照合前に取り除く。
    stem = strip_translation_marker(evidence.path.stem)
    author = re.split(r"\s+(?:&|et\s+al\.?|ほか)|\s*\(", stem, maxsplit=1, flags=re.IGNORECASE)[0]
    author = re.sub(r"^al-", "", author, flags=re.IGNORECASE).strip(" ._-") or None
    if author and len(re.sub(r"\W", "", author, flags=re.UNICODE)) < 2:
        author = None
    year_match = re.search(r"\((19|20)\d{2}\)", stem)
    return author, int(year_match.group(0)[1:-1]) if year_match else None


def resolve_metadata(
    evidence: LocalEvidence,
    client: CrossrefClient | None = None,
    openalex_client: OpenAlexClient | None = None,
) -> ResolvedMetadata:
    client = client or CrossrefClient()
    openalex_client = openalex_client or OpenAlexClient()
    item: dict[str, Any] | None = None
    source = evidence.metadata_source
    reasons: list[str] = []
    warnings: list[str] = []
    query_dois = evidence.doi_candidates or ((evidence.doi,) if evidence.doi else ())
    lookup_errors: list[str] = []

    if query_dois:
        doi_items: list[dict[str, Any]] = []
        for query_doi in query_dois:
            try:
                candidate = client.lookup_doi(query_doi)
                if candidate:
                    doi_items.append(candidate)
            except Exception as exc:
                lookup_errors.append(f"crossref-lookup-failed:{type(exc).__name__}")
        if doi_items:
            item = max(doi_items, key=lambda candidate: _candidate_rank(evidence, candidate))
            source = "crossref:doi"

    title_search_low = False
    if item is None and evidence.title:
        try:
            results = client.search_title(evidence.title)
            ranked = sorted(results, key=lambda candidate: _candidate_rank(evidence, candidate), reverse=True)
            best = _item_metadata(ranked[0]) if ranked else None
            similarity = title_similarity(evidence.title, best["title"]) if best else 0.0
            bibliographic_matches = _bibliographic_matches(evidence, best) if best else 0
            if ranked and (similarity >= 0.90 or (similarity >= 0.80 and bibliographic_matches >= 2)):
                item = ranked[0]
                source = "crossref:title"
            else:
                title_search_low = True
        except Exception as exc:
            lookup_errors.append(f"crossref-search-failed:{type(exc).__name__}")

    if item is None and evidence.title:
        try:
            openalex_results = openalex_client.search_title(evidence.title)
            ranked_openalex = sorted(
                openalex_results,
                key=lambda candidate: title_similarity(evidence.title, candidate.get("display_name")),
                reverse=True,
            )
            if ranked_openalex:
                candidate = _openalex_item(ranked_openalex[0])
                candidate_data = _item_metadata(candidate)
                similarity = title_similarity(evidence.title, candidate_data["title"])
                filename_author, filename_year = _filename_evidence(evidence)
                author_ok = _author_match(evidence.first_author, candidate_data["authors"]) or _author_match(
                    filename_author, candidate_data["authors"]
                )
                year_ok = bool(
                    candidate_data["year"]
                    and candidate_data["year"] in {evidence.year, filename_year}
                )
                if similarity >= 0.95 and author_ok and year_ok:
                    item = candidate
                    source = "openalex:title"
                    warnings.append("verified-by-openalex")
        except Exception as exc:
            lookup_errors.append(f"openalex-search-failed:{type(exc).__name__}")

    if item is None:
        reasons.extend(lookup_errors)
        if title_search_low:
            reasons.append("title-search-match-too-low")

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
        bibliographic_matches = _bibliographic_matches(evidence, data)
        filename_author, filename_year = _filename_evidence(evidence)
        author_ok = (
            _author_match(evidence.first_author, authors)
            or _author_match(filename_author, authors)
            if authors
            else False
        )
        year_ok = bool(year and year in {evidence.year, filename_year})
        # 翻訳版は本文がタイトル・著者と食い違うため、DOI一致時だけ照合を緩める。
        translation_relaxes_checks = bool(
            source == "crossref:doi"
            and (evidence.translation_marker or (document_language == "ja" and metadata_language != "ja"))
        )
        # ファイル名の ``[ja]`` などの印は、照合経路にかかわらずリネーム後も残す。
        translated = translation_relaxes_checks or evidence.translation_marker
        if evidence.title and similarity < 0.85 and not translation_relaxes_checks:
            reasons.append("title-mismatch")
        if evidence.first_author and not author_ok and not translation_relaxes_checks:
            # A PDF's first-page extraction is fragile. If DOI/title metadata
            # agrees, keep this discrepancy visible without blocking the rename.
            warnings.append("author-mismatch")
        verified_without_doi = bool(
            source == "openalex:title" and similarity >= 0.95 and author_ok and year_ok
        )
        if not doi and not verified_without_doi:
            reasons.append("doi-missing")
        elif not doi:
            warnings.append("doi-missing-verified-by-openalex")
        if paper_type and paper_type not in {"journal-article", "proceedings-article", "book-chapter", "book-part", "posted-content"}:
            reasons.append("non-paper-type")
        if not paper_type:
            reasons.append("paper-type-unconfirmed")
        confidence = 0.92
        if evidence.title and similarity >= 0.95:
            confidence += 0.04
        if evidence.first_author and author_ok:
            confidence += 0.03
        if bibliographic_matches >= 2:
            confidence += 0.04
        if evidence.title and similarity < 0.85 and not translation_relaxes_checks:
            confidence -= 0.35
        if source == "crossref:title":
            confidence -= 0.03
        if source == "openalex:title":
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
        warnings=tuple(dict.fromkeys(warnings)),
    )
