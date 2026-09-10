"""
Open ADE — Layout-Aware Parser using PP-Structure

Menggunakan PaddlePaddle PP-Structure untuk mendeteksi region layout pada halaman dokumen:
- text: Paragraf teks biasa
- title: Judul/heading
- table: Tabel → diproses oleh SLANet → output HTML → konversi ke Markdown table
- figure: Gambar/logo/grafik → annotated sebagai [IMAGE]
- header/footer: Header/footer halaman → di-skip

Menggantikan flat OCR approach dengan region-aware assembly.
"""
import re
import time
import fitz
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
    region_type: str       # text, title, table, figure, header, footer
    bbox: List[float]      # [x1, y1, x2, y2] in pixels
    text: str = ""         # Teks hasil OCR (untuk text/title regions)
    html: str = ""         # HTML table (untuk table regions)
    confidence: float = 0.0
    page: int = 1
    
    # Normalized bbox (0-1 range, relative to page dimensions)
    norm_xmin: float = 0.0
    norm_ymin: float = 0.0
    norm_xmax: float = 0.0
    norm_ymax: float = 0.0
    
    @property
    def y_center(self) -> float:
        """Y center for reading order sorting."""
        return (self.bbox[1] + self.bbox[3]) / 2
    
    @property
    def x_center(self) -> float:
        """X center for column detection."""
        return (self.bbox[0] + self.bbox[2]) / 2


