"""
Open ADE — Layout-Aware Parser using PP-Structure (v2: Robust Multi-type Layout)

Menggunakan PaddlePaddle PP-Structure untuk mendeteksi region layout pada halaman dokumen:
- text / paragraph: Paragraf teks biasa
- title / section_header: Judul/heading → ## Heading
- list / list_item: Daftar bernomor / bullet points → 1. Item / - Item
- table: Tabel → diproses oleh SLANet → output HTML → konversi ke Markdown table
- figure / image: Jika ada baris teks/tabel OCR di dalamnya, diekstrak secara terstruktur.
- header / footer: Mempertahankan informasi nomor surat, tanggal, dan penandatangan.
"""
import re
import time
import numpy as np
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from PIL import Image
import io

from app.config import config
from app.logger import logger
from app.parsers.text_cleaner import clean_ocr_text, clean_ocr_line
from app.parsers.table_converter import html_table_to_markdown


@dataclass
class LayoutRegion:
    """Satu region layout yang terdeteksi oleh PP-Structure."""
    region_type: str       # text, title, table, figure, header, footer, list
    bbox: List[float]      # [x1, y1, x2, y2] in pixels
    text: str = ""         # Teks hasil OCR (untuk text/title/list regions)
    html: str = ""         # HTML table (untuk table regions)
    confidence: float = 0.0
    page: int = 1
    
    # Normalized bbox (0-1 range, relative to page dimensions)
    norm_xmin: float = 0.0
    norm_ymin: float = 0.0
    norm_xmax: float = 0.0
    norm_ymax: float = 0.0



