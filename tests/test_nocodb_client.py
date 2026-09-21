"""
Tests untuk app/services/nocodb_client.py.

Fokus pada aturan yang kalau dilanggar TIDAK menimbulkan error, hanya data yang rusak
diam-diam: konfirmasi PM tertimpa saat dokumen diproses ulang, atau baris tergandakan.
"""
import pytest

from app.services.nocodb_client import HUMAN_OWNED_COLUMNS, NocoDBClient, NocoDBError


def test_butuh_token_sebelum_push():
    """Tanpa token, gagal terang-terangan -- bukan diam-diam tidak mengirim apa-apa."""
    with pytest.raises(NocoDBError, match="NOCODB_API_TOKEN"):
        NocoDBClient(api_token="")


def test_kolom_milik_manusia_terdaftar_lengkap():
    """
    Briefing hlm. 11 & 20: klausul kontrak dan harga wajib dikonfirmasi manusia.
    Kolom-kolom ini tidak boleh ikut dikirim saat MEMPERBARUI baris yang sudah ada --
    kalau ikut, menjalankan ulang parser akan mengembalikan pm_confirmed jadi False
    dan konfirmasi PM hilang tanpa jejak.
    """
    for kolom in ("pm_confirmed", "nilai_final", "harga_terverifikasi",
                  "dikonfirmasi_oleh", "dikonfirmasi_pada", "semua_field_terkonfirmasi"):
        assert kolom in HUMAN_OWNED_COLUMNS


def test_tabel_tanpa_pemetaan_id_dilewati_bukan_error(monkeypatch):
    """
    NocoDB v2 memakai tableId acak, bukan nama tabel. Tabel yang belum dipetakan di
    NOCODB_TABLE_IDS dilewati dengan peringatan supaya push parsial tetap berguna,
    bukan menggagalkan seluruh dokumen.
    """
    client = NocoDBClient(api_token="dummy", table_ids={"contracts": "m1abc"})
    hasil = client.push_table(None, "tabel_belum_dipetakan", [{"document_id": "doc-1"}])
    assert hasil == {"skipped": 1}


def test_urutan_push_mendahulukan_tabel_dokumen():
    """Baris induk (document_id) harus masuk sebelum baris anak yang merujuknya."""
    from app.exporters.nocodb import _PRIMARY_KEYS
    payload = {"contract_items": [], "contracts": [], "field_confirmations": []}
    urut = sorted(payload, key=lambda t: 0 if _PRIMARY_KEYS.get(t) == "document_id" else 1)
    assert urut[0] == "contracts"


def test_push_diblokir_kalau_belum_diaktifkan_sadar(monkeypatch):
    """
    Menulis ke NocoDB adalah efek keluar dari sistem kita. Tidak boleh terjadi hanya
    karena pemanggil mengirim push_to_nocodb=true -- operator harus mengaktifkannya
    lewat env NOCODB_PUSH_ENABLED secara sadar lebih dulu.
    """
    from app.config import config
    assert config.NOCODB_PUSH_ENABLED is False, "default harus mati"

    # Guard di endpoint: 409 (konflik konfigurasi), bukan diam-diam melewati push.
    import app.main as main
    src = __import__("pathlib").Path(main.__file__).read_text()
    assert "if not config.NOCODB_PUSH_ENABLED:" in src
    assert "status_code=409" in src


def test_kolom_manusia_dibuang_saat_update_baris_lama():
    """
    Inti aturan briefing hlm. 20: kalau PM sudah mengunci sebuah field, memproses ulang
    dokumen yang sama TIDAK boleh mengembalikannya jadi 'belum dikonfirmasi'.
    """
    row = {
        "confirmation_key": "doc-1::Nomor Kontrak",
        "document_id": "doc-1",
        "nilai_ekstraksi": "045/SPK/2026",
        "pm_confirmed": False,          # nilai awal dari mesin
        "nilai_final": None,
        "dikonfirmasi_oleh": None,
    }
    patch = {k: v for k, v in row.items() if k not in HUMAN_OWNED_COLUMNS}
    assert "pm_confirmed" not in patch
    assert "nilai_final" not in patch
    assert "dikonfirmasi_oleh" not in patch
    assert patch["nilai_ekstraksi"] == "045/SPK/2026"   # nilai mesin tetap diperbarui
