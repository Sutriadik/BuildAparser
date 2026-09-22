"""
Open ADE — Definisi model data companion (SATU sumber kebenaran).

Semua yang lain dibangkitkan dari file ini: DDL PostgreSQL, pemeta baris, dan validator.
Alasannya langsung: pada exporter lama, nama kolom ditulis ulang di tiga tempat (builder,
peta tipe kolom, tes), dan itu penyumbang terbesar beban pemeliharaan. Di sini nama kolom
hanya ada satu kali.

Model mengikuti docs/SKEMA_BAST_NOCODB.md. Prinsip yang ditegakkan struktur, bukan kode:

- **Satu penulis per tabel.** `extracted_field` ditulis mesin; `field_review` ditulis manusia.
  Karena terpisah tabel, memproses ulang dokumen secara struktural tidak bisa menimpa
  keputusan PM -- tidak perlu lagi daftar "kolom yang haram ditimpa" seperti di klien lama.
- **Mentah vs terparse.** Nilai apa adanya di dokumen disimpan di kolom `*_text` (bukti,
  tidak pernah diubah); hasil parse bertipe date/numeric di kolom tanpa akhiran dan boleh
  NULL. OCR yang merusak tanggal tidak membuat barisnya gagal masuk.
- **Tidak menyimpan yang bisa dihitung.** Status verifikasi dokumen adalah VIEW.
- **Batas kepemilikan data (briefing hlm. 7).** Tidak ada tabel klien/vendor/PO. Rujukan ke
  MyBhakti berupa kolom `*_ref` opak yang diisi n8n -- sengaja BUKAN foreign key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "companion-2026.09.1"


@dataclass(frozen=True)
class Column:
    name: str
    type: str                      # text | int | numeric | date | timestamptz | char3
    null: bool = True
    unique: bool = False           # UNIQUE satu kolom
    fk: Optional[str] = None       # "tabel.kolom"
    fk_on_delete: str = "CASCADE"  # RESTRICT untuk tabel berisi keputusan manusia
    enum: Optional[tuple] = None   # nilai yang diizinkan -> jadi CHECK
    check: Optional[str] = None    # ekspresi CHECK tambahan
    note: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    columns: List[Column]
    unique_together: tuple = ()    # ((kolom, kolom), ...)
    note: str = ""
    written_by: str = "machine"    # machine | human -- didokumentasikan, bukan ditegakkan DB

    def column(self, name: str) -> Optional[Column]:
        return next((c for c in self.columns if c.name == name), None)


# Kolom audit yang dimiliki semua tabel. Waktu selalu UTC.
_AUDIT = [
    Column("created_at", "timestamptz", null=False, note="UTC"),
    Column("updated_at", "timestamptz", null=False, note="UTC, diperbarui trigger"),
]

DOC_TYPES = ("contract", "sph", "bast")
BAST_DIRECTIONS = ("customer", "vendor", "unknown")
PARTY_ROLES = ("handover", "receiver")
BASIS_DOC_TYPES = ("contract", "pks", "spk", "order_note", "purchase_order", "other")
VAT_MODES = ("included", "excluded", "unstated")
SYSTEM_STATUSES = ("auto_verified", "auto_accepted", "review_required", "conflict", "missing")
REVIEW_DECISIONS = ("confirmed", "corrected", "rejected")
CONDITION_TYPES = (
    "progress_percent", "service_active_since", "acceptance_test_ref",
    "delivery_reconciliation_ref", "supporting_document", "amount_in_words",
)

TABLES: List[Table] = [
    Table(
        "document",
        [
            Column("content_hash", "text", null=False, unique=True,
                   note="sha1 isi berkas -- kunci idempotensi; proses ulang = UPDATE"),
            Column("doc_type", "text", null=False, enum=DOC_TYPES),
            Column("source_filename", "text", null=False),
            Column("page_count", "int", check="page_count > 0"),
            Column("markdown", "text", note="markdown utuh; PM membaca tanpa membuka PDF"),
            *_AUDIT,
        ],
        note="Supertipe: setiap berkas yang masuk, apa pun jenisnya.",
    ),
    Table(
        "extraction_run",
        [
            Column("document_id", "int", null=False, fk="document.id"),
            Column("ocr_engine", "text"),
            Column("llm_model", "text"),
            Column("prompt_version", "text"),
            Column("schema_version", "text"),
            Column("llm_call_count", "int"),
            Column("parse_seconds", "numeric"),
            Column("extract_seconds", "numeric"),
            Column("validation_status", "text"),
            Column("started_at", "timestamptz"),
            *_AUDIT,
        ],
        note="Jejak tiap pemrosesan -- dasar bukti 'Terukur' (briefing hlm. 21).",
    ),
    Table(
        "bast",
        [
            Column("document_id", "int", null=False, unique=True, fk="document.id",
                   note="UNIQUE -> 1:1 dengan document"),
            Column("direction", "text", null=False, enum=BAST_DIRECTIONS,
                   note="customer = kita->pelanggan; vendor = vendor->kita (hlm. 10)"),
            Column("contract_id", "int", fk="contract.id", fk_on_delete="RESTRICT",
                   note="hanya untuk direction=customer"),
            Column("mybhakti_po_ref", "text",
                   note="rujukan opak ke MyBhakti, diisi n8n -- SENGAJA bukan FK (hlm. 7)"),
            Column("bast_number_customer", "text", note="nomor versi pelanggan"),
            Column("bast_number_internal", "text", note="nomor versi BUT"),
            Column("handover_date_text", "text", note="apa adanya di dokumen"),
            Column("handover_date", "date", note="hasil parse; NULL kalau gagal"),
            Column("handover_city", "text"),
            Column("work_title", "text", null=False),
            Column("basis_doc_type", "text", enum=BASIS_DOC_TYPES),
            Column("basis_doc_number", "text", note="seperti tercetak"),
            Column("basis_doc_date_text", "text"),
            Column("basis_doc_date", "date"),
            Column("basis_doc_value", "numeric", check="basis_doc_value >= 0"),
            Column("basis_doc_value_vat", "text", enum=VAT_MODES),
            Column("currency", "char3", null=False, note="default IDR"),
            Column("acceptance_statement", "text"),
            *_AUDIT,
        ],
        note="Subtipe BAST. Kolom di sini hanya butir Tier 1-2 (selalu/hampir selalu ada).",
    ),
    Table(
        "bast_party",
        [
            Column("bast_id", "int", null=False, fk="bast.id"),
            Column("role", "text", null=False, enum=PARTY_ROLES,
                   note="peran, BUKAN 'Pihak Pertama/Kedua' -- label itu terbalik antar-format"),
            Column("org_name_text", "text", null=False, note="nama seperti ditandatangani"),
            Column("signer_name", "text", null=False),
            Column("signer_title", "text"),
            Column("org_address_text", "text"),
            Column("mybhakti_party_ref", "text", note="opak, diisi n8n"),
            *_AUDIT,
        ],
        unique_together=(("bast_id", "role"),),
        note="Tepat 2 baris per BAST. Cuplikan saat penandatanganan -- jabatan berubah, "
             "dokumen yang sudah diteken tidak boleh ikut berubah.",
    ),
    Table(
        "bast_item",
        [
            Column("bast_id", "int", null=False, fk="bast.id"),
            Column("line_no", "int", null=False, check="line_no > 0"),
            Column("description", "text", null=False),
            Column("quantity", "numeric", check="quantity >= 0"),
            Column("unit", "text"),
            Column("unit_price", "numeric", check="unit_price >= 0"),
            Column("line_total", "numeric", check="line_total >= 0"),
            Column("test_result", "text"),
            Column("remarks", "text"),
            *_AUDIT,
        ],
        unique_together=(("bast_id", "line_no"),),
        note="Harga boleh NULL: format kampus/vendor/instansi tidak memuatnya.",
    ),
    Table(
        "bast_condition",
        [
            Column("bast_id", "int", null=False, fk="bast.id"),
            Column("condition_type", "text", null=False, enum=CONDITION_TYPES),
            Column("value_text", "text"),
            Column("value_number", "numeric"),
            Column("value_date", "date"),
            *_AUDIT,
        ],
        note="Fakta Tier 3 sebagai BARIS. Fakta baru = nilai condition_type baru, bukan kolom baru.",
    ),
    Table(
        "extracted_field",
        [
            Column("document_id", "int", null=False, fk="document.id"),
            Column("field_path", "text", null=False, note="mis. bast.work_title"),
            Column("ai_value_text", "text"),
            Column("evidence_page", "int", note="dihitung deterministik, BUKAN ditulis LLM"),
            Column("evidence_quote", "text", note="potongan teks OCR yang cocok"),
            Column("evidence_score", "numeric"),
            Column("system_status", "text", null=False, enum=SYSTEM_STATUSES),
            *_AUDIT,
        ],
        unique_together=(("document_id", "field_path"),),
        note="Ditulis MESIN saja. Nilai terbaru per field.",
    ),
    Table(
        "field_review",
        [
            Column("document_id", "int", null=False, fk="document.id", fk_on_delete="RESTRICT",
                   note="RESTRICT: dokumen dengan keputusan PM tidak boleh terhapus diam-diam"),
            Column("field_path", "text", null=False),
            Column("reviewed_ai_value_text", "text",
                   note="nilai AI yang DILIHAT PM saat memutuskan; kalau nilai AI berubah, "
                        "konfirmasi lama tidak berlaku untuk nilai baru"),
            Column("decision", "text", null=False, enum=REVIEW_DECISIONS),
            Column("final_value_text", "text"),
            Column("reviewed_by", "text", null=False),
            Column("reviewed_at", "timestamptz", null=False),
            Column("reviewer_note", "text"),
            *_AUDIT,
        ],
        unique_together=(("document_id", "field_path"),),
        written_by="human",
        note="Ditulis MANUSIA saja. Pipeline tidak pernah menulis ke sini (hlm. 11 & 20).",
    ),
]

# `contract` belum dimigrasi; didefinisikan minimal agar FK bast.contract_id punya target.
CONTRACT_STUB = Table(
    "contract",
    [
        Column("document_id", "int", null=False, unique=True, fk="document.id"),
        Column("contract_number", "text"),
        *_AUDIT,
    ],
    note="SEMENTARA. Kontrak masih memakai exporter lama; tabel ini baru sasaran FK.",
)

ALL_TABLES: List[Table] = [TABLES[0], CONTRACT_STUB] + TABLES[1:]


def table(name: str) -> Table:
    t = next((t for t in ALL_TABLES if t.name == name), None)
    if t is None:
        raise KeyError(f"Tabel '{name}' tidak ada di model. Ada: {[t.name for t in ALL_TABLES]}")
    return t


def insert_order() -> List[str]:
    """Urutan aman untuk insert: induk sebelum anak (topological sort atas FK)."""
    done, order = set(), []
    remaining = list(ALL_TABLES)
    while remaining:
        maju = False
        for t in list(remaining):
            deps = {c.fk.split(".")[0] for c in t.columns if c.fk}
            if deps <= done:
                order.append(t.name); done.add(t.name); remaining.remove(t); maju = True
        if not maju:
            raise ValueError(f"Siklus FK terdeteksi pada: {[t.name for t in remaining]}")
    return order
