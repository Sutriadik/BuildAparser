from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field, ConfigDict

class PihakDetail(BaseModel):
    """
    nama_representative/jabatan/alamat/npwp dibuat Optional: dokumen ringkas (Nota Pesanan,
    PKS tanpa blok tanda tangan formal) sering tidak mencantumkannya. Wajib null jika
    tidak ada di teks -- bukan dikarang oleh LLM.
    """
    model_config = ConfigDict(populate_by_name=True)
    nama_perusahaan: str = Field(..., alias="Nama Perusahaan", description="Nama institusi atau perusahaan yang diwakili")
    npwp: Optional[str] = Field(None, alias="NPWP", description="Nomor Pokok Wajib Pajak (NPWP) jika tertera")
    nama_representative: Optional[str] = Field(None, alias="Nama Representative", description="Nama pejabat atau pihak yang menandatangani, jika disebutkan")
    jabatan: Optional[str] = Field(None, alias="Jabatan", description="Jabatan pejabat terkait, jika disebutkan")
    alamat: Optional[str] = Field(None, alias="Alamat", description="Alamat lengkap domisili instansi/perusahaan, jika disebutkan")

class PasalKontrakDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nomor_pasal: str = Field(..., alias="Nomor Pasal", description="Nomor pasal (misal: 'Pasal 1', 'Pasal 2', '23')")
    judul_pasal: str = Field(..., alias="Judul Pasal", description="Judul/topik pasal (misal: 'LINGKUP PEKERJAAN', 'HARGA PEKERJAAN', 'CARA PEMBAYARAN', 'DENDA', 'PENUTUP')")

class DokumenPendukungDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nama_dokumen: str = Field(..., alias="Nama Dokumen", description="Uraian nama surat atau berita acara konsiderans")
    nomor_dokumen: Optional[str] = Field(None, alias="Nomor Dokumen", description="Nomor surat/dokumen pendukung jika ada")
    tanggal_dokumen: Optional[str] = Field(None, alias="Tanggal Dokumen", description="Tanggal terbit dokumen pendukung")

class PenandatanganDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nama: str = Field(..., alias="Nama", description="Nama pejabat penandatangan")
    jabatan: Optional[str] = Field(None, alias="Jabatan", description="Jabatan pejabat penandatangan")

class KlausulJaminanDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nomor_pasal: Optional[str] = Field(None, alias="Nomor Pasal", description="Nomor pasal yang mengatur jaminan (misal: '10' atau 'Pasal 10')")
    uraian_jaminan: Optional[str] = Field(None, alias="Uraian Jaminan", description="Ketentuan jaminan pelaksanaan, masa laku, dan ganti rugi")

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
    periode: Optional[str] = Field(None, alias="Periode/Durasi", description="Periode waktu atau durasi bulanan/harian jika ada (misal: 12 bulan)")
    harga_satuan: float = Field(..., alias="Harga Satuan", description="Harga satuan per unit dalam Rupiah (MRC/OTC)")
    jumlah_harga: float = Field(..., alias="Jumlah Harga", description="Total harga item dalam Rupiah (MRC/OTC)")
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
    
    # default_factory: baris tabel diisi deterministik dari parser (_reconcile_items), jadi
    # pass-1 LLM tidak perlu mengetik ulang seluruh BoQ.
    items: List[ItemBarangPekerjaan] = Field(default_factory=list, alias="List Item/Barang", description="Daftar rincian lengkap seluruh baris tabel barang, jasa, atau BoQ")
    
    nomor_kontrak: Optional[str] = Field(None, alias="Nomor Kontrak Kerja", description="Nomor resmi SPK / Kontrak / PKS / Nota Pesanan, jika ada")
    nomor_kontrak_internal: Optional[str] = Field(
        None, alias="Nomor Kontrak Internal",
        description="Nomor registrasi kontrak versi pihak kedua/mitra pelaksana, jika dokumen mencantumkan dua nomor sekaligus "
                     "(pola 'Nomor A dan/atau Nomor B'). Null jika hanya ada satu nomor kontrak."
    )
    daftar_nomor_kontrak: Optional[List[str]] = Field(None, alias="Daftar Nomor Kontrak", description="Daftar seluruh nomor kontrak yang tertera pada dokumen")
    tanggal_negosiasi: Optional[str] = Field(None, alias="Tanggal Negosiasi", description="Tanggal dilakukannya negosiasi harga (format: YYYY-MM-DD atau teks asli)")
    nama_pekerjaan: Optional[str] = Field(None, alias="Nama Pekerjaan", description="Judul atau lingkup pengadaan pekerjaan, jika disebutkan")
    
    persentase_ppn: Optional[str] = Field("11%", alias="persentase ppn", description="Persentase PPN yang berlaku")
    jangka_waktu: Optional[str] = Field(None, alias="Jangka Waktu", description="Rentang tanggal pelaksanaan (misal: 02 Januari 2025 – 31 Desember 2025)")
    durasi_kerja: Optional[str] = Field(None, alias="Durasi Kerja", description="Durasi pengerjaan (misal: 12 (dua belas) bulan atau 30 hari kalender)")
    
    nama_bank: Optional[str] = Field(None, alias="Nama Bank", description="Nama bank rekening tujuan pembayaran (misal: Bank Mandiri)")
    cabang_bank: Optional[str] = Field(None, alias="Lokasi Cabang Bank", description="Lokasi cabang bank (misal: KK STT Telkom / Bandung Martadinata)")
    nomor_rekening: Optional[str] = Field(None, alias="Nomor Rekening Bank", description="Nomor rekening bank penyedia")
    nama_rekening: Optional[str] = Field(None, alias="Nama Rekening Bank", description="Nama pemilik rekening bank")
    
    mekanisme_pembayaran: Optional[str] = Field(
        None, 
        alias="Mekanisme Skema Pembayaran", 
        description="Tata cara atau syarat pembayaran (misal: Dilakukan secara bulanan / MRC atau setelah pekerjaan selesai 100% dibuktikan dengan BAST)"
    )
    klausul_ketentuan_pembayaran: Optional[List[str]] = Field(None, alias="Ketentuan Pembayaran", description="Daftar butir ketentuan atau syarat tata cara pembayaran")
    
    persentase_penalti: Optional[str] = Field(None, alias="Persentase Sanksi/Penalti", description="Denda keterlambatan (misal: 1/1000 atau satu permil)")
    lokasi: Optional[str] = Field(None, alias="Lokasi", description="Kota atau lokasi pembuatan/pelaksanaan dokumen (misal: Medan / Bandung)")
    tanggal_pembuatan_dokumen: Optional[str] = Field(None, alias="Tanggal Pembuatan Dokumen", description="Tanggal penandatanganan SPK / Kontrak (misal: 2025-01-02)")
    
    sub_total: Optional[float] = Field(None, alias="sub total", description="Total harga sebelum pajak, jika dokumen memisahkannya dari total")
    total_ppn: Optional[float] = Field(0.0, alias="Total PPN", description="Nominal PPN dalam Rupiah")
    total_harga_pekerjaan: Optional[float] = Field(None, alias="Total Harga Pekerjaan", description="Total nilai kontrak/pesanan termasuk pajak")
    jumlah_terbilang: Optional[str] = Field(None, alias="Jumlah Terbilang", description="Nominal rupiah yang ditulis dalam kata-kata")
    
    garansi: Optional[str] = Field(None, alias="Garansi", description="Klausul garansi atau SLA")
    klausul_jaminan: Optional[KlausulJaminanDetail] = Field(None, alias="Klausul Jaminan", description="Rincian pasal dan ketentuan jaminan pelaksanaan/garansi")
    syarat_lampiran_bast: Optional[List[str]] = Field(None, alias="Syarat Lampiran Wajib BAST", description="Dokumen yang wajib disertakan saat BAST")
    dokumen_pendukung: Optional[List[DokumenPendukungDetail]] = Field(None, alias="Dokumen Pendukung", description="Daftar surat penetapan, berita acara rapat, atau surat kesanggupan yang mendasari kontrak")
    daftar_pasal_kontrak: Optional[List[PasalKontrakDetail]] = Field(None, alias="Daftar Pasal Kontrak", description="Daftar seluruh pasal dan judul pasal yang termuat dalam kontrak")
    daftar_penandatangan: Optional[List[PenandatanganDetail]] = Field(None, alias="Daftar Penandatangan", description="Daftar nama dan jabatan para pihak yang menandatangani kontrak")
    informasi_bea_meterai: Optional[str] = Field(None, alias="Informasi Bea Meterai", description="Ketentuan pajak dan bea meterai pada dokumen")
    daftar_tabel_terstruktur: Optional[List[Dict[str, Any]]] = Field(None, alias="Daftar Tabel Terstruktur", description="Representasi tabel utuh dari dokumen jika ada tabel tambahan")


