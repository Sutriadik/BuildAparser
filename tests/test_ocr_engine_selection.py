"""
Tests untuk pemilihan engine OCR (app/parsers/docling_parser.py).

Dulu pemilihan engine dibungkus try/except tanpa nilai default: di macOS terpilih Apple
Vision, di Linux exception-nya ditelan dan Docling diam-diam memakai engine lain. Tes ini
menjaga agar perilaku "gagal terang-terangan" tidak tanpa sengaja dikembalikan jadi
"mundur diam-diam" — kesalahan yang baru ketahuan saat dokumen produksi salah terbaca.
"""
import sys

import pytest

from app.parsers import docling_parser as dp


@pytest.fixture
def set_engine(monkeypatch):
    def _set(value):
        monkeypatch.setattr(dp.config, "OCR_ENGINE", value)
    return _set


def test_auto_memilih_apple_vision_di_macos(set_engine, monkeypatch):
    """Di Mac, default harus tetap Apple Vision — ini yang menghasilkan output saat ini."""
    if sys.platform != "darwin":
        pytest.skip("hanya relevan di macOS")
    set_engine("auto")
    assert dp._resolve_ocr_engine().name == "mac"


def test_auto_di_linux_memakai_default_docling_dengan_peringatan(set_engine, monkeypatch, caplog):
    """Di platform non-Mac, auto boleh mundur ke default TAPI wajib bersuara."""
    set_engine("auto")
    monkeypatch.setattr(sys, "platform", "linux")
    engine = dp._resolve_ocr_engine()
    assert engine.name == "docling-default"
    assert engine.options is None


def test_engine_tidak_dikenal_ditolak(set_engine):
    set_engine("tesserakt")  # salah ketik
    with pytest.raises(ValueError, match="tidak dikenal"):
        dp._resolve_ocr_engine()


def test_engine_eksplisit_tidak_pernah_mundur_diam_diam(set_engine, monkeypatch):
    """
    Kalau engine diminta eksplisit tapi tidak bisa dimuat, harus RuntimeError —
    bukan diam-diam memakai engine lain.
    """
    set_engine("easyocr")
    import docling.datamodel.pipeline_options as po
    monkeypatch.delattr(po, "EasyOcrOptions", raising=False)
    with pytest.raises(RuntimeError, match="tidak bisa"):
        dp._resolve_ocr_engine()


def test_semua_engine_terdaftar_punya_kelas_options_di_docling():
    """Nama kelas di _OCR_ENGINES harus benar-benar ada di Docling versi terpasang."""
    import docling.datamodel.pipeline_options as po
    for key, (class_name, _kwargs, _desc) in dp._OCR_ENGINES.items():
        assert hasattr(po, class_name), f"{key}: {class_name} tidak ada di Docling ini"


def test_rapidocr_tidak_memakai_bahasa_chinese_default():
    """
    Regresi ke bug nyata: RapidOcrOptions() tanpa argumen default ke lang=["chinese"],
    yang di Docling resolve ke model gabungan CJK+Latin yang sama dipakai untuk
    lang=["en"] (diverifikasi byte-identical). Model itu memecah teks Latin murni jadi
    potongan tak terbaca -- pada dokumen scan kontrak, satu pasal terbaca
    "e ean eaan ean an ea" alih-alih kalimat aslinya.

    lang=["latin"] memuat model PP-OCRv5 khusus skrip Latin dan terbukti (benchmark
    end-to-end) menurunkan rasio kata rusak dari 18,3% ke 4,7% pada dokumen yang sama.
    Kalau tes ini gagal, seseorang mengembalikan RapidOCR ke default bahasa yang salah.
    """
    _class_name, kwargs, _desc = dp._OCR_ENGINES["rapidocr"]
    assert kwargs.get("lang") == ["latin"], (
        "RapidOCR harus eksplisit lang=['latin'], bukan default library (['chinese'])"
    )
