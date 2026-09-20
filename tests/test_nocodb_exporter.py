"""
Tests untuk app/exporters/nocodb.py.

Fokusnya pada hal-hal yang kalau rusak tidak menimbulkan error, hanya data yang salah
diam-diam: tipe kolom yang tidak cocok dengan nilainya, dan kolom persetujuan manusia
yang ikut terisi otomatis.
"""
import json
from pathlib import Path

import pytest

from app.exporters.nocodb import build_nocodb_payload, build_nocodb_schema

EXTRACTION_DIR = Path(__file__).resolve().parent.parent / "storage" / "outputs" / "extraction"

_OK_TYPES = {
    "Decimal": (int, float), "Number": (int, float), "Checkbox": (bool,),
    "SingleLineText": (str,), "LongText": (str,), "DateTime": (str,),
}


def _sample_contract():
    return {
        "document_name": "SPK contoh.pdf",
        "document_type": "contract",
        "data": {
            "Nomor Kontrak Kerja": "045/SPK/2026",
            "Nama Pekerjaan": "Pengadaan Switch",
            "Durasi Kerja": "30 hari kalender",          # teks, bukan angka
            "Persentase Sanksi/Penalti": "1/1000",       # teks, bukan angka
            "sub total": 48000000.0,
            "Total Harga Pekerjaan": 53280000.0,
            "Pihak Pertama": {"Nama Perusahaan": "PT A", "Alamat": "Jl. Satu"},
            "Pihak Kedua": {"Nama Perusahaan": "CV B", "Alamat": "Jl. Dua"},
            "List Item/Barang": [
                {"Nomor Item": "1", "Deskripsi Item/Barang/Pekerjaan": "Switch 24 Port",
                 "volume": 4, "unit": "unit", "Harga Satuan": 12000000.0, "Jumlah Harga": 48000000.0},
            ],
            "Syarat Lampiran Wajib BAST": ["Foto unit terpasang", "Berita acara uji"],
            "Ketentuan Pembayaran": ["Termin 1 sebesar 50%"],
        },
        "validation": {"status": "pass", "issues": []},
        "evidence": [
            {"field": "Nomor Kontrak Kerja", "value": "045/SPK/2026", "page": 1,
             "evidence_text": "Nomor : 045/SPK/2026", "confidence": 0.95,
             "match_type": "exact", "status": "AUTO_VERIFIED", "issues": []},
            # nilai non-numerik pada kolom bernama "nilai_*" — dulu ini ditipe-kan Decimal
            {"field": "Pihak Pertama.Nama Perusahaan", "value": "PT A", "page": 1,
             "evidence_text": "PT A", "confidence": 0.9,
             "match_type": "exact", "status": "AUTO_ACCEPTED", "issues": []},
        ],
        "quality_report": {"fill_rate": 0.8},
        "run_info": {"run_id": "run-test", "document_id": "doc-test", "timings": {"total_s": 1.0}},
    }


def _assert_types_match(payload):
    types = {t["table_name"]: {c["column_name"]: c["uidt"] for c in t["columns"]}
             for t in build_nocodb_schema(payload)["tables"]}
    for table, rows in payload.items():
        for row in rows:
            for key, value in row.items():
                if value is None:
                    continue
                uidt = types[table][key]
                # bool adalah subclass int di Python; Checkbox dan Decimal tidak boleh tertukar
                assert isinstance(value, bool) == (uidt == "Checkbox"), f"{table}.{key} = {value!r}"
                assert isinstance(value, _OK_TYPES[uidt]), (
                    f"{table}.{key} bertipe {uidt} tapi nilainya {type(value).__name__} ({value!r})"
                )


def test_contract_payload_tables():
    payload = build_nocodb_payload(_sample_contract())
    assert payload["contracts"][0]["nomor_kontrak"] == "045/SPK/2026"
    assert len(payload["contract_items"]) == 1
    assert len(payload["bast_requirements"]) == 2
    assert len(payload["payment_terms"]) == 1
    assert len(payload["field_confirmations"]) == 2


def test_types_match_values_on_sample():
    _assert_types_match(build_nocodb_payload(_sample_contract()))


def test_text_columns_not_typed_as_number():
    """Kolom bernama numerik tapi berisi teks pernah ditipe-kan Decimal dan gagal diimpor."""
    schema = build_nocodb_schema(build_nocodb_payload(_sample_contract()))
    types = {t["table_name"]: {c["column_name"]: c["uidt"] for c in t["columns"]}
             for t in schema["tables"]}
    assert types["contracts"]["durasi_kerja"] in ("SingleLineText", "LongText")
    assert types["contracts"]["persentase_penalti"] in ("SingleLineText", "LongText")
    assert types["field_confirmations"]["nilai_ekstraksi"] in ("SingleLineText", "LongText")
    assert types["contracts"]["total_harga_pekerjaan"] == "Decimal"


def test_human_approval_columns_never_prefilled():
    """
    Briefing hlm. 20: klausul kontrak dan harga wajib dikonfirmasi manusia.
    Status AUTO_* dari sistem tidak boleh dianggap persetujuan.
    """
    payload = build_nocodb_payload(_sample_contract())
    for row in payload["field_confirmations"]:
        assert row["pm_confirmed"] is False
        assert row["nilai_final"] is None
        assert row["dikonfirmasi_oleh"] is None
    for row in payload["contract_items"]:
        assert row["harga_terverifikasi"] is False
    assert payload["contracts"][0]["semua_field_terkonfirmasi"] is False


def test_mybhakti_link_columns_left_empty():
    """Briefing hlm. 7: Klien/Vendor/Proyek/PO milik MyBhakti, kita tidak menduplikasinya."""
    head = build_nocodb_payload(_sample_contract())["contracts"][0]
    for column in ("mybhakti_client_id", "mybhakti_vendor_id", "mybhakti_project_id", "mybhakti_po_id"):
        assert head[column] is None


def test_document_id_used_as_upsert_key():
    """Menjalankan ulang dokumen yang sama harus memperbarui baris, bukan menggandakannya."""
    payload = build_nocodb_payload(_sample_contract())
    assert payload["contracts"][0]["document_id"] == "doc-test"
    assert payload["contract_items"][0]["item_key"].startswith("doc-test::item::")
    schema = {t["table_name"]: t["primary_key"] for t in build_nocodb_schema(payload)["tables"]}
    assert schema["contracts"] == "document_id"
    assert schema["contract_items"] == "item_key"


@pytest.mark.parametrize("path", sorted(EXTRACTION_DIR.glob("*.extract.json")))
def test_types_match_values_on_real_extractions(path):
    """Regresi terhadap seluruh hasil ekstraksi nyata yang ada di repo."""
    _assert_types_match(build_nocodb_payload(json.loads(path.read_text(encoding="utf-8"))))
