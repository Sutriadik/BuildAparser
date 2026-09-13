"""
Unit Tests untuk Image Quality Enhancement Layer & Smooth Grounding Confidence Scoring
"""
import pytest
import numpy as np
import cv2

from app.parsers.image_enhancer import (
    assess_image_quality,
    deskew_image,
    enhance_contrast_and_lighting,
    sharpen_and_denoise,
    preprocess_image_for_ocr
)
from app.services.grounding_linker import (
    calculate_match_confidence,
    _validate_and_order_box,
    find_best_bounding_box,
    link_visual_groundings
)
from app.schemas.common import (
    BoundingBox,
    LandingAIParsedResponse,
    ParseMetadata,
    DocumentStructure,
    StructureItem,
    Grounding,
    TextRange
)


# ============================================================================
# 1. Image Quality Enhancement Tests
# ============================================================================

def test_assess_image_quality():
    # Create a synthetic white image with black text-like rectangles
    img = np.ones((300, 400, 3), dtype=np.uint8) * 255
    cv2.putText(img, "SURAT PERINTAH KERJA", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    cv2.putText(img, "Nomor: 001/SPK/2026", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

    quality = assess_image_quality(img)
    assert "sharpness" in quality
    assert "contrast" in quality
    assert "skew_angle" in quality
    assert isinstance(quality["is_blurry"], (bool, np.bool_))
    assert isinstance(quality["needs_deskew"], (bool, np.bool_))


def test_deskew_image():
    # Create an image and rotate by 5 degrees
    img = np.ones((400, 500, 3), dtype=np.uint8) * 255
    cv2.putText(img, "DOKUMEN PENGADAAN KONTRAK", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    
    # Explicit deskew rotation
    deskewed = deskew_image(img, angle=5.0)
    assert deskewed is not None
    assert deskewed.shape[0] >= 400
    assert deskewed.shape[1] >= 500


def test_enhance_contrast_and_lighting():
    # Create image with artificial shadow / gradient
    img = np.ones((200, 300, 3), dtype=np.uint8) * 200
    # Add dark gradient
    for y in range(200):
        img[y, :, :] = int(100 + (y / 200) * 100)
    cv2.putText(img, "PENGADAAN HARDWARE", (30, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)

    enhanced = enhance_contrast_and_lighting(img)
    assert enhanced is not None
    assert enhanced.shape == img.shape
    # Contrast should be maintained or boosted
    assert np.std(enhanced) > 20.0


def test_sharpen_and_denoise():
    img = np.ones((100, 200, 3), dtype=np.uint8) * 240
    cv2.putText(img, "Rp 140.400.000", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

    sharpened = sharpen_and_denoise(img)
    assert sharpened is not None
    assert sharpened.shape == img.shape


def test_preprocess_image_for_ocr():
    img = np.ones((200, 300, 3), dtype=np.uint8) * 255
    cv2.putText(img, "PT BHAKTI UNGGUL TEKNOVASI", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

    processed = preprocess_image_for_ocr(img)
    assert processed is not None
    assert processed.shape[2] == 3


# ============================================================================
# 2. Smooth Grounding Confidence Scoring Tests
# ============================================================================

def test_calculate_match_confidence_exact_match():
    score, match_type = calculate_match_confidence(
        query="PT Bhakti Unggul Teknovasi",
        item_text="PT Bhakti Unggul Teknovasi",
        ocr_confidence=0.98
    )
    assert match_type == "exact_match"
    assert 0.95 <= score <= 0.99


def test_calculate_match_confidence_phrase_containment():
    score, match_type = calculate_match_confidence(
        query="Sewa Router Koneksi Jaringan",
        item_text="Sewa Router Koneksi Jaringan Badan Pendapatan Daerah Prov. Jabar VPN IP Cisco 1941",
        ocr_confidence=0.95
    )
    assert match_type == "phrase_containment"
    assert 0.85 <= score <= 0.98


def test_calculate_match_confidence_number_matching():
    # Exact Indonesian currency formatted number match
    score, match_type = calculate_match_confidence(
        query="407796000",
        item_text="Unit/Bulan 999.500 | 407.796.000",
        ocr_confidence=0.95
    )
    assert match_type == "number_match"
    assert 0.85 <= score <= 0.98


def test_calculate_match_confidence_rejects_single_char_noise():
    # User's reported bug: snippet "E" matching a long category string
    score, match_type = calculate_match_confidence(
        query="B. Sharing knowledge terkait dengan pengelolaan jaringan Intra dan Internet",
        item_text="E",
        ocr_confidence=0.90
    )
    assert score == 0.0
    assert match_type is None


def test_calculate_match_confidence_semantic_tokens():
    score, match_type = calculate_match_confidence(
        query="MGR BIDDING MGT BUSINESS SUPPORT Graha Merah Putih",
        item_text="Kepada Yth. MGR BIDDING MGT & BUSINESS SUPPORT 2 REG II Graha Merah Putih Lantai 5 Bandung",
        ocr_confidence=0.95
    )
    assert match_type in ["phrase_containment", "semantic_token_match"]
    assert score >= 0.75


def test_validate_and_order_box():
    # Test inverted box (ymin > ymax)
    inverted_box = BoundingBox(xmin=0.85, ymin=0.96, xmax=0.92, ymax=0.90)
    valid_box = _validate_and_order_box(inverted_box)
    
    assert valid_box.xmin <= valid_box.xmax
    assert valid_box.ymin <= valid_box.ymax
    assert valid_box.ymin == 0.90
    assert valid_box.ymax == 0.96


def test_find_best_bounding_box_and_linking():
    parsed_res = LandingAIParsedResponse(
        markdown="# SPH\nPT Contoh\nNomor: 1241/00/BIS",
        metadata=ParseMetadata(
            job_id="test-job", page_count=1, output_markdown_chars=50, duration_ms=100
        ),
        structure=DocumentStructure(
            children=[
                StructureItem(
                    type="page",
                    grounding=Grounding(page=1, range=TextRange(start=0, end=50), box=BoundingBox(xmin=0, ymin=0, xmax=1, ymax=1)),
                    children=[
                        StructureItem(
                            type="header",
                            text="PT Contoh Solusi Teknologi",
                            grounding=Grounding(page=1, range=TextRange(start=0, end=25), box=BoundingBox(xmin=0.1, ymin=0.1, xmax=0.5, ymax=0.15), confidence=0.96)
                        ),
                        StructureItem(
                            type="paragraph",
                            text="Nomor: 1241/00/BIS-05/BUT/2025",
                            grounding=Grounding(page=1, range=TextRange(start=26, end=50), box=BoundingBox(xmin=0.1, ymin=0.2, xmax=0.6, ymax=0.25), confidence=0.95)
                        )
                    ]
                )
            ]
        )
    )

    data = {
        "Vendor": {"Nama Vendor": "PT Contoh Solusi Teknologi"},
        "Nomor SPH": "1241/00/BIS-05/BUT/2025",
        "NonExistentField": "String yang tidak ada di dokumen sama sekali"
    }

    linked = link_visual_groundings(data, parsed_res)
    assert "Vendor.Nama Vendor" in linked
    assert linked["Vendor.Nama Vendor"].confidence >= 0.90
    assert linked["Vendor.Nama Vendor"].box.ymin < linked["Vendor.Nama Vendor"].box.ymax
    
    assert "Nomor SPH" in linked
    assert linked["Nomor SPH"].confidence >= 0.90
    
    # Non existent field should be rejected and not produce noise
    assert "NonExistentField" not in linked
