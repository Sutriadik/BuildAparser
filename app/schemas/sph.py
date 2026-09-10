"""
Open ADE — Schema: Surat Penawaran Harga (SPH) / Quotation

Schema khusus untuk dokumen penawaran harga vendor,
termasuk license, hardware, dan jasa IT.
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class SPHVendorInfo(BaseModel):
    nama_vendor: str = Field(..., alias="Nama Vendor", description="Nama perusahaan yang mengajukan penawaran harga")
    alamat_vendor: Optional[str] = Field(None, alias="Alamat Vendor", description="Alamat kantor vendor")
    kontak_vendor: Optional[str] = Field(None, alias="Kontak / Email", description="No telepon atau email vendor")
    npwp: Optional[str] = Field(None, alias="NPWP", description="Nomor Pokok Wajib Pajak vendor")


class SPHItemDetail(BaseModel):
    nomor: Optional[str] = Field("1", alias="No")
    nama_item: str = Field(..., alias="Nama Barang/Jasa", description="Uraian barang, pekerjaan atau lisensi")
    spesifikasi: Optional[str] = Field(None, alias="Spesifikasi", description="Spesifikasi teknis barang/layanan")
    brand_merek: Optional[str] = Field(None, alias="Brand/Merek", description="Merek atau brand produk (misal: Fortinet, Cisco)")
    nomor_part: Optional[str] = Field(None, alias="Part Number", description="Nomor part atau SKU produk")
    volume: float = Field(..., alias="Volume / Qty", description="Jumlah kuantitas")
    satuan: str = Field(..., alias="Satuan", description="Satuan unit (Unit, Bulan, Lot, Pcs, License)")
    harga_satuan: float = Field(..., alias="Harga Satuan", description="Harga satuan per unit dalam Rupiah")
    total_harga: float = Field(..., alias="Total Harga", description="Total harga dalam Rupiah")


class SPHExtractionSchema(BaseModel):
    """
    Schema untuk Surat Penawaran Harga (SPH) Vendor — mendukung
    penawaran license, hardware, jasa IT, dan pengadaan umum.
    """
    vendor: SPHVendorInfo = Field(..., alias="Vendor")
    tujuan_surat: Optional[str] = Field(None, alias="Tujuan Surat / Klien", description="Nama instansi/perusahaan yang dituju")
    
    nomor_sph: str = Field(..., alias="Nomor SPH", description="Nomor surat penawaran harga")
    tanggal_sph: str = Field(..., alias="Tanggal SPH", description="Tanggal penawaran diterbitkan")
    perihal: Optional[str] = Field(None, alias="Perihal / Nama Pekerjaan", description="Perihal surat penawaran harga")
    
    masa_berlaku: Optional[str] = Field(None, alias="Masa Berlaku Penawaran", description="Masa berlaku harga penawaran (misal: 30 hari)")
    tanggal_berlaku_sampai: Optional[str] = Field(None, alias="Berlaku Sampai Tanggal", description="Tanggal kadaluarsa penawaran")
    jangka_waktu_pengiriman: Optional[str] = Field(None, alias="Jangka Waktu Pengiriman", description="Waktu pelaksanaan atau pengiriman barang")
    lokasi_pekerjaan: Optional[str] = Field(None, alias="Lokasi Pekerjaan", description="Lokasi instalasi/pekerjaan")
    
    items: List[SPHItemDetail] = Field(..., alias="Daftar Penawaran Harga", description="Rincian item penawaran (BoQ)")
    
    subtotal: float = Field(..., alias="Subtotal", description="Total sebelum PPN")
    persentase_ppn: Optional[str] = Field("11%", alias="Persentase PPN", description="Persentase PPN")
    ppn_nominal: Optional[float] = Field(0.0, alias="Nilai PPN", description="Nominal PPN")
    grand_total: float = Field(..., alias="Grand Total", description="Total akhir penawaran termasuk pajak")
    
    mekanisme_pembayaran: Optional[str] = Field(None, alias="Mekanisme Skema Pembayaran", description="Termin pembayaran yang ditawarkan")
    garansi_layanan: Optional[str] = Field(None, alias="Garansi / SLA", description="Garansi produk atau SLA layanan yang ditawarkan")
    catatan_khusus: Optional[str] = Field(None, alias="Catatan Khusus", description="Syarat dan ketentuan tambahan dari vendor")
    syarat_ketentuan: Optional[List[str]] = Field(None, alias="Syarat dan Ketentuan", description="Daftar syarat dan ketentuan penawaran")

    class Config:
        populate_by_name = True
