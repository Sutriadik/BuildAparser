"""
Open ADE — OCR Post-Processing & Text Cleaning Module

Membersihkan output OCR (PaddleOCR/Docling) sebelum dikirim ke LLM untuk extraction.
Markdown yang bersih → extraction accuracy jauh lebih tinggi.

Pipeline cleaning:
  1. fix_common_ocr_typos()   — Perbaiki typo karakter yang sering salah OCR
  2. fix_missing_spaces()     — Perbaiki kata/angka yang menempel
  3. normalize_entity_casing()— Normalisasi casing entitas yang acak dari OCR
  4. normalize_whitespace()   — Collapse spasi berlebih, trim baris
  5. clean_ocr_text()         — Master function yang chaining semua cleaner
"""
import re
from typing import Dict, List


# ============================================================================
# 1. Common OCR Typo Dictionary
# ============================================================================

# Karakter yang sering salah di-OCR (case-insensitive patterns)
OCR_TYPO_MAP: Dict[str, str] = {
    # Angka ↔ Huruf confusion
    "Nom0r": "Nomor",
    "nom0r": "nomor",
    "N0mor": "Nomor",
    "n0mor": "nomor",
    "0racle": "Oracle",
    "0racie": "Oracle",
    "Oracie": "Oracle",
    "oracie": "oracle",
    
    # Huruf besar/kecil acak yang sering muncul di OCR
    "UNIvERsITAs": "UNIVERSITAS",
    "TELkOM": "TELKOM",
    "BHAKTi": "BHAKTI",
    "TEKNOvASI": "TEKNOVASI",
    "PlHAK": "PIHAK",
    "PiHAK": "PIHAK",
    "KKSTT": "KK STT",
    
    # Kata-kata umum Indonesia yang sering typo
    "sebesar Rp": "sebesar Rp.",
    "Yangdalam": "Yang dalam",
    "diatas": "di atas",
    "dibawah": "di bawah",
    "apapun": "apapun",
    "sebaik-baiknya.Berdasarkan": "sebaik-baiknya. Berdasarkan",
    
    # Unit dan singkatan
    "pkt": "pkt",
}


def fix_common_ocr_typos(text: str) -> str:
    """
    Mengganti typo OCR yang umum berdasarkan dictionary lookup.
    Proses case-sensitive karena beberapa replacement tergantung casing.
    """
    for typo, correct in OCR_TYPO_MAP.items():
        text = text.replace(typo, correct)
    return text


# ============================================================================
# 2. Fix Missing Spaces
# ============================================================================

def fix_missing_spaces(text: str) -> str:
    """
    Memperbaiki kata/angka/simbol yang menempel tanpa spasi.
    Kasus umum di output PaddleOCR pada dokumen SPK/kontrak Indonesia.
    """
    # Angka menempel huruf: "29Mei" → "29 Mei", "11Kedaung" → "11 Kedaung"
    text = re.sub(r'(\d)([A-Z][a-z])', r'\1 \2', text)
    
    # Huruf kecil menempel huruf besar: "InformasiUniversitas" → "Informasi Universitas"
    text = re.sub(r'([a-z])([A-Z][a-z])', r'\1 \2', text)
    
    # "sebesarRp" → "sebesar Rp"
    text = re.sub(r'([a-z])(Rp[\.\s])', r'\1 \2', text)
    
    # Rp menempel angka: "Rp.174" → "Rp. 174"
    text = re.sub(r'(Rp\.?)(\d)', r'\1 \2', text)
    
    # Titik menempel huruf besar (awal kalimat baru): "terlampir.Berdasarkan" → "terlampir. Berdasarkan"
    text = re.sub(r'\.([A-Z])', r'. \1', text)
    
    # Koma menempel huruf: "Bandung,40257" → "Bandung, 40257"
    text = re.sub(r',(\S)', r', \1', text)
    
    # Titik dua menempel huruf/angka (tapi jangan pecah timestamp/nomor): 
    # "Nomor:" ok, tapi "Alamat:Jl." → "Alamat: Jl."
    text = re.sub(r':([A-Za-z])', r': \1', text)
    
    # Kurung tutup menempel huruf: ")PIHAK" → ") PIHAK"
    text = re.sub(r'\)([A-Z])', r') \1', text)
    
    # Huruf menempel kurung buka: "sah(" → "sah ("
    text = re.sub(r'([a-z])\(', r'\1 (', text)
    
    return text


# ============================================================================
# 3. Normalize Entity Casing  
# ============================================================================

