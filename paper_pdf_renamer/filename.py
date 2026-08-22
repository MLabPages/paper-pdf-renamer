from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .models import ResolvedMetadata

INVALID_WINDOWS = re.compile(r"[\\/:*?\"<>|]")
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
) -> Path:
    if not metadata.title or not metadata.first_author or not metadata.year:
        raise ValueError("著者・出版年・タイトルが揃っていません")
    if max_title_length < 10:
        raise ValueError("タイトル最大長は10以上にしてください")
    language = (metadata.language or "en").casefold()  # 不確かな場合は安全側に英語形式
    suffix = "ほか" if language.startswith("ja") and len(metadata.authors) >= 2 else "et al." if len(metadata.authors) >= 2 else ""
    author = _author_label(metadata.first_author)
    if suffix:
        author = f"{author}{suffix}" if suffix == "ほか" else f"{author} {suffix}"
    year = str(metadata.year)
    directory_path = Path(directory) if directory else (source.parent if source else Path.cwd())
    fixed_length = len(author) + 1 + len(year) + 1 + len(".pdf")
    allowed_title_length = min(max_title_length, max(10, max_path_length - len(str(directory_path)) - fixed_length - 1))
    title = _truncate_title(metadata.title, allowed_title_length)
    filename = sanitize_component(f"{author}_{year}_{title}") + ".pdf"
    destination = directory_path / filename
    return unique_destination(destination, source=source)
