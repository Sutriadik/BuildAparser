import time
import fitz
import re
from typing import List, Tuple
from dataclasses import dataclass
from paddleocr import PaddleOCR
from app.config import config
from app.logger import logger
from app.parsers.text_cleaner import clean_ocr_text, clean_ocr_line
from app.schemas.common import (
    LandingAIParsedResponse,
    ParseMetadata,
    DocumentStructure,
    StructureItem,
    Grounding,
    BoundingBox,
    TextRange,
    AtomicGrounding
)

@dataclass
class OCRBox:
    text: str
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    center_y: float
    confidence: float

def cluster_and_merge_lines(boxes: List[OCRBox], y_tolerance: float = None, x_gap_tolerance: float = None) -> List[OCRBox]:
    """
    Mengelompokkan dan menggabungkan potongan teks OCR yang berada pada baris horizontal yang sama (Spatial Reading Order).
    Menghasilkan kalimat utuh (misal: 'Nama :' + 'Mohamad Veni' -> 'Nama : Mohamad Veni').
    """
    if y_tolerance is None:
        y_tolerance = config.OCR_Y_TOLERANCE
    if x_gap_tolerance is None:
        x_gap_tolerance = config.OCR_X_GAP_TOLERANCE

    if not boxes:
        return []
        
    # Sort boxes primarily by ymin, then xmin
    sorted_boxes = sorted(boxes, key=lambda b: (round(b.ymin / y_tolerance) * y_tolerance, b.xmin))
    merged_lines: List[OCRBox] = []
    current_line: List[OCRBox] = [sorted_boxes[0]]
    
    for box in sorted_boxes[1:]:
        last = current_line[-1]
        same_row = abs(box.center_y - last.center_y) <= y_tolerance or (box.ymin >= last.ymin - y_tolerance and box.ymax <= last.ymax + y_tolerance)
        horizontal_gap = box.xmin - last.xmax
        
        if same_row and horizontal_gap <= x_gap_tolerance:
            current_line.append(box)
        else:
            # Selesai 1 baris, gabungkan dari kiri ke kanan
            current_line.sort(key=lambda b: b.xmin)
            merged_text = " ".join(b.text for b in current_line)
            # Bersihkan spasi berlebih
            merged_text = re.sub(r'\s*:\s*', ' : ', merged_text)
            merged_text = re.sub(r'\s+', ' ', merged_text).strip()
            
            # Bersihkan per-line OCR typo
            merged_text = clean_ocr_line(merged_text)
            
            merged_lines.append(OCRBox(
                text=merged_text,
                xmin=min(b.xmin for b in current_line),
                ymin=min(b.ymin for b in current_line),
                xmax=max(b.xmax for b in current_line),
                ymax=max(b.ymax for b in current_line),
                center_y=sum(b.center_y for b in current_line) / len(current_line),
                confidence=sum(b.confidence for b in current_line) / len(current_line)
            ))
            current_line = [box]
            
    if current_line:
        current_line.sort(key=lambda b: b.xmin)
        merged_text = re.sub(r'\s*:\s*', ' : ', " ".join(b.text for b in current_line))
        merged_text = re.sub(r'\s+', ' ', merged_text).strip()
        
        # Bersihkan per-line OCR typo
        merged_text = clean_ocr_line(merged_text)
        
        merged_lines.append(OCRBox(
            text=merged_text,
            xmin=min(b.xmin for b in current_line),
            ymin=min(b.ymin for b in current_line),
            xmax=max(b.xmax for b in current_line),
            ymax=max(b.ymax for b in current_line),
            center_y=sum(b.center_y for b in current_line) / len(current_line),
            confidence=sum(b.confidence for b in current_line) / len(current_line)
        ))
        
    return merged_lines

