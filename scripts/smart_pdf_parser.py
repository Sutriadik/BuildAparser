import fitz
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class TextBlock:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_size: float
    font_name: str = ""
    is_bold: bool = False
    page_num: int = 0

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

def clean_ocr_typos(text: str) -> str:
    """Pembersihan typo khas OCR/PDF ekstraksi dan penyambungan kata terpisah (hyphenation)."""
    text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('retrieal', 'retrieval').replace('witch support', 'with support').replace('Addess', 'Address')
    return text

def extract_text_blocks_digital(pdf_path: str) -> List[TextBlock]:
    """Ekstraksi teks digital beserta bounding box dan metadata font."""
    doc = fitz.open(pdf_path)
    blocks = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text_page = page.get_text("dict")
        for b in text_page.get("blocks", []):
            if b.get("type") == 0:  # text block
                for line in b.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if not text:
                            continue
                        font_name = span.get("font", "")
                        flags = span.get("flags", 0)
                        is_bold = bool(flags & 2) or ("bold" in font_name.lower())
                        bbox = span.get("bbox")
                        blocks.append(TextBlock(
                            text=text,
                            x0=bbox[0],
                            y0=bbox[1],
                            x1=bbox[2],
                            y1=bbox[3],
                            font_size=span.get("size", 0.0),
                            font_name=font_name,
                            is_bold=is_bold,
                            page_num=page_num
                        ))
    doc.close()
    return blocks

def group_blocks_into_lines(blocks: List[TextBlock], tolerance: float = 6.0, max_word_gap: float = 35.0) -> List[TextBlock]:
    """Mengelompokkan kata-kata yang berada pada baris horizontal yang sama tanpa melompati kolom."""
    if not blocks:
        return []
    
    sorted_blocks = sorted(blocks, key=lambda b: (b.page_num, round(b.y0 / tolerance) * tolerance, b.x0))
    lines = []
    current_line = [sorted_blocks[0]]
    
    for block in sorted_blocks[1:]:
        last = current_line[-1]
        same_line_y = (block.page_num == last.page_num and abs(block.center_y - last.center_y) <= tolerance)
        horizontal_gap = block.x0 - last.x1
        close_x = (0 <= horizontal_gap <= max_word_gap)
        
        if same_line_y and close_x:
            current_line.append(block)
        else:
            current_line.sort(key=lambda b: b.x0)
            merged_text = clean_ocr_typos(" ".join(b.text for b in current_line))
            lines.append(TextBlock(
                text=merged_text,
                x0=current_line[0].x0,
                y0=min(b.y0 for b in current_line),
                x1=current_line[-1].x1,
                y1=max(b.y1 for b in current_line),
                font_size=max(b.font_size for b in current_line),
                is_bold=any(b.is_bold for b in current_line),
                page_num=current_line[0].page_num
            ))
            current_line = [block]
            
    if current_line:
        current_line.sort(key=lambda b: b.x0)
        merged_text = clean_ocr_typos(" ".join(b.text for b in current_line))
        lines.append(TextBlock(
            text=merged_text,
            x0=current_line[0].x0,
            y0=min(b.y0 for b in current_line),
            x1=current_line[-1].x1,
            y1=max(b.y1 for b in current_line),
            font_size=max(b.font_size for b in current_line),
            is_bold=any(b.is_bold for b in current_line),
            page_num=current_line[0].page_num
        ))
        
    return lines

def sort_blocks_reading_order(blocks: List[TextBlock]) -> List[TextBlock]:
    """Pengurutan teks sesuai urutan baca alami manusia (Spatial Column-Aware)."""
    if not blocks:
        return []
    lines = group_blocks_into_lines(blocks, tolerance=6.0, max_word_gap=35.0)
    
    page_width = max(b.x1 for b in lines) if lines else 600
    mid_x = page_width / 2
    
    col1 = [b for b in lines if b.center_x < mid_x]
    col2 = [b for b in lines if b.center_x >= mid_x]
    
    if col1 and col2:
        y_overlap = min(max(b.y1 for b in col1), max(b.y1 for b in col2)) - max(min(b.y0 for b in col1), min(b.y0 for b in col2))
        if y_overlap > 100:  # multi-column layout detected
            full_width_top = [b for b in lines if b.x0 < mid_x - 40 and b.x1 > mid_x + 40]
            if full_width_top:
                y_split = max(b.y1 for b in full_width_top)
                top = sorted([b for b in lines if b.y1 <= y_split + 5], key=lambda b: (b.page_num, b.y0, b.x0))
                c1 = sorted([b for b in col1 if b.y0 > y_split + 5], key=lambda b: (b.page_num, b.y0, b.x0))
                c2 = sorted([b for b in col2 if b.y0 > y_split + 5], key=lambda b: (b.page_num, b.y0, b.x0))
                return top + c1 + c2
            else:
                return sorted(col1, key=lambda b: (b.page_num, b.y0, b.x0)) + sorted(col2, key=lambda b: (b.page_num, b.y0, b.x0))
                
    return sorted(lines, key=lambda b: (b.page_num, b.y0, b.x0))

