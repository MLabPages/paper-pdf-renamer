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


class FakeOpenAlex:
    def __init__(self, items=()):
        self.items = list(items)

    def search_title(self, title):
        return self.items


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


def test_mismatched_author_is_warning_when_doi_and_title_match():
    fake = FakeCrossref(ITEM)
    result = resolve_metadata(LocalEvidence(Path("a.pdf"), "10.5555/abc", "Understanding Customer Experience", ("Smith",)), fake)
    assert result.safe_to_rename
    assert "author-mismatch" not in result.reasons
    assert "author-mismatch" in result.warnings


def test_title_search_can_use_bibliographic_hints_with_imperfect_title():
    item = {
        **ITEM,
        "title": ["Understanding Customer Experience in Context"],
        "volume": "12",
        "issue": "3",
        "page": "44-60",
    }
    fake = FakeCrossref(item)
    result = resolve_metadata(
        LocalEvidence(
            Path("12-3-44.pdf"),
            None,
            "Understanding Customer Experience in Context for Teams",
            (),
            volume="12",
            issue="3",
            pages="44",
        ),
        fake,
    )
    assert result.source == "crossref:title"
    assert result.doi == "10.5555/abc"
    assert result.safe_to_rename


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


def test_multiple_visible_dois_select_the_title_matching_article():
    proceedings = {
        **ITEM,
        "DOI": "10.1145/3772318",
        "title": ["Proceedings of the 2026 CHI Conference"],
        "type": "proceedings",
    }
    article = {
        **ITEM,
        "DOI": "10.1145/3772318.3790714",
        "title": ["RECALLbot: Designing Agentic Memory and Reciprocal Disclosure for Human-Chatbot Relationships"],
        "type": "proceedings-article",
    }

    class MultiCrossref(FakeCrossref):
        def lookup_doi(self, doi):
            return proceedings if doi == "10.1145/3772318" else article

    result = resolve_metadata(
        LocalEvidence(
            Path("Jiang et al. (2026).pdf"),
            "10.1145/3772318",
            article["title"][0],
            ("Zhaojun Jiang",),
            year=2026,
            doi_candidates=("10.1145/3772318", "10.1145/3772318.3790714"),
        ),
        MultiCrossref(article),
        FakeOpenAlex(),
    )
    assert result.safe_to_rename
    assert result.doi == "10.1145/3772318.3790714"


def test_openalex_can_verify_doi_less_work_with_title_author_and_year():
    openalex_item = {
        "display_name": "Consumer-product attachment: Measurement and design implications",
        "publication_year": 2008,
        "doi": None,
        "type": "article",
        "authorships": [
            {"author": {"display_name": "Hendrik N.J. Schifferstein"}},
            {"author": {"display_name": "Elly P. H. Zwartkruis-Pelgrim"}},
        ],
    }
    result = resolve_metadata(
        LocalEvidence(
            Path("Schifferstein & Zwartkruis-Pelgrim (2008).pdf"),
            None,
            "Consumer-Product Attachment: Measurement and Design Implications",
            (),
            year=2009,
        ),
        FakeCrossref(None),
        FakeOpenAlex([openalex_item]),
    )
    assert result.safe_to_rename
    assert result.year == 2008
    assert result.source == "openalex:title"
    assert result.authors == ("Schifferstein", "Zwartkruis-Pelgrim")
    assert "doi-missing-verified-by-openalex" in result.warnings


def test_filename_ja_marker_is_kept_when_matched_by_title_search():
    fake = FakeCrossref(LAITY_ITEM)
    result = resolve_metadata(
        LocalEvidence(
            Path("[ja] Laity (2025).pdf"),
            None,
            "Robot Continuity across Embodiments: Portability, Identity and Migration of Robotic Systems",
            ("Weston Laity1", "Patrick Holthaus2", "Kerstin Haring1"),
            translation_marker=True,
        ),
        fake,
    )
    assert result.source == "crossref:title"
    assert result.translated
    assert result.safe_to_rename
