"""
Open ADE — Pemeta hasil ekstraksi BAST -> baris tabel companion.

Masukan: isi `*.extract.json` (atau hasil `OpenADEEngine.process_full`).
Keluaran: {nama_tabel: [baris, ...]} sesuai app/companion/model.py.

Dua hal yang dikerjakan di sini dan TIDAK diserahkan ke LLM:

1. **Menurunkan peran & arah BAST.** Label "PIHAK PERTAMA/KEDUA" tidak menentukan peran:
   pada 14 BAST asli, pihak pertama adalah penyerah di format Telkom tapi penerima di
   format kampus. Peran dibaca dari kalimat "menyerahkan"; arah lalu diturunkan dari peran
   BUT -- menyerahkan -> BAST Pelanggan, menerima -> BAST Vendor (briefing hlm. 10).
2. **Memisahkan nilai mentah dan terparse.** Tanggal ditulis bermacam cara ("Tanggal Sebelas
   Bulan Agustus...", "11/08/2026") dan OCR bisa merusaknya. Teks asli selalu disimpan;
   hasil parse boleh NULL. Baris tidak pernah gagal masuk hanya karena tanggalnya aneh.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.companion.model import SCHEMA_VERSION

# Nama perusahaan kita. Dipakai untuk menentukan arah BAST.
OUR_ORG_PATTERNS = (r"bhakti\s*unggul\s*teknovasi", r"\bBUT\b")

_BULAN = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}
_SATUAN = {
    "nol": 0, "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5,
    "enam": 6, "tujuh": 7, "delapan": 8, "sembilan": 9,
}


def parse_angka_kata(teks: str) -> Optional[int]:
    """
    Bilangan Indonesia berupa kata -> int. Cukup untuk tanggal: hari 1-31, tahun 2000-2099.

    Diperlukan karena 3 dari 11 BAST menulis tanggal HANYA dalam kata, tanpa angka
    pembanding dalam kurung ("tanggal Dua Puluh Dua bulan Juni tahun Dua Ribu Dua Puluh Enam").
    Tanpa ini, tanggal dokumen-dokumen itu selalu NULL.
    """
    kata = [w for w in re.split(r"[\s-]+", teks.lower().strip()) if w]
    if not kata:
        return None
    # Tiga penampung, karena satu penampung tidak cukup: pengali ("puluh") memakai angka
    # SEBELUMNYA, sedangkan sisa kelompok ("dua" pada "dua puluh dua") harus DITAMBAHKAN.
    # Versi pertama hanya punya `sekarang` sehingga "dua puluh dua" terbaca 2, bukan 22.
    total = 0        # kelompok yang sudah ditutup oleh "ribu"
    grup = 0         # kelompok berjalan (< 1000) yang sudah pasti
    sekarang = 0     # satuan yang belum tahu akan dikali apa
    for w in kata:
        if w == "se":            # jaga-jaga bila terpisah spasi
            sekarang = 1
        elif w in _SATUAN:
            sekarang = _SATUAN[w]
        elif w == "sepuluh":
            grup += 10
            sekarang = 0
        elif w == "sebelas":
            grup += 11
            sekarang = 0
        elif w == "belas":       # "tiga belas" = 13
            grup += 10 + sekarang
            sekarang = 0
        elif w == "puluh":       # "dua puluh" = 20, satuan berikutnya ditambahkan
            grup += (sekarang or 1) * 10
            sekarang = 0
        elif w == "ratus":
            grup += (sekarang or 1) * 100
            sekarang = 0
        elif w == "seratus":
            grup += 100
            sekarang = 0
        elif w in ("ribu", "seribu"):
            total += (grup + sekarang or 1) * 1000
            grup = sekarang = 0
        else:
            return None          # kata asing -> jangan menebak
    return (total + grup + sekarang) or None


def _txt(v: Any) -> Optional[str]:
    if v is None or isinstance(v, (list, dict)):
        return None
    s = str(v).strip()
    return s or None


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.-]", "", str(v))
    if not s:
        return None
    # Format Indonesia: titik = pemisah ribuan, koma = desimal.
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_indonesian_date(text: Optional[str]) -> Optional[date]:
    """
    Kembalikan date bila teks bisa dibaca dengan yakin, selain itu None.
    Sengaja konservatif: lebih baik NULL daripada tanggal yang salah tafsir -- teks aslinya
    tetap tersimpan di kolom `*_text` sebagai bukti.
    """
    if not text:
        return None
    t = str(text).strip().lower()

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)            # 2026-08-11
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return _safe_date(y, mo, d)

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", t)      # 11/08/2026
    if m:
        d, mo, y = (int(x) for x in m.groups())
        return _safe_date(y, mo, d)

    m = re.search(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", t)         # 11 Agustus 2026
    if m and m.group(2) in _BULAN:
        return _safe_date(int(m.group(3)), _BULAN[m.group(2)], int(m.group(1)))

    # Seluruhnya kata: "tanggal Dua Puluh Dua bulan Juni tahun Dua Ribu Dua Puluh Enam"
    m = re.search(r"tanggal\s+([a-z\s]+?)\s+bulan\s+([a-z]+)\s+tahun\s+([a-z\s]+)", t)
    if m and m.group(2) in _BULAN:
        hari = parse_angka_kata(m.group(1))
        tahun = parse_angka_kata(m.group(3))
        if hari and tahun and 1 <= hari <= 31 and 2000 <= tahun <= 2099:
            return _safe_date(tahun, _BULAN[m.group(2)], hari)
    return None


def _iso(d: Optional[date]) -> Optional[str]:
    """date -> 'YYYY-MM-DD'. Payload harus JSON-serializable, dan validator
    mengharapkan kolom bertipe `date` berupa string ISO."""
    return d.isoformat() if d else None


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _is_our_org(name: Optional[str]) -> bool:
    if not name:
        return False
    return any(re.search(p, name, re.I) for p in OUR_ORG_PATTERNS)


def detect_handover_party(markdown: str) -> Optional[str]:
    """
    -> "first" | "second" | None : pihak mana yang MENYERAHKAN, dibaca dari kalimatnya.

    Diuji pada 14 BAST: pola terbaca di 11 dokumen, 3 sisanya (format instansi & vendor)
    tidak memakai kalimat baku ini.
    """
    t = re.sub(r"\s+", " ", markdown[:6000])

    # Format kampus: "PIHAK KEDUA menyerahkan kepada PIHAK PERTAMA ..."
    m = re.search(r"pihak\s+(pertama|kedua)\s+menyerahkan\s+kepada", t, re.I)
    if m:
        return "first" if m.group(1).lower() == "pertama" else "second"

    # Format Telkom: "... Selanjutnya PIHAK PERTAMA" lalu heading "MENYERAHKAN"
    if re.search(r"##\s*MENYERAHKAN", t, re.I):
        m = re.search(r"selanjutnya\s+pihak\s+(pertama|kedua)", t, re.I)
        if m:
            return "first" if m.group(1).lower() == "pertama" else "second"
    return None


def derive_roles_and_direction(
    first_org: Optional[str], second_org: Optional[str], markdown: str
) -> Tuple[str, str, str]:
    """
    -> (peran_pihak_pertama, peran_pihak_kedua, direction)

    Dua langkah terpisah, dan urutannya penting:

    1. **Peran** dibaca dari kalimat "menyerahkan" di dokumen. Label "Pihak Pertama/Kedua"
       TIDAK menentukan peran -- pada 14 BAST, pihak pertama adalah penyerah di format
       Telkom tapi penerima di format kampus.
    2. **Arah** diturunkan dari peran BUT, bukan dari posisinya. BUT menyerahkan ->
       `customer` (kita -> pelanggan); BUT menerima -> `vendor` (briefing hlm. 10).

    Versi pertama fungsi ini menurunkan arah langsung dari posisi BUT, dan itu salah:
    pada BAST ATS Oracle, BUT ada di Pihak Kedua tetapi menyerahkan, sehingga arahnya
    terbaca `vendor` padahal seharusnya `customer`.
    """
    handover_side = detect_handover_party(markdown)
    if handover_side == "first":
        first_role, second_role = "handover", "receiver"
    elif handover_side == "second":
        first_role, second_role = "receiver", "handover"
    else:
        # Kalimatnya tidak terbaca: jangan menebak peran dari posisi.
        return "handover", "receiver", "unknown"

    our_role = (first_role if _is_our_org(first_org)
                else second_role if _is_our_org(second_org) else None)
    direction = {"handover": "customer", "receiver": "vendor"}.get(our_role, "unknown")
    return first_role, second_role, direction


def _basis_doc_type(number: Optional[str], markdown: str) -> Optional[str]:
    hay = f"{number or ''} {markdown[:4000]}".lower()
    for pattern, value in (
        (r"nota\s+pesanan", "order_note"),
        (r"surat\s+pesanan|inaproc", "order_note"),
        (r"\bpks\b|perjanjian\s+kerja", "pks"),
        (r"surat\s+perintah\s+kerja|\bspk\b", "spk"),
        (r"purchase\s+order|\bpo\b\s*/", "purchase_order"),
        (r"kontrak", "contract"),
    ):
        if re.search(pattern, hay):
            return value
    return None


def _vat_mode(markdown: str) -> str:
    head = markdown[:4000].lower()
    if re.search(r"belum\s+termasuk\s+ppn", head):
        return "excluded"
    if re.search(r"(sudah|termasuk)\s+(termasuk\s+)?ppn|sesudah\s+ppn", head):
        return "included"
    return "unstated"


def file_content_hash(path: Path) -> str:
    """sha1 ISI BERKAS -> kunci idempotensi `document.content_hash`.

    Sengaja dari byte PDF, bukan dari `run_info.document_id`. `document_id` dibangkitkan
    dari markdown (app/document_ir/adapter.py:42), jadi berkas yang sama menghasilkan hash
    BERBEDA kalau OCR-nya diganti -- dan dokumen yang sama akan masuk dua kali sebagai dua
    baris `document`. Hash byte tidak berubah oleh pilihan OCR.
    """
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return f"sha1-{h.hexdigest()}"


def map_bast(result: Dict[str, Any],
             content_hash: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """`content_hash` sebaiknya hasil file_content_hash(pdf). Tanpa itu dipakai
    `run_info.document_id` sebagai cadangan -- tetap jalan, tapi idempotensinya
    ikut berubah bila OCR diganti."""
    data = result.get("data") or {}
    run = result.get("run_info") or {}
    markdown = result.get("markdown") or ""
    validation = result.get("validation") or {}
    doc_hash = content_hash or run.get("document_id") or "doc-unknown"

    first = data.get("Pihak Pertama") or {}
    second = data.get("Pihak Kedua") or {}
    first_org, second_org = _txt(first.get("Nama Perusahaan")), _txt(second.get("Nama Perusahaan"))
    first_role, second_role, direction = derive_roles_and_direction(
        first_org, second_org, markdown)

    handover_text = _txt(data.get("Tanggal Serah Terima"))
    basis_number = _txt(data.get("Nomor PO / Kontrak"))
    basis_date_text = _txt(data.get("Tanggal PO / Kontrak"))

    document = {
        "content_hash": doc_hash,
        "doc_type": "bast",
        "source_filename": result.get("document_name"),
        "page_count": run.get("page_count"),
        "markdown": markdown or None,
    }

    bast = {
        "_document_ref": doc_hash,
        "direction": direction,
        "contract_id": None,
        "mybhakti_po_ref": None,          # diisi n8n
        "bast_number_customer": _txt(data.get("Nomor BAST")),
        "bast_number_internal": None,     # format Telkom punya nomor kedua; belum diekstrak
        "handover_date_text": handover_text,
        "handover_date": _iso(parse_indonesian_date(handover_text)),
        "handover_city": None,
        "work_title": _txt(data.get("Nama Pekerjaan")) or "(tidak terbaca)",
        "basis_doc_type": _basis_doc_type(basis_number, markdown),
        "basis_doc_number": basis_number,
        "basis_doc_date_text": basis_date_text,
        "basis_doc_date": _iso(parse_indonesian_date(basis_date_text)),
        "basis_doc_value": _num(data.get("Nilai Pengadaan")),
        "basis_doc_value_vat": _vat_mode(markdown),
        "currency": "IDR",
        "acceptance_statement": _txt(data.get("Pernyataan Penerimaan"))
                                or _txt(data.get("Hasil Uji Terima Keseluruhan")),
    }

    parties = []
    for party, role in ((first, first_role), (second, second_role)):
        org = _txt(party.get("Nama Perusahaan"))
        if not org:
            continue
        parties.append({
            "_bast_ref": doc_hash,
            "role": role,
            "org_name_text": org,
            "signer_name": _txt(party.get("Nama Representative")) or "(tidak terbaca)",
            "signer_title": _txt(party.get("Jabatan")),
            "org_address_text": _txt(party.get("Alamat")),
            "mybhakti_party_ref": None,
        })

    items = []
    raw_items = data.get("Daftar Barang/Pekerjaan Diserahkan")
    for i, row in enumerate(raw_items if isinstance(raw_items, list) else [], 1):
        if not isinstance(row, dict):
            continue
        desc = _txt(row.get("Deskripsi"))
        if not desc:
            continue
        items.append({
            "_bast_ref": doc_hash,
            "line_no": i,
            "description": desc,
            "quantity": _num(row.get("Volume")),
            "unit": _txt(row.get("Satuan")),
            "unit_price": None,
            "line_total": None,
            "test_result": _txt(row.get("Hasil Uji Terima")),
            "remarks": _txt(row.get("Keterangan")),
        })

    conditions = []

    def add_condition(ctype: str, text: Optional[str] = None,
                      number: Optional[float] = None, dt: Optional[date] = None) -> None:
        if text is None and number is None and dt is None:
            return
        conditions.append({"_bast_ref": doc_hash, "condition_type": ctype,
                           "value_text": text, "value_number": number,
                           "value_date": _iso(dt)})

    aktif = _txt(data.get("Tanggal Aktivasi Layanan"))
    add_condition("service_active_since", aktif, dt=parse_indonesian_date(aktif))
    uji = _txt(data.get("Tanggal Uji Terima"))
    add_condition("acceptance_test_ref", uji, dt=parse_indonesian_date(uji))
    for doc in (data.get("Dokumen Pendukung") or []):
        if _txt(doc):
            add_condition("supporting_document", _txt(doc))
    m = re.search(r"progress[^\n]{0,40}?(\d{1,3})\s*%", markdown, re.I)
    if m:
        add_condition("progress_percent", m.group(0).strip(), number=float(m.group(1)))
    m = re.search(r"\(\s*([A-Za-z][A-Za-z\s]{10,200}?rupiah)\s*\)", markdown, re.I)
    if m:
        add_condition("amount_in_words", re.sub(r"\s+", " ", m.group(1)).strip())

    fields = []
    for ev in (result.get("evidence") or []):
        if not isinstance(ev, dict):
            continue
        fields.append({
            "_document_ref": doc_hash,
            "field_path": f"bast.{ev.get('field')}",
            "ai_value_text": _txt(ev.get("value")),
            "evidence_page": ev.get("page"),
            "evidence_quote": _txt(ev.get("evidence_text")),
            "evidence_score": ev.get("evidence_score"),
            "system_status": (ev.get("status") or "missing").lower(),
        })

    timings = run.get("timings") or {}
    extraction_run = {
        "_document_ref": doc_hash,
        "ocr_engine": run.get("parser_engine"),
        "llm_model": run.get("model"),
        "prompt_version": run.get("prompt_version"),
        "schema_version": SCHEMA_VERSION,
        "llm_call_count": run.get("llm_calls"),
        "parse_seconds": timings.get("parse_s"),
        "extract_seconds": timings.get("extract_s"),
        "validation_status": validation.get("status"),
        "started_at": run.get("timestamp"),
    }

    return {
        "document": [document],
        "bast": [bast],
        "bast_party": parties,
        "bast_item": items,
        "bast_condition": conditions,
        "extracted_field": fields,
        "extraction_run": [extraction_run],
        # field_review sengaja TIDAK dihasilkan: tabel itu milik manusia.
    }
