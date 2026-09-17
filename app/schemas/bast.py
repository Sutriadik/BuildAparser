"""
Open ADE — Schema: Berita Acara Serah Terima (BAST)

BAST adalah dokumen serah terima yang memicu penagihan (lihat Delivery Ops Layer briefing:
"BAST Pelanggan yang menahan cashflow"). Berbeda dari Kontrak/SPH, tabel isinya biasanya
TANPA kolom harga (hanya daftar barang/pekerjaan yang diserahkan), dan seringkali dalam satu
file yang sama juga memuat "Berita Acara Uji Terima" (verifikasi teknis hasil pekerjaan) --
karena itu field hasil uji terima dibuat sebagai bagian opsional dari skema yang sama.

Field pihak memakai PihakDetail yang sama dengan skema kontrak (konsisten & bisa null
untuk representative/jabatan/alamat) karena BAST juga memuat blok "PIHAK PERTAMA/KEDUA"
atau "PIHAK KESATU/KEDUA" dengan struktur identik.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict

from app.schemas.contract import PihakDetail


class BASTItemDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nomor: Optional[str] = Field(None, alias="No", description="Nomor urut baris barang/pekerjaan yang diserahkan")
    deskripsi: str = Field(..., alias="Deskripsi", description="Nama/uraian barang atau pekerjaan yang diserahterimakan")
    volume: Optional[float] = Field(None, alias="Volume", description="Jumlah/kuantitas barang")
    satuan: Optional[str] = Field(None, alias="Satuan", description="Satuan unit (Unit, Paket, Lisensi, dsb)")
    hasil_uji: Optional[str] = Field(None, alias="Hasil Uji Terima", description="'Baik' atau 'Tidak' jika dokumen memiliki kolom uji terima per-item")
    keterangan: Optional[str] = Field(None, alias="Keterangan", description="Catatan tambahan pada baris")
    extra_attributes: Optional[Dict[str, Any]] = Field(None, alias="Atribut Tambahan", description="Kolom tambahan lain dari tabel jika ada")


class BASTExtractionSchema(BaseModel):
    """
    Sebagian besar field Optional secara sengaja: BAST bervariasi tergantung klien/proyek --
    beberapa mencantumkan nilai pengadaan, beberapa tidak; beberapa memuat bagian "Uji Terima",
    beberapa tidak. Nilai yang tidak ada di dokumen WAJIB null, bukan dikarang LLM.
    """
    model_config = ConfigDict(populate_by_name=True)

    nomor_bast: Optional[str] = Field(None, alias="Nomor BAST", description="Nomor resmi dokumen BAST, jika dicantumkan")
    nama_pekerjaan: Optional[str] = Field(None, alias="Nama Pekerjaan", description="Judul pekerjaan/pengadaan yang diserahterimakan")
    nomor_po_kontrak: Optional[str] = Field(None, alias="Nomor PO / Kontrak", description="Nomor PO/SPK/Kontrak/Nota Pesanan rujukan yang mendasari BAST")
    tanggal_po_kontrak: Optional[str] = Field(None, alias="Tanggal PO / Kontrak", description="Tanggal PO/SPK/Kontrak rujukan")
    tanggal_serah_terima: Optional[str] = Field(None, alias="Tanggal Serah Terima", description="Tanggal pelaksanaan serah terima (format: YYYY-MM-DD atau teks asli)")

    pihak_pertama: PihakDetail = Field(..., alias="Pihak Pertama", description="Pihak penerima barang/pekerjaan (Pemberi Kerja / Klien / Pemesan). Kadang disebut PIHAK KESATU")
    pihak_kedua: PihakDetail = Field(..., alias="Pihak Kedua", description="Pihak penyedia/pelaksana yang menyerahkan barang/pekerjaan")

    items: List[BASTItemDetail] = Field(..., alias="Daftar Barang/Pekerjaan Diserahkan", description="Rincian seluruh baris barang/pekerjaan pada tabel serah terima")

    nilai_pengadaan: Optional[float] = Field(None, alias="Nilai Pengadaan", description="Nilai total pengadaan/pekerjaan dalam Rupiah, jika dicantumkan di BAST")
    pernyataan_penerimaan: Optional[str] = Field(None, alias="Pernyataan Penerimaan", description="Kalimat pernyataan kondisi barang/hasil pekerjaan diterima (misal: 'diterima dalam kondisi baik, lengkap, dan sesuai')")

    tanggal_uji_terima: Optional[str] = Field(None, alias="Tanggal Uji Terima", description="Tanggal pelaksanaan Berita Acara Uji Terima, jika dokumen memuat bagian ini")
    hasil_uji_terima: Optional[str] = Field(None, alias="Hasil Uji Terima Keseluruhan", description="Kesimpulan akhir uji terima jika ada, misal 'DITERIMA DENGAN HASIL BAIK DAN LENGKAP'")

    dokumen_pendukung: Optional[List[str]] = Field(None, alias="Dokumen Pendukung", description="Daftar lampiran wajib yang disebutkan, misal Purchase Order, Delivery Order/Surat Jalan")
    daftar_tabel_terstruktur: Optional[List[Dict[str, Any]]] = Field(None, alias="Daftar Tabel Terstruktur", description="Representasi tabel utuh dari dokumen jika ada tabel tambahan")
