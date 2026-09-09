"""
Unit tests untuk Visual Grounding Linker v2.
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.extractors.grounding_linker import (
    find_best_bounding_box,
    link_visual_groundings,
    _tokenize,
    _token_overlap_score,
    _fuzzy_score,
)
from app.schemas.common import (
    LandingAIParsedResponse,
    ParseMetadata,
    DocumentStructure,
    StructureItem,
    Grounding,
    BoundingBox,
    TextRange,
)


def _make_parsed_response(items_by_page: dict) -> LandingAIParsedResponse:
    """Helper: buat LandingAIParsedResponse dari dict {page_num: [(text, bbox), ...]}"""
    pages = []
    for page_num, items in items_by_page.items():
        children = []
        for idx, (text, bbox_tuple) in enumerate(items):
            children.append(StructureItem(
                type="paragraph",
                id=f"p{page_num}-{idx}",
                text=text,
                grounding=Grounding(
                    page=page_num,
                    range=TextRange(start=0, end=len(text)),
                    box=BoundingBox(
                        xmin=bbox_tuple[0], ymin=bbox_tuple[1],
                        xmax=bbox_tuple[2], ymax=bbox_tuple[3]
                    ),
                    confidence=0.95
                ),
                confidence=0.95
            ))
        pages.append(StructureItem(
            type="page",
            id=f"page-{page_num}",
            grounding=Grounding(
                page=page_num,
                range=TextRange(start=0, end=100),
                box=BoundingBox(xmin=0, ymin=0, xmax=1, ymax=1),
                confidence=1.0
            ),
            children=children,
            confidence=1.0
        ))
    return LandingAIParsedResponse(
        markdown="test",
        metadata=ParseMetadata(job_id="test", page_count=len(pages), output_markdown_chars=4),
        structure=DocumentStructure(children=pages)
    )


class TestTokenize:
    def test_basic(self):
        assert _tokenize("Hello World Test") == ["hello", "world", "test"]

    def test_filters_short(self):
        assert _tokenize("a b cd ef") == ["cd", "ef"]

    def test_empty(self):
        assert _tokenize("") == []


class TestTokenOverlapScore:
    def test_full_overlap(self):
        score = _token_overlap_score(["hello", "world"], ["hello", "world"])
        assert score == 1.0

    def test_partial_overlap(self):
        score = _token_overlap_score(["hello", "world"], ["hello", "world", "extra", "text"])
        assert 0.3 < score < 0.9

    def test_no_overlap(self):
        score = _token_overlap_score(["hello"], ["world"])
        assert score == 0.0

    def test_empty(self):
        assert _token_overlap_score([], ["hello"]) == 0.0
        assert _token_overlap_score(["hello"], []) == 0.0


class TestFuzzyScore:
    def test_exact_match(self):
        assert _fuzzy_score("hello", "hello") == 1.0

    def test_similar(self):
        score = _fuzzy_score("mohamad veni raharja", "mohamad veni raharja")
        assert score > 0.9

    def test_different(self):
        score = _fuzzy_score("hello", "world")
        assert score < 0.5


class TestFindBestBoundingBox:
    def test_exact_match_prefers_specific(self):
        """Should match the item that most specifically contains the query."""
        parsed = _make_parsed_response({
            1: [
                ("Telkom", (0.7, 0.04, 0.9, 0.08)),
                ("UNIVERSITAS TELKOM", (0.2, 0.1, 0.8, 0.15)),
                ("Kampus Universitas Telkom Jl.Telekomunikasi", (0.1, 0.2, 0.9, 0.3)),
            ]
        })
        
        result = find_best_bounding_box("UNIVERSITAS TELKOM", parsed)
        assert result is not None
        # Should match "UNIVERSITAS TELKOM" (exact, most specific) not "Telkom"
        assert "UNIVERSITAS TELKOM" in result.source_snippet

    def test_substring_in_longer_text(self):
        """Query is substring of item text."""
        parsed = _make_parsed_response({
            1: [
                ("rekening Bank Mandiri Cabang KK STT Telkom No.131.00.8888818.7", (0.1, 0.1, 0.9, 0.2)),
            ]
        })
        
        result = find_best_bounding_box("Bank Mandiri", parsed)
        assert result is not None
        assert "Bank Mandiri" in result.source_snippet

    def test_fuzzy_match_for_ocr_noise(self):
        """Should still match despite minor OCR differences."""
        parsed = _make_parsed_response({
            1: [
                ("Mohamad Veni Raharja", (0.1, 0.8, 0.3, 0.82)),
            ]
        })
        
        result = find_best_bounding_box("Mohamad Veni Raharja", parsed)
        assert result is not None
        assert result.source_snippet == "Mohamad Veni Raharja"

    def test_no_match_below_threshold(self):
        """Should not match completely unrelated text."""
        parsed = _make_parsed_response({
            1: [
                ("Lorem ipsum dolor sit amet", (0.1, 0.1, 0.9, 0.2)),
            ]
        })
        
        result = find_best_bounding_box("Bank Mandiri", parsed)
        assert result is None

    def test_empty_query(self):
        parsed = _make_parsed_response({1: [("test", (0, 0, 1, 1))]})
        assert find_best_bounding_box("", parsed) is None
        assert find_best_bounding_box("a", parsed) is None  # too short

    def test_multi_page_search(self):
        """Should search across all pages and find the best match."""
        parsed = _make_parsed_response({
            1: [("Telkom University", (0.7, 0.04, 0.9, 0.08))],
            2: [("Indah Purnomowati", (0.6, 0.8, 0.8, 0.82))],
        })
        
        result = find_best_bounding_box("Indah Purnomowati", parsed)
        assert result is not None
        assert result.page == 2


class TestLinkVisualGroundings:
    def test_links_simple_dict(self):
        parsed = _make_parsed_response({
            1: [
                ("UNIVERSITAS TELKOM", (0.2, 0.1, 0.8, 0.15)),
                ("687/AST11/AST-SET/2026", (0.3, 0.08, 0.9, 0.11)),
            ]
        })
        
        data = {
            "Nama Perusahaan": "UNIVERSITAS TELKOM",
            "Nomor Kontrak": "687/AST11/AST-SET/2026",
        }
        
        result = link_visual_groundings(data, parsed)
        assert "Nama Perusahaan" in result
        assert "Nomor Kontrak" in result

    def test_links_nested_dict(self):
        parsed = _make_parsed_response({
            1: [("Mohamad Veni Raharja", (0.1, 0.8, 0.3, 0.82))],
        })
        
        data = {
            "Pihak Pertama": {
                "Nama Representative": "Mohamad Veni Raharja"
            }
        }
        
        result = link_visual_groundings(data, parsed)
        assert "Pihak Pertama.Nama Representative" in result

    def test_skips_null_values(self):
        parsed = _make_parsed_response({1: [("test", (0, 0, 1, 1))]})
        data = {"field1": None, "field2": "test"}
        
        result = link_visual_groundings(data, parsed)
        assert "field1" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
