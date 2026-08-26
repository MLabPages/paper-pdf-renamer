from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .config import FORMAT_TEMPLATE, validate_format_template
from .models import ResolvedMetadata

INVALID_WINDOWS = re.compile(r"[\\/:*?\"<>|]")
JA_MARKER = "[ja]"
# 翻訳版の印は、Readableなどで手動で付けた先頭表記に合わせて先頭に置く。
_JA_MARKER_PREFIX_RE = re.compile(
    r"^[\s._()\[\]-]*(?:ja|jpn|日本語|翻訳)(?:$|[\s._()\[\]-])", re.IGNORECASE
)
# 旧版が末尾に付けた ``... [ja].pdf`` を二重に付けないため残す。
_JA_MARKER_SUFFIX_RE = re.compile(
    r"(?:^|[\s._()\[\]-])(?:ja|jpn|日本語|翻訳)[\s._()\[\]-]*$", re.IGNORECASE
)
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _author_label(value: str) -> str:
    """第一著者の姓を、ファイル名に使える短い表記へ揃える。"""

    value = sanitize_component(value)
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", value):
        return re.split(r"[\s　,、]+", value, maxsplit=1)[0] or value
    if "," in value:
        return value.split(",", 1)[0].strip() or value
    parts = value.split()
    return parts[-1] if parts else value


def _authors_label(metadata: ResolvedMetadata) -> str:
    """著者数に応じたファイル名用の著者表記を作る。"""

    authors = tuple(_author_label(author) for author in metadata.authors if author)
    if not authors:
        return _author_label(metadata.first_author or "")
    if (metadata.language or "en").casefold().startswith("ja"):
        if len(authors) >= 3:
            return f"{authors[0]}ほか"
        if len(authors) == 2:
            return f"{authors[0]}・{authors[1]}"
        return authors[0]
    if len(authors) >= 3:
        return f"{authors[0]} et al."
    if len(authors) == 2:
        return f"{authors[0]} & {authors[1]}"
    return authors[0]


def sanitize_component(value: str, replacements: dict[str, str] | None = None) -> str:
    replacements = replacements or {":": "-", "/": "-", "\\": "-"}
    value = unicodedata.normalize("NFKC", value)
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = INVALID_WINDOWS.sub("-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = re.sub(r"-{2,}", "-", value)
    if value.upper() in RESERVED_NAMES:
        value = f"_{value}"
    return value or "untitled"


def _truncate_title(title: str, max_length: int) -> str:
    title = sanitize_component(title)
    if len(title) <= max_length:
        return title
    cut = max(1, max_length - 1)
    if re.search(r"[A-Za-z]", title):
        prefix = title[:cut].rsplit(" ", 1)[0] if " " in title[:cut] else title[:cut]
    else:
        prefix = title[:cut]
    return prefix.rstrip(" .-") + "…"


def unique_destination(path: Path, source: Path | None = None) -> Path:
    """既存ファイルを上書きせず、Windows同様に大文字小文字を区別しない。"""

    source_key = str(source.resolve()).casefold() if source else None
    if not path.exists() or (source_key and str(path.resolve()).casefold() == source_key):
        return path
    stem, suffix = path.stem, path.suffix
    number = 2
    while True:
        candidate = path.with_name(f"{stem} ({number}){suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def build_filename(
    metadata: ResolvedMetadata,
    directory: str | Path | None = None,
    max_title_length: int = 100,
    source: Path | None = None,
    max_path_length: int = 260,
    format_template: str = FORMAT_TEMPLATE,
) -> Path:
    if not metadata.title or not metadata.first_author or not metadata.year:
        raise ValueError("著者・出版年・タイトルが揃っていません")
    if max_title_length < 10:
        raise ValueError("タイトル最大長は10以上にしてください")
    format_template = validate_format_template(format_template)
    author = _authors_label(metadata)
    year = str(metadata.year)
    directory_path = Path(directory) if directory else (source.parent if source else Path.cwd())
    values = {
        "author": author,
        "first_author": _author_label(metadata.first_author),
        "year": year,
        "doi": metadata.doi or "",
    }
    title_limit = min(max_title_length, max(10, len(sanitize_component(metadata.title))))
    filename = ""
    for current_limit in range(title_limit, 9, -1):
        title = _truncate_title(metadata.title, current_limit)
        try:
            rendered = format_template.format(title=title, **values).strip()
        except (IndexError, KeyError, ValueError) as exc:
            raise ValueError(f"ファイル名形式を展開できません: {exc}") from exc
        if not rendered.casefold().endswith(".pdf"):
            rendered += ".pdf"
        if metadata.translated:
            stem, extension = rendered[:-4], rendered[-4:]
            if not _JA_MARKER_PREFIX_RE.match(stem) and not _JA_MARKER_SUFFIX_RE.search(stem):
                rendered = f"{JA_MARKER} {stem.lstrip()}{extension}"
        filename = sanitize_component(rendered)
        if len(str(directory_path / filename)) <= max_path_length:
            break
    destination = directory_path / filename
    return unique_destination(destination, source=source)
