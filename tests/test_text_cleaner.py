"""
Unit tests untuk OCR Text Cleaner module.
"""
import pytest
import sys
import os

# Ensure app can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.text_cleaner import (
    fix_common_ocr_typos,
    fix_missing_spaces,
    normalize_entity_casing,
    normalize_whitespace,
    fix_number_formatting,
    clean_footer_noise,
    clean_ocr_text,
    clean_ocr_line,
)


class TestFixCommonOCRTypos:
    def test_number_letter_confusion(self):
        assert "Nomor" in fix_common_ocr_typos("Nom0r")
        assert "Oracle" in fix_common_ocr_typos("0racle")
        assert "Oracle" in fix_common_ocr_typos("Oracie")

    def test_mixed_casing_entities(self):
        assert "UNIVERSITAS" in fix_common_ocr_typos("UNIvERsITAs")
        assert "TELKOM" in fix_common_ocr_typos("TELkOM")
        assert "BHAKTI" in fix_common_ocr_typos("BHAKTi")
        assert "TEKNOVASI" in fix_common_ocr_typos("TEKNOvASI")

    def test_pihak_typos(self):
        assert "PIHAK" in fix_common_ocr_typos("PlHAK")
        assert "PIHAK" in fix_common_ocr_typos("PiHAK")

    def test_no_change_on_correct_text(self):
        text = "Nomor Kontrak Kerja Oracle Database"
        assert fix_common_ocr_typos(text) == text


class TestFixMissingSpaces:
    def test_number_before_capitalized_word(self):
        assert "29 Mei" in fix_missing_spaces("29Mei")
        assert "11 Kedaung" in fix_missing_spaces("11Kedaung")

    def test_lowercase_before_uppercase(self):
        assert "Informasi Universitas" in fix_missing_spaces("InformasiUniversitas")
        assert "Teknologi Informasi" in fix_missing_spaces("TeknologiInformasi")

    def test_rp_formatting(self):
        assert "sebesar Rp." in fix_missing_spaces("sebesarRp.")
        assert "Rp. 174" in fix_missing_spaces("Rp.174")

    def test_period_before_capital(self):
        assert "terlampir. Berdasarkan" in fix_missing_spaces("terlampir.Berdasarkan")

    def test_comma_spacing(self):
        result = fix_missing_spaces("Bandung,40257")
        assert ", " in result

    def test_colon_spacing(self):
        result = fix_missing_spaces("Alamat:Jl.")
        assert ": " in result

    def test_parenthesis_spacing(self):
        assert ") PIHAK" in fix_missing_spaces(")PIHAK")


class TestNormalizeEntityCasing:
    def test_universitas_telkom(self):
        result = normalize_entity_casing("universitas telkom")
        assert "UNIVERSITAS TELKOM" in result

    def test_pihak_pertama_kedua(self):
        result = normalize_entity_casing("pihak pertama memberikan")
        assert "PIHAK PERTAMA" in result

    def test_mixed_input(self):
        result = normalize_entity_casing("Universitas Telkom")
        assert "UNIVERSITAS TELKOM" in result


class TestNormalizeWhitespace:
    def test_multiple_spaces(self):
        assert "hello world" == normalize_whitespace("hello   world")

    def test_trailing_spaces(self):
        assert "hello" == normalize_whitespace("  hello  ")

    def test_multiple_newlines(self):
        result = normalize_whitespace("hello\n\n\n\n\nworld")
        assert result.count("\n") <= 2

    def test_tabs_replaced(self):
        assert "hello world" == normalize_whitespace("hello\tworld")


class TestFixNumberFormatting:
    def test_trailing_dash_removal(self):
        assert "174.825.000" in fix_number_formatting("174.825.000-")
        assert "174.825.000" in fix_number_formatting("174.825.000–")

    def test_preserve_normal_numbers(self):
        assert "174.825.000" == fix_number_formatting("174.825.000")

    def test_preserve_dash_between_numbers(self):
        # Should NOT remove dash between two numbers (date range)
        result = fix_number_formatting("2026-2027")
        assert "2026-2027" == result


class TestCleanFooterNoise:
    def test_campus_footer_removal(self):
        text = "Main Campus Bangkit Building TelkomUniversity Jl.Telekomunikasi Terusan Buah BatuBandung 40257 West Java Indonesia www.telkomuniversity.ac.id"
        result = clean_footer_noise(text)
        assert "Main Campus" not in result

    def test_url_standalone_removal(self):
        result = clean_footer_noise("www.telkomuniversity.ac.id")
        assert result.strip() == ""

    def test_preserve_normal_text(self):
        text = "Pihak Pertama memberikan pekerjaan kepada Pihak Kedua"
        assert clean_footer_noise(text) == text


class TestCleanOCRText:
    """Integration tests for the full cleaning pipeline."""

    def test_full_pipeline(self):
        dirty = "Nom0r : 687/AST11/AST-SET/2026 sebesarRp.174.825.000- UNIvERsITAs TELkOM"
        result = clean_ocr_text(dirty)
        
        assert "Nomor" in result
        assert "sebesar Rp." in result
        assert "174.825.000" in result
        assert "UNIVERSITAS TELKOM" in result

    def test_missing_spaces_in_pipeline(self):
        dirty = "29Mei 2026 InformasiUniversitas TeknologiInformasi"
        result = clean_ocr_text(dirty)
        
        assert "29 Mei" in result
        assert "Informasi Universitas" in result

    def test_empty_string(self):
        assert clean_ocr_text("") == ""
        assert clean_ocr_text(None) is None

    def test_clean_text_unchanged(self):
        clean = "Nomor Kontrak : 687/AST11/AST-SET/2026"
        result = clean_ocr_text(clean)
        # Should not significantly alter already clean text
        assert "687/AST11/AST-SET/2026" in result


class TestCleanOCRLine:
    def test_single_line(self):
        dirty = "Nom0r : 687/AST11/AST-SET/2026"
        result = clean_ocr_line(dirty)
        assert "Nomor" in result

    def test_whitespace_collapse(self):
        result = clean_ocr_line("hello   world   test")
        assert result == "hello world test"

    def test_empty_string(self):
        assert clean_ocr_line("") == ""
        assert clean_ocr_line(None) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
