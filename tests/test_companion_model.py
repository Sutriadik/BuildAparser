"""
Tests untuk model data companion (app/companion/).

Fokus pada hal yang kalau rusak TIDAK menimbulkan error, hanya data yang salah diam-diam:
arah BAST terbalik, peran pihak tertukar, tanggal salah tafsir, atau pipeline menulis ke
tabel milik manusia.
"""
import pytest

from app.companion.bast_mapper import (
    derive_roles_and_direction,
    detect_handover_party,
    map_bast,
    parse_indonesian_date,
)
from app.companion.ddl import generate_ddl
from app.companion.model import ALL_TABLES, insert_order, table
from app.companion.validate import validate_payload

BUT = "PT. BHAKTI UNGGUL TEKNOVASI"
TELKOM = "PT TELKOM INDONESIA (PERSERO) TBK"
KAMPUS = "UNIVERSITAS TELKOM"

MD_FORMAT_KAMPUS = "PIHAK KEDUA menyerahkan kepada PIHAK PERTAMA hasil Pengadaan ..."
MD_FORMAT_TELKOM = "... Selanjutnya PIHAK PERTAMA\n\n## MENYERAHKAN\n\nKepada PIHAK KEDUA ..."


# ---------------------------------------------------------------- arah & peran
def test_format_kampus_but_di_pihak_kedua_tetap_bast_pelanggan():
    """
    Regresi ke bug nyata di pemeta ini. Versi pertama menurunkan arah dari POSISI BUT:
    karena BUT ada di Pihak Kedua, arahnya terbaca 'vendor'. Padahal kalimatnya jelas
    "PIHAK KEDUA menyerahkan kepada PIHAK PERTAMA" -- BUT menyerahkan, jadi BAST Pelanggan.
    """
    first_role, second_role, direction = derive_roles_and_direction(
        KAMPUS, BUT, MD_FORMAT_KAMPUS)
    assert (first_role, second_role) == ("receiver", "handover")
    assert direction == "customer"


def test_format_telkom_but_di_pihak_pertama_juga_bast_pelanggan():
    first_role, second_role, direction = derive_roles_and_direction(
        BUT, TELKOM, MD_FORMAT_TELKOM)
    assert (first_role, second_role) == ("handover", "receiver")
    assert direction == "customer"


def test_but_menerima_berarti_bast_vendor():
    """BAST Vendor: vendor menyerahkan ke BUT (briefing hlm. 10, acuannya PO bukan kontrak)."""
    _, _, direction = derive_roles_and_direction(
        "PT Belama Guna Daya", BUT, "PIHAK PERTAMA menyerahkan kepada PIHAK KEDUA")
    assert direction == "vendor"


def test_kalimat_tidak_terbaca_tidak_menebak_arah():
    """3 dari 14 BAST tidak memakai kalimat baku. Lebih baik 'unknown' daripada tebakan."""
    first_role, second_role, direction = derive_roles_and_direction(BUT, TELKOM, "teks tanpa pola")
    assert direction == "unknown"
    assert detect_handover_party("teks tanpa pola") is None


# ---------------------------------------------------------------- tanggal
@pytest.mark.parametrize("teks,harapan", [
    ("22 Juni 2026", "2026-06-22"),
    ("11/08/2026", "2026-08-11"),
    ("2026-06-02", "2026-06-02"),
    ("Tanggal Sebelas Bulan Agustus Tahun Dua Ribu Dua Puluh Enam", "2026-08-11"),
])
def test_tanggal_indonesia_terbaca(teks, harapan):
    assert parse_indonesian_date(teks).isoformat() == harapan


@pytest.mark.parametrize("teks", ["", None, "tanggal rusak OCR", "32 Foobar 2026"])
def test_tanggal_tak_terbaca_jadi_none_bukan_tebakan(teks):
    """Teks aslinya tetap disimpan di kolom *_text; lebih baik NULL daripada salah tafsir."""
    assert parse_indonesian_date(teks) is None


