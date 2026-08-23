from pathlib import Path

from paper_pdf_renamer.crossref import _author_match, _item_metadata, resolve_metadata
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

LAITY_ITEM = {
    "DOI": "10.1109/RO-MAN63969.2025.11217771",
    "title": ["Robot Continuity across Embodiments: Portability, Identity and Migration of Robotic Systems"],
    "author": [
        {"family": "Laity", "given": "Weston"},
        {"family": "Holthaus", "given": "Patrick"},
        {"family": "Haring", "given": "Kerstin"},
    ],
    "issued": {"date-parts": [[2025]]},
    "type": "proceedings-article",
}

VOGES_ITEM = {
    "DOI": "10.1145/3757279.3785627",
    "title": ["Crafting Companions: A Mixed Methods Exploration of Customization amongst Robot Owners"],
    "author": [
        {"family": "Voges", "given": "Amelie"},
        {"family": "Foster", "given": "Mary Ellen"},
        {"family": "Cross", "given": "Emily S."},
    ],
    "issued": {"date-parts": [[2026]]},
    "type": "proceedings-article",
}


def test_crossref_subtitle_is_kept_for_full_filename_title():
    item = {
        "title": ["Engineering Robots with Heart in Japan"],
        "subtitle": ["The Politics of Cultural Difference in Artificial Emotional Intelligence"],
    }
    assert _item_metadata(item)["title"] == (
        "Engineering Robots with Heart in Japan: The Politics of Cultural Difference in Artificial Emotional Intelligence"
    )


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


def test_author_match_ignores_et_al_and_affiliation_markers():
    assert _author_match("Voges et al.", ("Voges", "Foster", "Cross"))
    assert _author_match("Weston Laity1, Patrick Holthaus2", ("Laity", "Holthaus"))


def test_wrapped_title_without_doi_can_recover_crossref_metadata():
    fake = FakeCrossref(LAITY_ITEM)
    result = resolve_metadata(
        LocalEvidence(
            Path("Laity2025.pdf"),
            None,
            "Robot Continuity across Embodiments: Portability, Identity and Migration of Robotic Systems",
            ("Weston Laity1", "Patrick Holthaus2", "Kerstin Haring1"),
        ),
        fake,
    )
    assert result.safe_to_rename
    assert result.doi == "10.1109/ro-man63969.2025.11217771"
    assert result.source == "crossref:title"


def test_doi_with_et_al_local_author_can_be_renamed():
    fake = FakeCrossref(VOGES_ITEM)
    result = resolve_metadata(
        LocalEvidence(
            Path("Voges et al. (2026).pdf"),
            "10.1145/3757279.3785627",
            "Crafting Companions: A Mixed Methods Exploration of Customization amongst Robot Owners",
            ("Voges et al.",),
        ),
        fake,
    )
    assert result.safe_to_rename
    assert result.first_author == "Voges"


def test_translated_pdf_uses_doi_metadata_and_is_marked_for_filename():
    fake = FakeCrossref(ITEM)
    result = resolve_metadata(
        LocalEvidence(
            Path("paper_ja.pdf"),
            "10.5555/abc",
            "日本語に翻訳されたタイトル",
            ("翻訳著者",),
            language="ja",
            translation_marker=True,
        ),
        fake,
    )
    assert result.safe_to_rename
    assert result.translated
    assert result.document_language == "ja"
    assert result.title == "Understanding Customer Experience"
