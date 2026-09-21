"""
Open ADE — Exporter payload NocoDB.

Mengubah satu hasil ekstraksi (nested, ber-alias Bahasa Indonesia berspasi) menjadi
baris-baris datar siap POST ke NocoDB:

    {"contracts": [ {...} ], "contract_items": [ {...}, ... ], ...}

Bentuk ini langsung bisa dipakai n8n:
    POST /api/v2/tables/{tableId}/records   body = payload["<nama_tabel>"]

Keputusan desain mengikuti briefing Delivery Ops Layer:

- **Batas kepemilikan data (hlm. 7).** Kita TIDAK menduplikasi Klien/Vendor/Proyek/PO milik
  MyBhakti. Kolom `mybhakti_*_id` sengaja dibiarkan kosong — n8n yang mengisinya saat
  menautkan, sehingga tidak muncul dua sumber kebenaran.
- **Konfirmasi per field, bukan per dokumen (hlm. 11).** Tabel `field_confirmations`
  memuat satu baris per field beserta halaman, kutipan bukti, dan skor keyakinannya.
- **Status sistem BUKAN persetujuan (hlm. 20).** `status_sistem` (AUTO_VERIFIED dsb.) dan
  `pm_confirmed` adalah dua kolom terpisah. `pm_confirmed` selalu lahir False dan hanya
  manusia yang boleh mengubahnya. Begitu pula `harga_terverifikasi` pada baris item.
- **Terukur (hlm. 21).** Tabel `extraction_runs` menyimpan durasi tiap tahap agar
  perbaikan terhadap baseline bisa dibuktikan dengan angka.

Nama kolom sengaja snake_case ASCII: alias asli memakai spasi dan garis miring
("Kategori/Kelompok", "Garansi / SLA") yang merepotkan di URL, ekspresi n8n, dan filter
NocoDB.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PAYLOAD_VERSION = "nocodb-2026.09.1"

# Kolom yang diisi n8n saat menautkan ke MyBhakti (read-only). Kita hanya menyediakan
# tempatnya; isinya bukan milik kita.
_MYBHAKTI_LINKS = {"mybhakti_client_id": None, "mybhakti_vendor_id": None,
                   "mybhakti_project_id": None, "mybhakti_po_id": None}


def _num(value: Any) -> Optional[float]:
    """Nominal -> float, atau None. String kosong/placeholder tidak boleh jadi 0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _txt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return None
    text = str(value).strip()
    return text or None