# ---------------------------------------------------------------- model & DDL
def test_urutan_insert_induk_sebelum_anak():
    urut = insert_order()
    assert urut.index("document") < urut.index("bast")
    assert urut.index("bast") < urut.index("bast_party")
    assert urut.index("bast") < urut.index("bast_item")


def test_field_review_milik_manusia_dan_tidak_mudah_terhapus():
    t = table("field_review")
    assert t.written_by == "human"
    # Dokumen yang sudah punya keputusan PM tidak boleh terhapus diam-diam.
    assert t.column("document_id").fk_on_delete == "RESTRICT"


def test_ddl_memuat_constraint_nyata():
    ddl = generate_ddl()
    assert "UNIQUE (bast_id, role)" in ddl                       # tepat 2 pihak per BAST
    assert "bast_direction_valid CHECK" in ddl                   # enum jadi CHECK
    assert "ON DELETE RESTRICT" in ddl                           # lindungi keputusan PM
    assert "CREATE OR REPLACE VIEW document_review_status" in ddl  # status dihitung
    assert "CREATE OR REPLACE VIEW field_review_stale" in ddl      # konfirmasi basi


def test_setiap_fk_menunjuk_tabel_yang_ada():
    nama = {t.name for t in ALL_TABLES}
    for t in ALL_TABLES:
        for c in t.columns:
            if c.fk:
                assert c.fk.split(".")[0] in nama, f"{t.name}.{c.name} -> {c.fk}"


# ---------------------------------------------------------------- validator
def _payload_minimal():
    return map_bast({
        "document_name": "BAST uji.pdf",
        "document_type": "bast",
        "data": {
            "Nama Pekerjaan": "Pengadaan uji",
            "Pihak Pertama": {"Nama Perusahaan": KAMPUS, "Nama Representative": "A"},
            "Pihak Kedua": {"Nama Perusahaan": BUT, "Nama Representative": "B"},
            "Daftar Barang/Pekerjaan Diserahkan": [{"Deskripsi": "Lisensi", "Volume": 1}],
        },
        "evidence": [{"field": "Nama Pekerjaan", "value": "Pengadaan uji", "page": 1,
                      "evidence_text": "Pengadaan uji", "evidence_score": 1.0,
                      "status": "auto_verified"}],
        "run_info": {"document_id": "doc-uji", "page_count": 2, "timings": {}},
        "markdown": MD_FORMAT_KAMPUS,
    })


def test_payload_hasil_pemeta_lolos_validasi():
    assert validate_payload(_payload_minimal()) == []


def test_pemeta_tidak_pernah_mengisi_tabel_manusia():
    assert "field_review" not in _payload_minimal()


def test_validator_menolak_pipeline_menulis_field_review():
    payload = _payload_minimal()
    payload["field_review"] = [{"document_id": 1, "field_path": "x",
                                "decision": "confirmed", "reviewed_by": "bot",
                                "reviewed_at": "2026-01-01T00:00:00Z"}]
    masalah = validate_payload(payload)
    assert any("milik manusia" in m for m in masalah)


def test_validator_menangkap_tipe_salah():
    payload = _payload_minimal()
    payload["bast"][0]["basis_doc_value"] = "Rp 174.825.000"     # harus numeric
    assert any("basis_doc_value" in m for m in validate_payload(payload))


def test_validator_menangkap_enum_salah():
    payload = _payload_minimal()
    payload["bast"][0]["direction"] = "pelanggan"                 # harus customer/vendor/unknown
    assert any("bukan nilai sah" in m for m in validate_payload(payload))


def test_validator_menangkap_pihak_tidak_tepat_dua():
    payload = _payload_minimal()
    payload["bast_party"] = payload["bast_party"][:1]
    assert any("seharusnya tepat 2" in m for m in validate_payload(payload))