class LayoutParser:
    """
    Parser berbasis PP-Structure yang mendeteksi layout region,
    merekonstruksi tabel, dan menyusun markdown terstruktur.
    """
    _engine = None
    
    @classmethod
    def get_engine(cls):
        """Lazy-load PPStructure engine (singleton)."""
        if cls._engine is None:
            from paddleocr import PPStructure
            cls._engine = PPStructure(
                show_log=False,
                recovery=True,
                lang="en",
                use_gpu=False,
            )
            logger.info("🏗️  PP-Structure engine initialized")
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
    
    def _extract_text_from_res(self, res_list: List[Dict]) -> str:
        """
        Ekstrak teks dari result list PP-Structure region.
        Setiap item di res_list biasanya dict dengan 'text' dan 'confidence'.
        """
        if not res_list:
            return ""
        
        texts = []
        for item in res_list:
            if isinstance(item, dict):
                text = item.get("text", "")
                if text:
                    texts.append(text.strip())
            elif isinstance(item, (list, tuple)):
                # Nested format: [[bbox, (text, conf)], ...]
                for sub in item:
                    if isinstance(sub, (list, tuple)) and len(sub) >= 2:
                        if isinstance(sub[1], (list, tuple)):
                            texts.append(str(sub[1][0]).strip())
                        elif isinstance(sub[1], str):
                            texts.append(sub[1].strip())
        
        return " ".join(texts)

    def _is_footer_region(self, region: LayoutRegion, img_h: int) -> bool:
        """Deteksi apakah region ini adalah footer (bagian bawah halaman)."""
        # Region di bawah 90% halaman
        if region.bbox[1] / img_h > 0.88:
            return True
        # Teks mengandung URL, alamat kampus, dll
        if region.text and any(kw in region.text.lower() for kw in [
            "www.", ".ac.id", ".co.id", "campus", "kampus"
        ]):
            return True
        return False

    def _is_header_region(self, region: LayoutRegion, img_h: int) -> bool:
        """Deteksi apakah region ini adalah header (logo/branding di atas)."""
        # Region di atas 5% halaman dan kecil (logo)
        if region.bbox[3] / img_h < 0.06:
            return True
        return False

    def parse_page_layout(
        self, img_bytes: bytes, img_w: int, img_h: int, page_num: int
    ) -> List[LayoutRegion]:
        """
        Mendeteksi layout region pada satu halaman menggunakan PP-Structure.
        
        Args:
            img_bytes: PNG bytes dari halaman PDF
            img_w: Lebar gambar (pixels)
            img_h: Tinggi gambar (pixels)
            page_num: Nomor halaman (1-indexed)
            
        Returns:
            List[LayoutRegion] yang sudah di-sort berdasarkan reading order
        """
        engine = self.get_engine()
        
        # Convert bytes to numpy array for PPStructure
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
                # Table region: extract HTML from 'res' → 'html'
                res = block.get("res", {})
                if isinstance(res, dict):
                    region.html = res.get("html", "")
                elif isinstance(res, str):
                    region.html = res
                # Juga simpan teks mentah untuk grounding
                region.text = "[TABLE]"
                
            elif region_type == "figure":
                region.text = "[IMAGE]"
                
            else:
                # Text/title region: extract OCR text
                res = block.get("res", [])
                if isinstance(res, list):
                    text_parts = []
                    for line in res:
                        if isinstance(line, dict):
                            text_parts.append(line.get("text", ""))
                        elif isinstance(line, (list, tuple)) and len(line) >= 2:
                            if isinstance(line[1], (list, tuple)):
                                text_parts.append(str(line[1][0]))
                            elif isinstance(line[1], str):
                                text_parts.append(line[1])
                    region.text = " ".join(t.strip() for t in text_parts if t.strip())
                elif isinstance(res, str):
                    region.text = res
            
            # Filter: skip low confidence regions
            if region.confidence < config.LAYOUT_SCORE_THRESHOLD:
                logger.debug(f"  Skip low-confidence region: {region_type} ({region.confidence:.2f})")
                continue
            
            # Filter: skip footer/header noise
            if self._is_footer_region(region, img_h):
                logger.debug(f"  Skip footer region: {region.text[:50]}...")
                continue
            if self._is_header_region(region, img_h) and region_type != "title":
                logger.debug(f"  Skip header region: {region.text[:50]}...")
                continue
            
            regions.append(region)
        
        # Sort by reading order: top-to-bottom, then left-to-right
        regions.sort(key=lambda r: (r.bbox[1], r.bbox[0]))
        
        logger.debug(
            f"  Page {page_num}: {len(regions)} layout regions detected "
            f"({sum(1 for r in regions if r.region_type == 'table')} tables, "
            f"{sum(1 for r in regions if r.region_type == 'figure')} figures)"
        )
        
        return regions
    
    def regions_to_markdown(self, regions: List[LayoutRegion]) -> str:
        """
        Menyusun markdown dari list of layout regions.
        Setiap tipe region diformat berbeda.
        
        Args:
            regions: List[LayoutRegion] yang sudah sorted
            
        Returns:
            Markdown string terstruktur
        """
        md_parts = []
        
        for region in regions:
            text = region.text.strip() if region.text else ""
            
            if region.region_type == "title":
                if text:
                    # Clean OCR typos pada title
                    text = clean_ocr_line(text)
                    md_parts.append(f"\n## {text}\n")
                    
            elif region.region_type == "table":
                if region.html:
                    md_table = html_table_to_markdown(region.html)
                    if md_table:
                        md_parts.append(f"\n{md_table}\n")
                    else:
                        # Fallback: raw text jika table conversion gagal
                        if text and text != "[TABLE]":
                            md_parts.append(text)
                            
            elif region.region_type == "figure":
                md_parts.append(f"\n[IMAGE: detected at page {region.page}]\n")
                
            elif region.region_type in ("text", "reference"):
                if text:
                    text = clean_ocr_line(text)
                    
                    # Detect key-value pairs
                    if ":" in text and len(text) < 120:
                        md_parts.append(text)
                    else:
                        md_parts.append(text)
                        
            elif region.region_type == "figure_caption":
                if text:
                    md_parts.append(f"*{text}*")
            
            # Skip header/footer types (already filtered)
        
        raw_md = "\n\n".join(md_parts)
        
        # Apply full text cleaning
        cleaned_md = clean_ocr_text(raw_md)
        
        return cleaned_md
