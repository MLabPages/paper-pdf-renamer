from pathlib import Path

from paper_pdf_renamer.pdf_extract import (
    _extract_bibliographic_hints,
    _guess_title_and_authors,
    detect_document_language,
    extract_pdf,
    has_translation_marker,
    normalize_doi,
)


def test_normalize_doi():
    assert normalize_doi("https://doi.org/10.1000/ABC.123).") == "10.1000/abc.123"
    assert normalize_doi("not a doi") is None


def test_doi_is_detected_without_pymupdf(tmp_path: Path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4\n/Subject (doi: 10.1234/test.paper)\n")
    evidence = extract_pdf(path)
    assert evidence.doi == "10.1234/test.paper"


def test_wrapped_title_and_affiliated_author_line_are_recovered():
    text = """
    Robot Continuity across Embodiments: Portability, Identity and
    Migration of Robotic Systems
    Weston Laity1, Patrick Holthaus2, Kerstin Haring1
    Abstract—This paper explores robot identity.
    """
    title, authors = _guess_title_and_authors(text)
    assert title == "Robot Continuity across Embodiments: Portability, Identity and Migration of Robotic Systems"
    assert authors == ("Weston Laity1", "Patrick Holthaus2", "Kerstin Haring1")


def test_title_parser_stops_before_visible_author_profile_lines():
    text = """
    Brand experiences in service
    organizations: Exploring the
    individual effects of brand
    experience dimensions
    Received (in revised form): 19th March 2012
    Herbjørn Nysveen
    is a Professor in marketing at Norwegian School of Economics.
    ABSTRACT Brand experience has been conceptualized.
    """
    title, authors = _guess_title_and_authors(text)
    assert title == "Brand experiences in service organizations: Exploring the individual effects of brand experience dimensions"
    assert authors == ("Herbjørn Nysveen",)


def test_question_title_with_uppercase_authors_is_recovered():
    text = """
    Do Reverse-Worded Items Confound Measures
    in Cross-Cultural Consumer Research? The
    Case of the Material Values Scale
    NANCY WONG
    ARIC RINDFLEISCH
    JAMES E. BURROUGHS*
    Most measures of consumer behavior have been developed.
    """
    title, authors = _guess_title_and_authors(text)
    assert title == "Do Reverse-Worded Items Confound Measures in Cross-Cultural Consumer Research? The Case of the Material Values Scale"
    assert authors[0] == "NANCY WONG"


def test_bibliographic_hints_can_use_filename_fingerprint():
    assert _extract_bibliographic_hints("", Path("30-1-72.pdf")) == ("30", "1", "72")


def test_title_is_recovered_after_publisher_sidebar_and_citation():
    text = """
    royalsocietypublishing.org/journal/rstb
    Research
    Cite this article: Prescott TJ, Camilleri D,
    Martinez-Hernandez U, Damianou A, Lawrence ND. 2019 Memory and mental time travel in
    humans and social robots. Phil. Trans. R. Soc. B
    http://dx.doi.org/10.1098/rstb.2018.0025
    Memory and mental time travel in
    humans and social robots
    Tony J. Prescott1, Daniel Camilleri1, Uriel Martinez-Hernandez2,
    Andreas Damianou3 and Neil D. Lawrence3
    Abstract—This paper describes social robot memory.
    """
    title, authors = _guess_title_and_authors(text)
    assert title == "Memory and mental time travel in humans and social robots"
    assert authors[0].startswith("Tony J. Prescott")


def test_title_at_end_of_two_column_page_is_recovered():
    text = """
    Abstract—This paper examines participants’ experiences of interacting with a robotic companion.
    The study describes agent migration and identity retention.
    W. C. Ho was with the School of Computer Science, University of Hertfordshire.
    Prototyping Realistic Long-term Human-Robot Interaction for the
    Study of Agent Migration
    K. L. Koay, D. S. Syrdal, W. C. Ho, and K. Dautenhahn, Senior Member, IEEE
    """
    title, authors = _guess_title_and_authors(text)
    assert title == "Prototyping Realistic Long-term Human-Robot Interaction for the Study of Agent Migration"
    assert authors[0].startswith("K. L. Koay")


def test_title_after_chapter_cover_is_recovered():
    text = """
    PART IV
    EAST AND SOUTH EAST ASIA
    Downloaded from academic.oup.com/book/46567/chapter/408130483
    19
    Engineering Robots with Heart
    in Japan
    The Politics of Cultural Difference in Artificial
    Emotional Intelligence
    Hirofumi Katsuno and Daniel White
    19.1 Introduction
    While the concept of artificial intelligence has often emphasized cognition.
    """
    title, authors = _guess_title_and_authors(text)
    assert title == "Engineering Robots with Heart in Japan The Politics of Cultural Difference in Artificial Emotional Intelligence"
    assert authors == ("Hirofumi Katsuno", "Daniel White")


def test_translation_markers_and_document_language_are_detected():
    assert has_translation_marker("paper_ja.pdf")
    assert has_translation_marker("paper_日本語訳.pdf")
    assert not has_translation_marker("japan-paper.pdf")
    assert detect_document_language("日本語の文章。" * 100 + " English") == "ja"
