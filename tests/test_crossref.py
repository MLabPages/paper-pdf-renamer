from pathlib import Path

from paper_pdf_renamer.crossref import resolve_metadata
from paper_pdf_renamer.models import LocalEvidence


class FakeCrossref:
    def __init__(self, item):
        self.item = item
        self.lookups = []

    def lookup_doi(self, doi):
        self.lookups.append(("doi", doi))
        return self.item

    def search_title(self, title):
        self.lookups.append(("title", title))
        return [self.item]


ITEM = {
    "DOI": "10.5555/ABC",
    "title": ["Understanding Customer Experience"],
    "author": [{"family": "Lemon", "given": "Katherine"}, {"family": "Kumar", "given": "V."}],
    "issued": {"date-parts": [[2016]]},
    "language": "en",
    "type": "journal-article",
}


def test_doi_has_priority_and_high_confidence():
    fake = FakeCrossref(ITEM)
    result = resolve_metadata(LocalEvidence(Path("a.pdf"), "10.5555/abc", "Understanding Customer Experience", ("Lemon",), language="en"), fake)
    assert fake.lookups == [("doi", "10.5555/abc")]
    assert result.safe_to_rename
    assert result.doi == "10.5555/abc"
    assert result.year == 2016


def test_mismatched_author_is_held():
    fake = FakeCrossref(ITEM)
    result = resolve_metadata(LocalEvidence(Path("a.pdf"), "10.5555/abc", "Understanding Customer Experience", ("Smith",)), fake)
    assert not result.safe_to_rename
    assert "author-mismatch" in result.reasons


def test_title_search_can_recover_doi_but_low_evidence_remains_visible():
    fake = FakeCrossref(ITEM)
    result = resolve_metadata(LocalEvidence(Path("a.pdf"), None, "Understanding Customer Experience", ("Lemon",)), fake)
    assert fake.lookups[0][0] == "title"
    assert result.doi == "10.5555/abc"
    assert result.source == "crossref:title"