def _join(value: Any) -> Optional[str]:
    """List of string -> satu teks bernomor, supaya terbaca di sel NocoDB."""
    if not isinstance(value, list) or not value:
        return None
    parts = [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    return "\n".join(f"{n}. {p}" for n, p in enumerate(parts, 1)) or None


def _party(data: Dict[str, Any], key: str, prefix: str) -> Dict[str, Any]:
    party = data.get(key) or {}
    if not isinstance(party, dict):
        party = {}
    return {
        f"{prefix}_nama_perusahaan": _txt(party.get("Nama Perusahaan")),
        f"{prefix}_npwp": _txt(party.get("NPWP")),
        f"{prefix}_nama_pejabat": _txt(party.get("Nama Representative")),
        f"{prefix}_jabatan": _txt(party.get("Jabatan")),
        f"{prefix}_alamat": _txt(party.get("Alamat")),
    }


def _rows(data: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    value = data.get(key)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


# --------------------------------------------------------------------------- field confirmations
def _field_confirmations(doc_id: str, evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Satu baris per field — inti dari "dikonfirmasi per field" (briefing hlm. 11).

    `nilai_final` sengaja dibiarkan kosong: PM mengisinya (atau menyalin `nilai_ekstraksi`)
    saat mengunci. Dengan begitu nilai hasil mesin dan nilai yang disetujui manusia tidak
    pernah tercampur di kolom yang sama.
    """
    rows = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        rows.append({
            "confirmation_key": f"{doc_id}::{item.get('field')}",
            "document_id": doc_id,
            "field_path": item.get("field"),
            "nilai_ekstraksi": _txt(item.get("value")),
            "nilai_final": None,            # diisi PM saat mengunci
            "halaman": item.get("page"),
            "kutipan_bukti": _txt(item.get("evidence_text")),
            "skor_keyakinan": item.get("confidence"),
            "metode_pencocokan": item.get("match_type"),
            "status_sistem": item.get("status"),
            "catatan_sistem": "; ".join(item.get("issues") or []) or None,
            # --- kolom milik manusia, jangan pernah diisi otomatis ---
            "pm_confirmed": False,
            "dikonfirmasi_oleh": None,
            "dikonfirmasi_pada": None,
            "catatan_pm": None,
        })
    return rows


# --------------------------------------------------------------------------- per tipe dokumen
def _contract_tables(doc_id: str, data: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, List[Dict]]:
    head = {
        **base,
        "nomor_kontrak": _txt(data.get("Nomor Kontrak Kerja")),
        "nomor_kontrak_internal": _txt(data.get("Nomor Kontrak Internal")),
        "daftar_nomor_kontrak": _join(data.get("Daftar Nomor Kontrak")),
        "nama_pekerjaan": _txt(data.get("Nama Pekerjaan")),
        "tanggal_negosiasi": _txt(data.get("Tanggal Negosiasi")),
        "tanggal_dokumen": _txt(data.get("Tanggal Pembuatan Dokumen")),
        "lokasi": _txt(data.get("Lokasi")),
        "jangka_waktu": _txt(data.get("Jangka Waktu")),
        "durasi_kerja": _txt(data.get("Durasi Kerja")),
        "sub_total": _num(data.get("sub total")),
        "persentase_ppn": _num(data.get("persentase ppn")),
        "total_ppn": _num(data.get("Total PPN")),
        "total_harga_pekerjaan": _num(data.get("Total Harga Pekerjaan")),
        "jumlah_terbilang": _txt(data.get("Jumlah Terbilang")),
        "nama_bank": _txt(data.get("Nama Bank")),
        "cabang_bank": _txt(data.get("Lokasi Cabang Bank")),
        "nomor_rekening": _txt(data.get("Nomor Rekening Bank")),
        "nama_rekening": _txt(data.get("Nama Rekening Bank")),
        "mekanisme_pembayaran": _txt(data.get("Mekanisme Skema Pembayaran")),
        "persentase_penalti": _txt(data.get("Persentase Sanksi/Penalti")),
        "garansi": _txt(data.get("Garansi")),
        "informasi_bea_meterai": _txt(data.get("Informasi Bea Meterai")),
        **_party(data, "Pihak Pertama", "pihak_pertama"),
        **_party(data, "Pihak Kedua", "pihak_kedua"),
        **_MYBHAKTI_LINKS,
    }
    jaminan = data.get("Klausul Jaminan")
    if isinstance(jaminan, dict):
        head["jaminan_nomor_pasal"] = _txt(jaminan.get("Nomor Pasal"))
        head["jaminan_uraian"] = _txt(jaminan.get("Uraian Jaminan"))

    items = []
    for idx, row in enumerate(_rows(data, "List Item/Barang"), 1):
        items.append({
            "item_key": f"{doc_id}::item::{idx}",
            "document_id": doc_id,
            "nomor_item": _txt(row.get("Nomor Item")) or str(idx),
            "kategori": _txt(row.get("Kategori/Kelompok")),
            "deskripsi": _txt(row.get("Deskripsi Item/Barang/Pekerjaan")),
            "spesifikasi": _txt(row.get("Spesifikasi")),
            "volume": _num(row.get("volume")),
            "satuan": _txt(row.get("unit")),
            "periode": _txt(row.get("Periode/Durasi")),
            "harga_satuan": _num(row.get("Harga Satuan")),
            "jumlah_harga": _num(row.get("Jumlah Harga")),
            "keterangan": _txt(row.get("Keterangan")),
            # Briefing hlm. 20: "Harga selalu diverifikasi manusia".
            "harga_terverifikasi": False,
            "diverifikasi_oleh": None,
        })

    # Lampiran wajib BAST -> umpan untuk checklist gabungan Network Engineer (hlm. 15).
    lampiran = data.get("Syarat Lampiran Wajib BAST")
    requirements = []
    if isinstance(lampiran, list):
        for idx, syarat in enumerate(lampiran, 1):
            if not _txt(syarat):
                continue
            requirements.append({
                "requirement_key": f"{doc_id}::lampiran::{idx}",
                "document_id": doc_id,
                "urutan": idx,
                "uraian_lampiran": _txt(syarat),
                "sumber": "klausul_kontrak",
                "wajib": True,
                "sudah_terpenuhi": False,   # diisi dari evidence ODK oleh n8n
            })

    clauses = [{
        "clause_key": f"{doc_id}::pasal::{idx}",
        "document_id": doc_id,
        "nomor_pasal": _txt(row.get("Nomor Pasal")),
        "judul_pasal": _txt(row.get("Judul Pasal")),
    } for idx, row in enumerate(_rows(data, "Daftar Pasal Kontrak"), 1)]

    supporting = [{
        "support_key": f"{doc_id}::dukung::{idx}",
        "document_id": doc_id,
        "nama_dokumen": _txt(row.get("Nama Dokumen")),
        "nomor_dokumen": _txt(row.get("Nomor Dokumen")),
        "tanggal_dokumen": _txt(row.get("Tanggal Dokumen")),
    } for idx, row in enumerate(_rows(data, "Dokumen Pendukung"), 1)]

    signatories = [{
        "signatory_key": f"{doc_id}::ttd::{idx}",
        "document_id": doc_id,
        "nama": _txt(row.get("Nama")),
        "jabatan": _txt(row.get("Jabatan")),
    } for idx, row in enumerate(_rows(data, "Daftar Penandatangan"), 1)]

    terms = data.get("Ketentuan Pembayaran")
    payment_terms = [{
        "term_key": f"{doc_id}::bayar::{idx}",
        "document_id": doc_id,
        "urutan": idx,
        "uraian": _txt(t),
    } for idx, t in enumerate(terms, 1)] if isinstance(terms, list) else []

    return {
        "contracts": [head],
        "contract_items": items,
        "contract_clauses": clauses,
        "bast_requirements": requirements,
        "supporting_documents": supporting,
        "signatories": signatories,
        "payment_terms": [t for t in payment_terms if t["uraian"]],
    }


def _sph_tables(doc_id: str, data: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, List[Dict]]:
    vendor = data.get("Vendor") or {}
    if not isinstance(vendor, dict):
        vendor = {}
    head = {
        **base,
        "nomor_sph": _txt(data.get("Nomor SPH")),
        "tanggal_sph": _txt(data.get("Tanggal SPH")),
        "perihal": _txt(data.get("Perihal / Nama Pekerjaan")),
        "tujuan_klien": _txt(data.get("Tujuan Surat / Klien")),
        "vendor_nama": _txt(vendor.get("Nama Vendor")),
        "vendor_alamat": _txt(vendor.get("Alamat Vendor")),
        "vendor_kontak": _txt(vendor.get("Kontak / Email")),
        "vendor_npwp": _txt(vendor.get("NPWP")),
        "masa_berlaku": _txt(data.get("Masa Berlaku Penawaran")),
        "berlaku_sampai": _txt(data.get("Berlaku Sampai Tanggal")),
        "jangka_waktu_pengiriman": _txt(data.get("Jangka Waktu Pengiriman")),
        "lokasi_pekerjaan": _txt(data.get("Lokasi Pekerjaan")),
        "subtotal": _num(data.get("Subtotal")),
        "persentase_ppn": _num(data.get("Persentase PPN")),
        "nilai_ppn": _num(data.get("Nilai PPN")),
        "grand_total": _num(data.get("Grand Total")),
        "jumlah_terbilang": _txt(data.get("Jumlah Terbilang")),
        "mekanisme_pembayaran": _txt(data.get("Mekanisme Skema Pembayaran")),
        "garansi_sla": _txt(data.get("Garansi / SLA")),
        "catatan_khusus": _txt(data.get("Catatan Khusus")),
        "syarat_ketentuan": _join(data.get("Syarat dan Ketentuan")),
        **_MYBHAKTI_LINKS,
    }
    items = [{
        "item_key": f"{doc_id}::item::{idx}",
        "document_id": doc_id,
        "nomor": _txt(row.get("No")) or str(idx),
        "kategori": _txt(row.get("Kategori/Kelompok")),
        "nama_item": _txt(row.get("Nama Barang/Jasa")),
        "spesifikasi": _txt(row.get("Spesifikasi")),
        "brand_merek": _txt(row.get("Brand/Merek")),
        "part_number": _txt(row.get("Part Number")),
        "volume": _num(row.get("Volume / Qty")),
        "satuan": _txt(row.get("Satuan")),
        "periode": _txt(row.get("Periode/Durasi")),
        "harga_satuan": _num(row.get("Harga Satuan")),
        "total_harga": _num(row.get("Total Harga")),
        "keterangan": _txt(row.get("Keterangan")),
        "harga_terverifikasi": False,
        "diverifikasi_oleh": None,
    } for idx, row in enumerate(_rows(data, "Daftar Penawaran Harga"), 1)]
    return {"sph_offers": [head], "sph_items": items}


def _bast_tables(doc_id: str, data: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, List[Dict]]:
    head = {
        **base,
        "nomor_bast": _txt(data.get("Nomor BAST")),
        "nama_pekerjaan": _txt(data.get("Nama Pekerjaan")),
        "nomor_po_kontrak": _txt(data.get("Nomor PO / Kontrak")),
        "tanggal_po_kontrak": _txt(data.get("Tanggal PO / Kontrak")),
        "tanggal_serah_terima": _txt(data.get("Tanggal Serah Terima")),
        "tanggal_aktivasi": _txt(data.get("Tanggal Aktivasi Layanan")),
        "tanggal_uji_terima": _txt(data.get("Tanggal Uji Terima")),
        "hasil_uji_keseluruhan": _txt(data.get("Hasil Uji Terima Keseluruhan")),
        "pernyataan_penerimaan": _txt(data.get("Pernyataan Penerimaan")),
        "nilai_pengadaan": _num(data.get("Nilai Pengadaan")),
        # Briefing hlm. 10: BAST Vendor dan BAST Pelanggan punya acuan & risiko berbeda.
        # Arahnya tidak bisa disimpulkan dari isi dokumen, jadi PM/n8n yang menetapkan.
        "arah_bast": None,           # "vendor_ke_kita" | "kita_ke_pelanggan"
        "acuan_kebenaran": None,     # "PO" untuk BAST vendor, "Kontrak/SPK" untuk BAST pelanggan
        **_party(data, "Pihak Pertama", "pihak_pertama"),
        **_party(data, "Pihak Kedua", "pihak_kedua"),
        **_MYBHAKTI_LINKS,
    }
    items = [{
        "item_key": f"{doc_id}::item::{idx}",
        "document_id": doc_id,
        "nomor": _txt(row.get("No")) or str(idx),
        "deskripsi": _txt(row.get("Deskripsi")),
        "volume": _num(row.get("Volume")),
        "satuan": _txt(row.get("Satuan")),
        "hasil_uji": _txt(row.get("Hasil Uji Terima")),
        "keterangan": _txt(row.get("Keterangan")),
    } for idx, row in enumerate(_rows(data, "Daftar Barang/Pekerjaan Diserahkan"), 1)]
    return {"bast_documents": [head], "bast_items": items}


_DISPATCH = {"contract": _contract_tables, "sph": _sph_tables, "bast": _bast_tables}


# --------------------------------------------------------------------------- entry point
def build_nocodb_payload(result: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Hasil `OpenADEEngine.process_full` (atau isi *.extract.json) -> baris-baris datar NocoDB.

    Kunci dict = nama tabel NocoDB, nilainya list baris siap POST.
    """
    data = result.get("data") or {}
    doc_type = (result.get("document_type") or "contract").lower()
    run_info = result.get("run_info") or {}
    validation = result.get("validation") or {}
    quality = result.get("quality_report") or {}
    doc_id = run_info.get("document_id") or "doc-unknown"

    base = {
        "document_id": doc_id,                    # kunci upsert; stabil antar-run (sha1 isi dokumen)
        "source_file": result.get("document_name"),
        "document_type": doc_type,
        "status_validasi": validation.get("status"),
        "catatan_validasi": "; ".join(
            f"[{i.get('severity')}] {i.get('rule')}: {i.get('message')}"
            for i in (validation.get("issues") or []) if isinstance(i, dict)
        ) or None,
        "fill_rate": quality.get("fill_rate"),
        # Markdown utuh hasil parsing, disimpan di kolom LongText NocoDB. Gunanya: saat PM
        # mengonfirmasi field satu per satu (briefing hlm. 11), ia bisa membaca dokumennya
        # langsung di NocoDB tanpa membuka PDF aslinya -- struktur heading/tabel tetap
        # terbaca karena NocoDB merender teks panjang apa adanya.
        "markdown_dokumen": result.get("markdown") or None,
        # Satu dokumen belum tentu selesai dikonfirmasi; flag ini baru True kalau SELURUH
        # baris field_confirmations sudah pm_confirmed. n8n yang menghitungnya.
        "semua_field_terkonfirmasi": False,
        "payload_version": PAYLOAD_VERSION,
    }

    builder = _DISPATCH.get(doc_type)
    if builder is None:
        tables: Dict[str, List[Dict]] = {"unclassified_documents": [dict(base)]}
    else:
        tables = builder(doc_id, data, base)

    tables["field_confirmations"] = _field_confirmations(doc_id, result.get("evidence") or [])
    tables["extraction_runs"] = [{
        "run_id": run_info.get("run_id"),
        "document_id": doc_id,
        "source_file": result.get("document_name"),
        "document_type": doc_type,
        "waktu": run_info.get("timestamp"),
        "parser_engine": run_info.get("parser_engine"),
        "model": run_info.get("model"),
        "prompt_version": run_info.get("prompt_version"),
        "schema_version": run_info.get("schema_version"),
        "jumlah_halaman": run_info.get("page_count"),
        "llm_calls": run_info.get("llm_calls"),
        "llm_seconds": run_info.get("llm_seconds"),
        **{f"durasi_{k}": v for k, v in (run_info.get("timings") or {}).items()},
    }]
    return tables


# --------------------------------------------------------------------------- definisi tabel
# Tipe kolom NocoDB ditentukan dari nama kolom. Aturan ini dipisah dari builder supaya
# tidak ada dua daftar kolom yang harus dijaga sinkron.
#
# Catatan penting soal tanggal: kolom `tanggal_*` sengaja SingleLineText, bukan Date.
# Dokumen Indonesia menulis tanggal bebas ("29 Mei 2026", "27-122024" hasil OCR). Memaksa
# tipe Date membuat baris gagal diimpor, dan yang hilang justru baris yang paling perlu
# dikoreksi manusia.
_CHECKBOX = {"pm_confirmed", "harga_terverifikasi", "wajib", "sudah_terpenuhi",
             "semua_field_terkonfirmasi"}
_DATETIME = {"dikonfirmasi_pada", "waktu"}
_INTEGER = {"halaman", "urutan", "llm_calls", "jumlah_halaman"}
_DECIMAL_HINTS = ("harga", "total", "nilai", "persentase", "volume", "skor_", "fill_rate",
                  "llm_seconds", "durasi_")
_LONGTEXT_HINTS = ("deskripsi", "spesifikasi", "mekanisme_", "catatan_", "kutipan_",
                   "syarat_", "uraian", "pernyataan_", "garansi", "judul_pasal",
                   "daftar_nomor_kontrak", "jumlah_terbilang", "nama_pekerjaan",
                   "informasi_bea_meterai", "alamat", "perihal")
# Kolom kunci untuk upsert idempoten — n8n memakainya agar menjalankan ulang dokumen yang
# sama memperbarui baris, bukan menggandakannya.
_PRIMARY_KEYS = {
    "contracts": "document_id", "sph_offers": "document_id", "bast_documents": "document_id",
    "unclassified_documents": "document_id", "extraction_runs": "run_id",
    "field_confirmations": "confirmation_key", "contract_items": "item_key",
    "sph_items": "item_key", "bast_items": "item_key", "contract_clauses": "clause_key",
    "bast_requirements": "requirement_key", "supporting_documents": "support_key",
    "signatories": "signatory_key", "payment_terms": "term_key",
}


# Kolom yang NAMANYA terdengar numerik tapi isinya teks bebas. Daftar eksplisit ini menang
# atas heuristik di bawah — tanpa ini, "durasi_kerja" ("30 hari kalender"),
# "persentase_penalti" ("1/1000"), dan "nilai_ekstraksi" ("UNIVERSITAS TELKOM") akan
# dibuat sebagai kolom Decimal dan barisnya gagal masuk NocoDB tanpa pesan yang jelas.
_FORCE_TEXT = {
    "nilai_ekstraksi": "LongText", "nilai_final": "LongText",
    "durasi_kerja": "SingleLineText", "persentase_penalti": "SingleLineText",
    "nomor": "SingleLineText", "nomor_item": "SingleLineText",
    "jumlah_terbilang": "LongText", "hasil_uji_keseluruhan": "LongText",
    "markdown_dokumen": "LongText",
}


def _column_type(name: str) -> str:
    if name in _FORCE_TEXT:
        return _FORCE_TEXT[name]
    if name in _CHECKBOX:
        return "Checkbox"
    if name in _DATETIME:
        return "DateTime"
    if name in _INTEGER:
        return "Number"
    if any(h in name for h in _DECIMAL_HINTS):
        return "Decimal"
    if any(h in name for h in _LONGTEXT_HINTS):
        return "LongText"
    return "SingleLineText"


def build_nocodb_schema(payload: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Definisi tabel + tipe kolom, untuk membuat base NocoDB sekali di awal."""
    tables = []
    for table, rows in sorted(payload.items()):
        columns = {}
        for row in rows:
            for key in row:
                columns.setdefault(key, _column_type(key))
        tables.append({
            "table_name": table,
            "primary_key": _PRIMARY_KEYS.get(table, "document_id"),
            "columns": [{"column_name": k, "uidt": v} for k, v in columns.items()],
        })
    return {"payload_version": PAYLOAD_VERSION, "tables": tables}
