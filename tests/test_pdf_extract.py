from pathlib import Path

from paper_pdf_renamer.pdf_extract import extract_pdf, normalize_doi


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1000/ABC.123).") == "10.1000/abc.123"
    assert normalize_doi("not a doi") is None


def test_doi_is_detected_without_pymupdf(tmp_path: Path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4\n/Subject (doi: 10.1234/test.paper)\n")
    evidence = extract_pdf(path)
    assert evidence.doi == "10.1234/test.paper"