class LayoutParser:
    """
    Parser berbasis PP-Structure yang mendeteksi layout region,
    merekonstruksi tabel, dan menyusun markdown terstruktur.
    """
    _engine = None
    
    @classmethod
    def get_engine(cls):
        """Lazy-load PPStructure engine (singleton). Layout model PPStructure wajib en/ch."""
        if cls._engine is None:
            from paddleocr import PPStructure
            cls._engine = PPStructure(
                show_log=False,
                recovery=True,
                lang="en",  # Model layout PP-Structure hanya mendukung 'en' atau 'ch'
                use_gpu=False,
            )
            logger.info("🏗️  PP-Structure layout engine initialized (lang=en)")
        return cls._engine
    
    def _normalize_bbox(
        self, bbox: List[float], img_w: int, img_h: int
    ) -> Tuple[float, float, float, float]:
        """Normalize pixel bbox ke 0-1 range."""
        x1, y1, x2, y2 = bbox
        return (
            round(max(0.0, min(1.0, x1 / img_w)), 5),
            round(max(0.0, min(1.0, y1 / img_h)), 5),
            round(max(0.0, min(1.0, x2 / img_w)), 5),
            round(max(0.0, min(1.0, y2 / img_h)), 5),
        )
    
    def _extract_boxes_and_text_from_res(
        self, res_list: Any
    ) -> str:
        """
        Ekstrak teks terstruktur dari result list PP-Structure region.
        Menggunakan spatial line clustering jika ada beberapa baris OCR.
        """
        if not res_list:
            return ""
        
        if isinstance(res_list, str):
            return res_list.strip()
            
        from app.parsers.paddle_parser import OCRBox, cluster_and_merge_lines
        
        raw_boxes: List[OCRBox] = []
        if isinstance(res_list, list):
            for item in res_list:
                if isinstance(item, dict):
                    text = item.get("text", "").strip()
                    conf = float(item.get("confidence", 0.9))
                    tr = item.get("text_region")
                    if text and tr:
                        xs = [p[0] for p in tr]
                        ys = [p[1] for p in tr]
                        raw_boxes.append(OCRBox(
                            text=text,
                            xmin=min(xs),
                            ymin=min(ys),
                            xmax=max(xs),
                            ymax=max(ys),
                            center_y=(min(ys) + max(ys)) / 2,
                            confidence=conf,
                        ))
                    elif text:
                        raw_boxes.append(OCRBox(
                            text=text, xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0, center_y=0.0, confidence=conf
                        ))
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    tr = item[0]
                    content = item[1]
                    text = ""
                    conf = 0.9
                    if isinstance(content, (list, tuple)):
                        text = str(content[0]).strip()
                        conf = float(content[1]) if len(content) > 1 else 0.9
                    elif isinstance(content, str):
                        text = content.strip()
                    if text and isinstance(tr, (list, tuple)) and len(tr) >= 4:
                        xs = [p[0] for p in tr]
                        ys = [p[1] for p in tr]
                        raw_boxes.append(OCRBox(
                            text=text,
                            xmin=min(xs),
                            ymin=min(ys),
                            xmax=max(xs),
                            ymax=max(ys),
                            center_y=(min(ys) + max(ys)) / 2,
                            confidence=conf,
                        ))
                    elif text:
                        raw_boxes.append(OCRBox(
                            text=text, xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0, center_y=0.0, confidence=conf
                        ))
        
        if not raw_boxes:
            return ""
            
        from app.parsers.table_converter import format_structured_tabular_boxes
        structured_table = format_structured_tabular_boxes(raw_boxes, y_tol=14)
        if "|" in structured_table:
            return structured_table
            
        merged = cluster_and_merge_lines(raw_boxes, y_tolerance=12, x_gap_tolerance=80)
        lines_text = [b.text for b in merged if b.text.strip()]
        return "\n".join(lines_text)

    def _is_noise_region(self, region: LayoutRegion, img_h: int) -> bool:
        """Deteksi apakah region ini adalah noise murni (nomor halaman tunggal '1/1' atau whitespace kosong)."""
        t = region.text.strip()
        if not t and not region.html:
            return True
        # Nomor halaman tunggal di pojok bawah misal "1 / 2"
        if re.match(r'^\d+\s*/\s*\d+$', t) and region.bbox[1] / img_h > 0.92:
            return True
        return False

    def parse_page_layout(
        self, img_bytes: bytes, img_w: int, img_h: int, page_num: int,
        raw_ocr_boxes: Optional[List[Any]] = None
    ) -> List[LayoutRegion]:
        """
        Mendeteksi layout region pada satu halaman menggunakan PP-Structure,
        dan menggabungkan seluruh OCR boxes yang tidak tercover oleh layout detection
        sehingga tidak ada kop surat, nomor, perihal, lampiran, atau footer yang terlewat.
        
        Args:
            img_bytes: PNG bytes dari halaman PDF
            img_w: Lebar gambar (pixels)
            img_h: Tinggi gambar (pixels)
            page_num: Nomor halaman (1-indexed)
            raw_ocr_boxes: List of OCRBox dari PaddleOCR untuk 100% coverage
            
        Returns:
            List[LayoutRegion] yang sudah di-sort berdasarkan reading order
        """
        engine = self.get_engine()
        
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_array = np.array(img)
        
        # Run PP-Structure
        result = engine(img_array)
        
        regions: List[LayoutRegion] = []
        
        for idx, block in enumerate(result):
            region_type = block.get("type", "text").lower()
            bbox = block.get("bbox", [0, 0, img_w, img_h])
            
            # Normalize bbox
            norm = self._normalize_bbox(bbox, img_w, img_h)
            
            region = LayoutRegion(
                region_type=region_type,
                bbox=bbox,
                confidence=block.get("score", 0.0),
                page=page_num,
                norm_xmin=norm[0],
                norm_ymin=norm[1],
                norm_xmax=norm[2],
                norm_ymax=norm[3],
            )
            
            if region_type == "table":
                res = block.get("res", {})
                if isinstance(res, dict):
                    region.html = res.get("html", "")
                elif isinstance(res, str):
                    region.html = res
                
                # Jika ada teks dari res, ekstrak juga
                if isinstance(res, list):
                    region.text = self._extract_boxes_and_text_from_res(res)
                elif not region.text:
                    region.text = "[TABLE]"
                
            elif region_type in ("figure", "image"):
                # Jangan buang teks OCR yang ada di dalam figure/tabel
                res = block.get("res", [])
                extracted_text = self._extract_boxes_and_text_from_res(res)
                if extracted_text and len(extracted_text) > 10:
                    region.text = extracted_text
                else:
                    region.text = "[IMAGE]"
                
            else:
                # text, title, list, header, footer, reference
                res = block.get("res", [])
                region.text = self._extract_boxes_and_text_from_res(res)
            
            # Filter noise murni
            if self._is_noise_region(region, img_h):
                continue
            
            regions.append(region)
        
        # Recover missed OCR boxes (letterheads, headers, footers, signatures)
        if raw_ocr_boxes:
            from app.parsers.paddle_parser import cluster_and_merge_lines
            
            def is_inside_regions(box: Any, current_regions: List[LayoutRegion], tol: float = 12.0) -> bool:
                b_xmin = box.xmin * img_w if box.xmin <= 1.0 else box.xmin
                b_xmax = box.xmax * img_w if box.xmax <= 1.0 else box.xmax
                b_ymin = box.ymin * img_h if box.ymin <= 1.0 else box.ymin
                b_ymax = box.ymax * img_h if box.ymax <= 1.0 else box.ymax
                box_h = max(b_ymax - b_ymin, 1.0)
                
                for r in current_regions:
                    rx1, ry1, rx2, ry2 = r.bbox
                    # Vertical overlap check
                    overlap_y = max(0.0, min(b_ymax, ry2) - max(b_ymin, ry1))
                    if overlap_y / box_h > 0.5:
                        return True
                return False

            missed = [b for b in raw_ocr_boxes if not is_inside_regions(b, regions)]
            if missed:
                # Group missed boxes into lines
                merged_missed = cluster_and_merge_lines(missed, y_tolerance=14, x_gap_tolerance=80)
                for mb in merged_missed:
                    mb_ymin = mb.ymin * img_h if mb.ymin <= 1.0 else mb.ymin
                    mb_ymax = mb.ymax * img_h if mb.ymax <= 1.0 else mb.ymax
                    mb_xmin = mb.xmin * img_w if mb.xmin <= 1.0 else mb.xmin
                    mb_xmax = mb.xmax * img_w if mb.xmax <= 1.0 else mb.xmax
                    mb_center_y = (mb_ymin + mb_ymax) / 2.0
                    
                    if mb_center_y < img_h * 0.35:
                        r_type = "header"
                    elif mb_center_y > img_h * 0.70:
                        r_type = "footer"
                    else:
                        r_type = "text"
                        
                    norm = self._normalize_bbox([mb_xmin, mb_ymin, mb_xmax, mb_ymax], img_w, img_h)
                    regions.append(LayoutRegion(
                        region_type=r_type,
                        bbox=[mb_xmin, mb_ymin, mb_xmax, mb_ymax],
                        text=mb.text,
                        confidence=mb.confidence,
                        page=page_num,
                        norm_xmin=norm[0],
                        norm_ymin=norm[1],
                        norm_xmax=norm[2],
                        norm_ymax=norm[3],
                    ))
        
        # Sort by reading order: top-to-bottom (Y), then left-to-right (X)
        # Dengan toleransi Y 20px agar item sebaris (misal Label : Nilai) diurutkan kiri->kanan
        regions.sort(key=lambda r: (round(r.bbox[1] / 20.0) * 20.0, r.bbox[0]))
        
        table_count = sum(1 for r in regions if r.region_type == "table")
        figure_count = sum(1 for r in regions if r.region_type in ("figure", "image"))
        logger.debug(
            f"  Page {page_num}: {len(regions)} layout regions detected "
            f"({table_count} tables, {figure_count} figures)"
        )
        
        return regions
    
    def regions_to_markdown(self, regions: List[LayoutRegion]) -> str:
        """
        Menyusun markdown terstruktur dari list of layout regions.
        
        Args:
            regions: List[LayoutRegion] yang sudah sorted
            
        Returns:
            Markdown string terstruktur
        """
        md_parts = []
        
        for region in regions:
            text = region.text.strip() if region.text else ""
            if not text and not region.html:
                continue
                
            r_type = region.region_type.lower()
            
            if r_type in ("title", "section_header"):
                if text:
                    text = clean_ocr_line(text)
                    md_parts.append(f"\n## {text}\n")
                    
            elif r_type == "table":
                if region.html:
                    md_table = html_table_to_markdown(region.html)
                    if md_table:
                        md_parts.append(f"\n{md_table}\n")
                    elif text and text != "[TABLE]":
                        md_parts.append(text)
                elif text and text != "[TABLE]":
                    md_parts.append(text)
                    
            elif r_type in ("figure", "image"):
                if text and text != "[IMAGE]":
                    md_parts.append(text)
                else:
                    md_parts.append(f"\n<!-- image: page {region.page} -->\n")
                    
            elif r_type in ("list", "list_item"):
                if text:
                    lines = text.split("\n")
                    formatted = []
                    for l in lines:
                        cleaned = clean_ocr_line(l)
                        if not cleaned:
                            continue
                        if re.match(r'^\d+[\.\)]\s*', cleaned) or cleaned.startswith("- ") or cleaned.startswith("* "):
                            formatted.append(cleaned)
                        else:
                            formatted.append(f"- {cleaned}")
                    if formatted:
                        md_parts.append("\n".join(formatted))
                        
            elif r_type in ("text", "paragraph", "reference", "header", "footer"):
                if text:
                    lines = [clean_ocr_line(l) for l in text.split("\n") if l.strip()]
                    if lines:
                        md_parts.append("\n".join(lines))
                        
            elif r_type == "figure_caption":
                if text:
                    md_parts.append(f"*{clean_ocr_line(text)}*")
        
        raw_md = "\n\n".join(md_parts)
        cleaned_md = clean_ocr_text(raw_md)
        return cleaned_md
