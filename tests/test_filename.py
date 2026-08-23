from pathlib import Path

from paper_pdf_renamer.filename import build_filename, sanitize_component
from paper_pdf_renamer.models import ResolvedMetadata


def metadata(language="en", authors=("Lemon", "Kumar"), title="Understanding Customer Experience", translated=False):
    return ResolvedMetadata("10.1234/example", title, authors, 2016, language, "crossref:doi", 0.98, translated=translated)


def test_english_multiple_authors_and_windows_cleanup(tmp_path: Path):
    value = metadata(title='Understanding / Customer: Experience?')
    result = build_filename(value, directory=tmp_path)
    assert result.name == "Lemon et al._2016_Understanding - Customer- Experience-.pdf"
    assert all(char not in result.name for char in '\\/:*?"<>|')


def test_japanese_multiple_authors_and_single_author(tmp_path: Path):
    assert build_filename(metadata("ja", ("田中", "鈴木"), "ブランド経験に関する研究"), tmp_path).name == "田中ほか_2016_ブランド経験に関する研究.pdf"
    assert build_filename(metadata("ja", ("田中",), "ブランド経験に関する研究"), tmp_path).name == "田中_2016_ブランド経験に関する研究.pdf"


def test_author_label_uses_family_name(tmp_path: Path):
    assert build_filename(metadata(authors=("Katherine Lemon", "V. Kumar")), tmp_path).name.startswith("Lemon et al._2016_")
    assert build_filename(metadata(authors=("Lemon, Katherine", "Kumar, V.")), tmp_path).name.startswith("Lemon et al._2016_")


def test_custom_format_template(tmp_path: Path):
    result = build_filename(
        metadata(),
        tmp_path,
        format_template="{author} ({year}). - {title}.pdf",
    )
    assert result.name == "Lemon et al. (2016). - Understanding Customer Experience.pdf"


def test_custom_format_template_adds_pdf_extension(tmp_path: Path):
    result = build_filename(metadata(), tmp_path, format_template="{author} ({year}) - {title}")
    assert result.name.endswith(".pdf")


def test_translated_pdf_gets_ja_suffix_without_changing_original_format(tmp_path: Path):
    result = build_filename(
        metadata(translated=True),
        tmp_path,
        format_template="{author} ({year}). - {title}.pdf",
    )
    assert result.name == "Lemon et al. (2016). - Understanding Customer Experience [ja].pdf"


def test_duplicate_does_not_overwrite(tmp_path: Path):
    first = build_filename(metadata(), tmp_path)
    first.touch()
    second = build_filename(metadata(), tmp_path)
    assert second.name.endswith(" (2).pdf")


def test_title_truncation_and_trailing_cleanup(tmp_path: Path):
    value = metadata(authors=("A",), title="one two three four five six seven")
    result = build_filename(value, tmp_path, max_title_length=16)
    assert len(Path(result).stem.split("_", 2)[-1]) <= 16
    assert not result.name.endswith((" ", "."))
    assert sanitize_component("  a   b. ") == "a b"