# ---------------------------------------------------------------- pengiriman ke NocoDB
def _nocodb_palsu():
    """NocoDB tiruan: menyimpan baris di memori dan memberi Id naik. Cukup untuk menguji
    hal yang benar-benar bisa salah -- FK antar-tabel dan idempotensi -- tanpa server."""
    import json as _json
    import httpx

    db: dict = {}
    berikut = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        tid = request.url.path.split("/")[-2]
        rows = db.setdefault(tid, [])
        if request.method == "GET":
            where = request.url.params.get("where", "")
            cocok = []
            for r in rows:
                ok = all(
                    str(r.get(bit.split(",")[0].lstrip("("))) ==
                    bit.split(",", 2)[2].rstrip(")").replace("\\", "")
                    for bit in where.split("~and") if bit
                )
                if ok:
                    cocok.append(r)
            return httpx.Response(200, json={"list": cocok})
        body = _json.loads(request.content)
        if request.method == "POST":
            keluar = []
            for b in body:
                berikut["n"] += 1
                baris = dict(b, Id=berikut["n"])
                rows.append(baris)
                keluar.append({"Id": baris["Id"]})
            return httpx.Response(200, json=keluar)
        if request.method == "PATCH":
            for b in body:
                for r in rows:
                    if r["Id"] == b["Id"]:
                        r.update(b)
            return httpx.Response(200, json=body)
        if request.method == "DELETE":
            buang = {b["Id"] for b in body}
            db[tid] = [r for r in rows if r["Id"] not in buang]
            return httpx.Response(200, json=body)
        return httpx.Response(405)

    return db, httpx.MockTransport(handler)


def _pusher_uji(transport):
    from app.companion.nocodb_push import CompanionPusher
    ids = {t.name: f"tbl_{t.name}" for t in ALL_TABLES}
    return CompanionPusher("http://uji", "token-uji", ids, transport=transport)


def test_push_mengisi_fk_dengan_id_baris_nyata():
    """Inti pemeta->NocoDB: `_bast_ref` harus jadi `bast_id` berisi Id baris bast yang
    baru masuk. Kalau ini salah, baris anak tetap masuk tapi menggantung -- PM tidak
    akan pernah melihatnya di bawah dokumennya."""
    db, transport = _nocodb_palsu()
    hasil = _pusher_uji(transport).push(_payload_minimal())

    id_bast = db["tbl_bast"][0]["Id"]
    assert all(r["bast_id"] == id_bast for r in db["tbl_bast_party"])
    assert all("_bast_ref" not in r for r in db["tbl_bast_party"])
    id_doc = db["tbl_document"][0]["Id"]
    assert db["tbl_bast"][0]["document_id"] == id_doc
    assert all(r["document_id"] == id_doc for r in db["tbl_extracted_field"])
    assert hasil["inserted"] > 0


def test_push_ulang_tidak_menggandakan_baris():
    """Proses ulang dokumen yang sama = UPDATE, bukan baris kembar (idempotensi n8n)."""
    db, transport = _nocodb_palsu()
    payload = _payload_minimal()
    _pusher_uji(transport).push(payload)
    _pusher_uji(transport).push(payload)

    assert len(db["tbl_document"]) == 1
    assert len(db["tbl_bast"]) == 1
    assert len(db["tbl_bast_party"]) == 2
    assert len(db["tbl_bast_item"]) == 1
    # Riwayat justru harus bertambah: dua kali proses = dua baris bukti.
    assert len(db["tbl_extraction_run"]) == 2


def test_push_menolak_payload_berisi_tabel_manusia():
    from app.companion.nocodb_push import CompanionPushError
    payload = _payload_minimal()
    payload["field_review"] = [{"document_id": 1, "field_path": "x", "decision": "confirmed",
                                "reviewed_by": "bot", "reviewed_at": "2026-01-01T00:00:00Z"}]
    _, transport = _nocodb_palsu()
    with pytest.raises(CompanionPushError):
        _pusher_uji(transport).push(payload, dry_run=True)


def test_kunci_alami_diturunkan_dari_model_bukan_daftar_hardcode():
    from app.companion.nocodb_push import natural_key
    assert natural_key(table("document")) == ("content_hash",)
    assert natural_key(table("bast_party")) == ("bast_id", "role")
    assert natural_key(table("bast_condition")) is None   # -> strategi ganti-anak