# Pattern untuk entitas yang harus di-uppercase
UPPERCASE_ENTITIES = [
    r'universitas\s+telkom',
    r'pt\.\s*bhakti\s+unggul\s+teknovasi',
    r'pihak\s+pertama',
    r'pihak\s+kedua',
    r'surat\s+perintah\s+kerja',
    r'berita\s+acara\s+serah\s+terima',
]


def normalize_entity_casing(text: str) -> str:
    """
    Normalisasi casing acak dari OCR pada entitas penting.
    Contoh: 'UNIvERsITAs TELkOM' → 'UNIVERSITAS TELKOM'
    """
    for pattern in UPPERCASE_ENTITIES:
        text = re.sub(pattern, lambda m: m.group(0).upper(), text, flags=re.IGNORECASE)
    return text


# ============================================================================
# 4. Normalize Whitespace
# ============================================================================

def normalize_whitespace(text: str) -> str:
    """
    Membersihkan whitespace berlebih tanpa menghilangkan struktur markdown.
    """
    # Collapse multiple spaces menjadi satu (tapi preserve newlines)
    text = re.sub(r'[ \t]+', ' ', text)
    
    # Hilangkan spasi di awal/akhir setiap baris
    lines = text.split('\n')
    lines = [line.strip() for line in lines]
    text = '\n'.join(lines)
    
    # Collapse 3+ newlines berturut-turut menjadi 2 (preserve double newline untuk paragraf)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


# ============================================================================
# 5. Clean Footer Noise
# ============================================================================

# Pola footer yang sering muncul di dokumen SPK/kontrak
FOOTER_NOISE_PATTERNS = [
    # Alamat kampus panjang yang bukan konten
    r'Main\s+Campus\s+Bangkit\s+Building.*?(?:www\.\S+|\.ac\.id)',
    # URL standalone
    r'^www\.\S+\.\S+$',
    # Nomor telepon panjang yang standalone
    r'^[t\s:]*[\+]?\d[\d\s\-/\(\)]{15,}$',
]


def clean_footer_noise(text: str) -> str:
    """
    Menghapus noise footer (alamat kampus, URL, nomor telepon panjang)
    yang ikut ter-parse tapi bukan konten dokumen sebenarnya.
    """
    for pattern in FOOTER_NOISE_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return text


# ============================================================================
# 6. Fix Number Formatting
# ============================================================================

def fix_number_formatting(text: str) -> str:
    """
    Normalisasi format angka rupiah agar konsisten.
    '174.825.000-' → '174.825.000'  (hilangkan dash trailing)
    Preserve format Indonesia: titik sebagai separator ribuan.
    """
    # Hapus dash trailing setelah angka nominal: "174.825.000-" → "174.825.000"
    text = re.sub(r'(\d{1,3}(?:\.\d{3})+)\s*[-–—](?!\d)', r'\1', text)
    
    return text


# ============================================================================
# Master Cleaning Function
# ============================================================================

def clean_ocr_text(text: str) -> str:
    """
    Master function yang menjalankan seluruh pipeline pembersihan OCR.
    Urutan pipeline penting — jangan diubah tanpa testing.
    
    Args:
        text: Raw markdown text dari OCR engine
        
    Returns:
        Cleaned markdown text siap untuk LLM extraction
    """
    if not text:
        return text
    
    # Step 1: Fix typo karakter OCR terlebih dahulu
    text = fix_common_ocr_typos(text)
    
    # Step 2: Fix spasi yang hilang (harus setelah typo fix)
    text = fix_missing_spaces(text)
    
    # Step 3: Normalisasi casing entitas
    text = normalize_entity_casing(text)
    
    # Step 4: Bersihkan footer noise
    text = clean_footer_noise(text)
    
    # Step 5: Fix format angka
    text = fix_number_formatting(text)
    
    # Step 6: Normalize whitespace (selalu terakhir)
    text = normalize_whitespace(text)
    
    return text


def clean_ocr_line(line_text: str) -> str:
    """
    Versi ringan untuk membersihkan satu baris OCR (per-line cleaning).
    Digunakan di PaddleOCR parser sebelum merge.
    
    Args:
        line_text: Satu baris teks dari OCR
        
    Returns:
        Cleaned line text
    """
    if not line_text:
        return line_text
    
    line_text = fix_common_ocr_typos(line_text)
    line_text = fix_missing_spaces(line_text)
    line_text = re.sub(r'\s+', ' ', line_text).strip()
    
    return line_text
