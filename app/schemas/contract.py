from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

class PihakDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nama_perusahaan: str = Field(..., alias="Nama Perusahaan", description="Nama institusi atau perusahaan yang diwakili")
    nama_representative: str = Field(..., alias="Nama Representative", description="Nama pejabat atau pihak yang menandatangani")
    jabatan: str = Field(..., alias="Jabatan", description="Jabatan pejabat terkait")
    alamat: str = Field(..., alias="Alamat", description="Alamat lengkap domisili instansi/perusahaan")

class ItemBarangPekerjaan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nomor_item: Optional[str] = Field(
        None, 
        alias="Nomor Item", 
        description="Nomor urut baris item pekerjaan dalam tabel secara berurutan (misal: 1, 2, 3, dst.)"
    )
    kategori: Optional[str] = Field(None, alias="Kategori/Kelompok", description="Kategori kelompok pekerjaan (misal: Tenaga Ahli, Non Personil, Hardware, Jasa)")
    deskripsi: str = Field(..., alias="Deskripsi Item/Barang/Pekerjaan", description="Uraian redaksi lengkap nama barang, spesifikasi teknis, atau layanan")
    spesifikasi: Optional[str] = Field(None, alias="Spesifikasi", description="Spesifikasi teknis, part number, atau cakupan layanan")
    volume: float = Field(..., alias="volume", description="Jumlah kuantitas/volume barang")
    unit: str = Field(..., alias="unit", description="Satuan unit (misal: pkt, unit, bulan, pcs, Orang. Bulan, dsb)")
    periode: Optional[str] = Field(None, alias="Periode/Durasi", description="Periode waktu atau durasi bulanan/harian jika ada")
    harga_satuan: float = Field(..., alias="Harga Satuan", description="Harga satuan per unit dalam Rupiah")
    jumlah_harga: float = Field(..., alias="Jumlah Harga", description="Total harga item dalam Rupiah")
    keterangan: Optional[str] = Field(None, alias="Keterangan", description="Catatan atau keterangan khusus pada baris tabel")
    extra_attributes: Optional[Dict[str, Any]] = Field(None, alias="Atribut Tambahan", description="Kolom atau atribut fleksibel lainnya dari tabel (OTC, MRC, dll)")

class ContractExtractionSchema(BaseModel):
    """
    1-to-1 Compatible Schema dengan LandingAI ADE Extract untuk Dokumen Kontrak & SPK,
    dilengkapi dukungan ekstraksi tabel fleksibel & redaksi lengkap.
    """
    model_config = ConfigDict(populate_by_name=True)
    pihak_pertama: PihakDetail = Field(..., alias="Pihak Pertama", description="Pihak pemberi perintah kerja / klien")
    pihak_kedua: PihakDetail = Field(..., alias="Pihak Kedua", description="Pihak penerima perintah kerja / penyedia")
    
    items: List[ItemBarangPekerjaan] = Field(..., alias="List Item/Barang", description="Daftar rincian lengkap seluruh baris tabel barang, jasa, atau BoQ")
    
    nomor_kontrak: str = Field(..., alias="Nomor Kontrak Kerja", description="Nomor resmi Surat Perintah Kerja / Kontrak")
    tanggal_negosiasi: Optional[str] = Field(None, alias="Tanggal Negosiasi", description="Tanggal dilakukannya negosiasi harga (format: YYYY-MM-DD atau teks asli)")
    nama_pekerjaan: str = Field(..., alias="Nama Pekerjaan", description="Judul atau lingkup pengadaan pekerjaan")
    
    persentase_ppn: Optional[str] = Field("11%", alias="persentase ppn", description="Persentase PPN yang berlaku")
    jangka_waktu: Optional[str] = Field(None, alias="Jangka Waktu", description="Rentang tanggal pelaksanaan (misal: 29 Mei 2026 - 29 Mei 2027)")
    durasi_kerja: Optional[str] = Field(None, alias="Durasi Kerja", description="Durasi pengerjaan (misal: 30 hari kalender)")
    
    nama_bank: Optional[str] = Field(None, alias="Nama Bank", description="Nama bank rekening tujuan pembayaran (misal: Bank Mandiri)")
    cabang_bank: Optional[str] = Field(None, alias="Lokasi Cabang Bank", description="Lokasi cabang bank (misal: KK STT Telkom)")
    nomor_rekening: Optional[str] = Field(None, alias="Nomor Rekening Bank", description="Nomor rekening bank penyedia")
    nama_rekening: Optional[str] = Field(None, alias="Nama Rekening Bank", description="Nama pemilik rekening bank")
    
    mekanisme_pembayaran: Optional[str] = Field(
        None, 
        alias="Mekanisme Skema Pembayaran", 
        description="Tata cara atau syarat pembayaran (misal: Dilakukan setelah pekerjaan selesai 100% dibuktikan dengan BAST)"
    )
    
    persentase_penalti: Optional[str] = Field(None, alias="Persentase Sanksi/Penalti", description="Denda keterlambatan (misal: 1/1000 atau satu per mil)")
    lokasi: Optional[str] = Field(None, alias="Lokasi", description="Kota atau lokasi pembuatan/pelaksanaan dokumen (misal: Bandung)")
    tanggal_pembuatan_dokumen: Optional[str] = Field(None, alias="Tanggal Pembuatan Dokumen", description="Tanggal penandatanganan SPK / Kontrak (misal: 2026-06-02)")
    
    sub_total: float = Field(..., alias="sub total", description="Total harga sebelum pajak")
    total_ppn: Optional[float] = Field(0.0, alias="Total PPN", description="Nominal PPN dalam Rupiah")
    total_harga_pekerjaan: float = Field(..., alias="Total Harga Pekerjaan", description="Total nilai kontrak termasuk pajak")
    jumlah_terbilang: Optional[str] = Field(None, alias="Jumlah Terbilang", description="Nominal rupiah yang ditulis dalam kata-kata")
    
    garansi: Optional[str] = Field(None, alias="Garansi", description="Klausul garansi atau SLA")
    syarat_lampiran_bast: Optional[List[str]] = Field(None, alias="Syarat Lampiran Wajib BAST", description="Dokumen yang wajib disertakan saat BAST")
    daftar_tabel_terstruktur: Optional[List[Dict[str, Any]]] = Field(None, alias="Daftar Tabel Terstruktur", description="Representasi tabel utuh dari dokumen jika ada tabel tambahan")