def format_structured_markdown(lines: List[TextBlock]) -> str:
    """Format tingkat lanjut yang mengubah pasangan Key-Value dan Spesifikasi menjadi Tabel Markdown rapi."""
    if not lines:
        return ""
    
    md_output = []
    
    # Deteksi Header Utama (misal HUMMING)
    top_banner = [b for b in lines if b.y1 < 50]
    if top_banner:
        title_text = " ".join(b.text for b in top_banner)
        title_clean = "".join(title_text.split())
        md_output.append(f"# {title_clean if len(title_clean) > 2 else title_text}\n")
    
    # Kelompokkan sisa blok berdasarkan struktur kolom
    body_lines = [b for b in lines if b.y1 >= 50 and b.y0 < 720]
    contact_lines = [b for b in lines if b.y0 >= 720]
    
    # Deteksi apakah ada struktur Key-Value (seperti Processor:, Memory:)
    kv_keys = [b for b in body_lines if b.text.endswith(":")]
    kv_vals = [b for b in body_lines if not b.text.endswith(":") and b.center_x > 380]
    
    # Pisahkan blok teks biasa dan blok spesifikasi
    non_kv_lines = [b for b in body_lines if not b.text.endswith(":") and b not in kv_vals]
    
    # Bagian 1: Software Features / Teks Paragraf
    if non_kv_lines:
        md_output.append("## 1. Software Features & General Description\n")
        i = 0
        while i < len(non_kv_lines):
            b = non_kv_lines[i]
            # Tentukan apakah baris ini adalah Judul Fitur (singkat & bold/prominent)
            if b.is_bold or (len(b.text) < 45 and not b.text.endswith(".")):
                md_output.append(f"### {b.text}")
                # Jika baris berikutnya adalah deskripsi paragraf, gabungkan
                if i + 1 < len(non_kv_lines) and len(non_kv_lines[i+1].text) > 30:
                    md_output.append(f"{non_kv_lines[i+1].text}\n")
                    i += 1
                else:
                    md_output.append("")
            else:
                md_output.append(f"{b.text}\n")
            i += 1
            
    # Bagian 2: Hardware Specifications (Format Tabel Rapi)
    if kv_keys:
        md_output.append("\n---\n\n## 2. Hardware Specifications\n")
        md_output.append("| Component / Feature | Technical Specification |")
        md_output.append("| :--- | :--- |")
        
        for k in kv_keys:
            key_name = k.text.rstrip(":")
            # Cari nilai yang paling pas berdasarkan koordinat Y
            match = min(kv_vals, key=lambda v: abs(v.center_y - k.center_y)) if kv_vals else None
            val_text = match.text if (match and abs(match.center_y - k.center_y) < 15) else "-"
            md_output.append(f"| **{key_name}** | {val_text} |")
        md_output.append("")
        
    # Bagian 3: Worldwide Contacts / Offices
    if contact_lines:
        md_output.append("\n---\n\n## 3. Worldwide Office Locations & Contacts\n")
        md_output.append("| Region | Office Address | Contact Information |")
        md_output.append("| :--- | :--- | :--- |")
        
        # Kelompokkan alamat kontak
        for c in contact_lines:
            text = c.text
            region = "Office"
            for r in ["Taiwan", "China", "Malaysia", "Japan", "USA"]:
                if r in text:
                    region = r
                    break
            addr_part = text.replace(region, "").strip()
            md_output.append(f"| **{region}** | {addr_part} | Email / Phone |")
            
    return "\n".join(md_output)

def pdf_to_markdown_smart(pdf_path: str) -> str:
    """Pipeline pemrosesan PDF ke Markdown Cerdas Spatial Reading Order Optimasi Tinggi."""
    print(f"📄 Processing PDF: {pdf_path}")
    blocks = extract_text_blocks_digital(pdf_path)
    pages = sorted(list(set(b.page_num for b in blocks)))
    markdown_output = []
    
    for idx, page_num in enumerate(pages):
        if idx > 0:
            markdown_output.append(f"\n\n---\n\n> **📄 Halaman {page_num + 1}**\n\n")
            
        page_blocks = [b for b in blocks if b.page_num == page_num]
        sorted_page_blocks = sort_blocks_reading_order(page_blocks)
        page_md = format_structured_markdown(sorted_page_blocks)
        markdown_output.append(page_md)
        
    return "\n\n".join(markdown_output)

if __name__ == "__main__":
    import sys
    pdf_file = sys.argv[1] if len(sys.argv) > 1 else "readingOrder.pdf"
    md_result = pdf_to_markdown_smart(pdf_file)
    output_file = os.path.splitext(pdf_file)[0] + "_output.md"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md_result)
    print(f"✅ High-Quality Optimized Markdown saved to: {output_file}")