class PaddleOCRParser:
    _ocr_engine = None

    @classmethod
    def get_engine(cls):
        if cls._ocr_engine is None:
            cls._ocr_engine = PaddleOCR(use_angle_cls=True, lang="en")
        return cls._ocr_engine

    def _classify_element_type(self, text_content: str) -> str:
        """
        Deteksi tipe elemen layout berdasarkan konten teks.
        Lebih presisi daripada hardcoded if-elif chain.
        """
        text_upper = text_content.upper().strip()
        
        # Header detection — judul dokumen utama
        header_keywords = [
            "SURAT PERINTAH KERJA", "SURAT PENAWARAN", "BERITA ACARA", 
            "PERJANJIAN KERJASAMA", "LAMPIRAN", "KONTRAK PENGADAAN"
        ]
        if any(h in text_upper for h in header_keywords):
            return "header"
        
        # Header detection — nomor pasal (misal: "1. LINGKUP PEKERJAAN")
        if re.match(r'^\d+[\.\)]\s*[A-Z\s]{3,}', text_content):
            return "header"
        
        # Key-value pairs (misal: "Nama : Mohamad Veni")
        if ":" in text_content and len(text_content) < 120:
            return "key_value"
        
        # Table elements
        table_keywords = ["uraian barang", "harga satuan", "jumlah harga", "sub total", "ppn 11%", "ppn11%"]
        if "|" in text_content or any(col in text_content.lower() for col in table_keywords):
            return "table"
        
        # Signature zone — hanya jika teks pendek DAN mengandung indikator tanda tangan
        signature_keywords = ["METERAI TEMPEL", "METERAI", "TANDA TANGAN"]
        if len(text_content) < 60 and any(s in text_upper for s in signature_keywords):
            return "signature"
        
        return "paragraph"

    def _is_footer_noise(self, text_content: str) -> bool:
        """
        Deteksi apakah teks ini adalah footer noise (alamat kampus, URL, etc.)
        yang tidak relevan untuk extraction.
        """
        noise_indicators = [
            "www.", ".ac.id", ".co.id", ".com",
            "Main Campus", "Jakarta Campus", "Surabaya Campus", "Purwokerto Campus"
        ]
        # Teks panjang yang mengandung banyak alamat kampus → noise
        if len(text_content) > 200 and any(n in text_content for n in noise_indicators):
            return True
        # URL standalone
        if re.match(r'^(https?://)?www\.\S+$', text_content.strip()):
            return True
        return False

    def parse(self, pdf_path: str, max_pages: int = None) -> LandingAIParsedResponse:
        """
        Memproses Dokumen Scan / Tanda Tangan Menggunakan PaddleOCR dengan Spatial Layout Assembly.
        Menghasilkan Visual Grounding Bounding Box dan Markdown Bersih berkualitas tinggi.
        """
        start_time = time.time()
        ocr = self.get_engine()
        doc = fitz.open(pdf_path)
        
        pages_structure = []
        full_markdown_parts = []
        char_offset = 0
        
        total_pages = len(doc)
        pages_to_process = min(total_pages, max_pages) if max_pages else total_pages
        
        logger.info(f"🔍 PaddleOCR: Memproses {pages_to_process}/{total_pages} halaman")
        
        for page_idx in range(pages_to_process):
            page = doc[page_idx]
            page_num = page_idx + 1
            
            # Render page to DPI image (configurable)
            pix = page.get_pixmap(dpi=config.DEFAULT_DPI)
            img_bytes = pix.tobytes("png")
            img_w = pix.width
            img_h = pix.height
            
            ocr_results = ocr.ocr(img_bytes, cls=True)
            raw_boxes: List[OCRBox] = []
            
            if ocr_results and ocr_results[0]:
                for line in ocr_results[0]:
                    box_coords = line[0]
                    text_content = line[1][0].strip()
                    confidence = round(float(line[1][1]), 3)
                    
                    if not text_content:
                        continue
                        
                    xs = [pt[0] for pt in box_coords]
                    ys = [pt[1] for pt in box_coords]
                    xmin = round(max(0.0, min(1.0, min(xs) / img_w)), 5)
                    ymin = round(max(0.0, min(1.0, min(ys) / img_h)), 5)
                    xmax = round(max(0.0, min(1.0, max(xs) / img_w)), 5)
                    ymax = round(max(0.0, min(1.0, max(ys) / img_h)), 5)
                    
                    raw_boxes.append(OCRBox(
                        text=text_content,
                        xmin=xmin,
                        ymin=ymin,
                        xmax=xmax,
                        ymax=ymax,
                        center_y=(ymin + ymax) / 2,
                        confidence=confidence
                    ))
            
            logger.debug(f"  Halaman {page_num}: {len(raw_boxes)} raw boxes terdeteksi")
                    
            # Cluster and merge horizontal fragments into coherent reading lines
            merged_lines = cluster_and_merge_lines(raw_boxes)
            
            logger.debug(f"  Halaman {page_num}: {len(merged_lines)} merged lines setelah clustering")
            
            page_items: List[StructureItem] = []
            page_md_lines = []
            
            for item_idx, line in enumerate(merged_lines):
                text_content = line.text
                
                # Skip footer noise
                if self._is_footer_noise(text_content):
                    logger.debug(f"  Skipped footer noise: {text_content[:50]}...")
                    continue
                
                start_char = char_offset + sum(len(l) + 1 for l in page_md_lines)
                end_char = start_char + len(text_content)
                
                # Deteksi tipe elemen layout (menggunakan method yang lebih presisi)
                elem_type = self._classify_element_type(text_content)
                
                # Format markdown berdasarkan tipe
                if elem_type == "header":
                    page_md_lines.append(f"\n## {text_content}")
                elif elem_type == "signature":
                    page_md_lines.append(f"[SIGNED] {text_content}")
                elif elem_type == "key_value":
                    page_md_lines.append(text_content)
                elif elem_type == "table":
                    page_md_lines.append(text_content)
                else:
                    page_md_lines.append(text_content)
                    
                bbox = BoundingBox(xmin=line.xmin, ymin=line.ymin, xmax=line.xmax, ymax=line.ymax)
                st_item = StructureItem(
                    type=elem_type,
                    id=f"{elem_type}-p{page_num}-{item_idx}",
                    text=text_content,
                    grounding=Grounding(
                        page=page_num,
                        range=TextRange(start=start_char, end=end_char),
                        box=bbox,
                        confidence=round(line.confidence, 3)
                    ),
                    atomic_grounding=[
                        AtomicGrounding(
                            page=page_num,
                            range=TextRange(start=start_char, end=end_char),
                            box=bbox,
                            text=text_content,
                            confidence=round(line.confidence, 3)
                        )
                    ],
                    confidence=round(line.confidence, 3)
                )
                page_items.append(st_item)
                
            page_md = "\n\n".join(page_md_lines)
            full_markdown_parts.append(page_md)
            char_offset += len(page_md) + 20
            
            pages_structure.append(
                StructureItem(
                    type="page",
                    id=f"page-{page_num}",
                    grounding=Grounding(
                        page=page_num,
                        range=TextRange(start=0, end=len(page_md)),
                        box=BoundingBox(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0),
                        confidence=1.0
                    ),
                    children=page_items,
                    confidence=1.0
                )
            )
            
        doc.close()
        raw_markdown = "\n\n<!-- PAGE BREAK -->\n\n".join(full_markdown_parts)
        
        # Apply full OCR text cleaning pada final markdown
        cleaned_markdown = clean_ocr_text(raw_markdown)
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ PaddleOCR selesai: {len(pages_structure)} halaman, {len(cleaned_markdown)} karakter, {duration_ms}ms")
        
        return LandingAIParsedResponse(
            markdown=cleaned_markdown,
            metadata=ParseMetadata(
                job_id=f"parse-paddle-{int(time.time())}",
                page_count=len(pages_structure),
                output_markdown_chars=len(cleaned_markdown),
                duration_ms=duration_ms,
                is_scanned=True,
                parser_engine="paddleocr+spatial_cluster"
            ),
            structure=DocumentStructure(children=pages_structure)
        )
