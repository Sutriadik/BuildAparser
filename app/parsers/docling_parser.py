"""
Open ADE — Docling Parser (v2: Universal Mode)

Menggunakan IBM Docling untuk parsing PDF dengan dukungan:
- PDF Digital Native: text extraction + table structure
- PDF Scanned: OCR + table structure (do_ocr=True)
- Bounding box normalization yang akurat dari page dimensions
"""
import time
from typing import Dict, Any, List
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from app.logger import logger
from app.parsers.text_cleaner import clean_ocr_text
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


class DoclingParser:
    def __init__(self, do_ocr: bool = True):
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = do_ocr
        pipeline_options.do_table_structure = True
        
        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        self.do_ocr = do_ocr
        logger.info(f"📄 DoclingParser initialized (OCR: {do_ocr}, Table Structure: True)")

    def _get_page_dimensions(self, doc, page_no: int) -> tuple:
        """Get page width and height from Docling document for accurate bbox normalization."""
        try:
            pages = getattr(doc, 'pages', {})
            if pages and page_no in pages:
                page = pages[page_no]
                size = getattr(page, 'size', None)
                if size:
                    w = getattr(size, 'width', 612)
                    h = getattr(size, 'height', 792)
                    return (max(w, 1), max(h, 1))
        except Exception:
            pass
        return (612, 792)  # Default US Letter

    def _normalize_bbox(self, bbox_obj, page_w: float, page_h: float) -> BoundingBox:
        """Normalize bbox coordinates to 0-1 range using actual page dimensions."""
        try:
            l = getattr(bbox_obj, 'l', 0.0)
            t = getattr(bbox_obj, 't', 0.0)
            r = getattr(bbox_obj, 'r', page_w)
            b = getattr(bbox_obj, 'b', page_h)
            
            return BoundingBox(
                xmin=round(max(0.0, min(1.0, l / page_w)), 5),
                ymin=round(max(0.0, min(1.0, t / page_h)), 5),
                xmax=round(max(0.0, min(1.0, r / page_w)), 5),
                ymax=round(max(0.0, min(1.0, b / page_h)), 5)
            )
        except Exception:
            return BoundingBox(xmin=0.1, ymin=0.1, xmax=0.9, ymax=0.2)

    def parse(self, pdf_path: str) -> LandingAIParsedResponse:
        """
        Memproses PDF (Digital Native atau Scanned) menggunakan IBM Docling.
        Menghasilkan Markdown terstruktur dan LandingAI-compatible Structure JSON.
        """
        start_time = time.time()
        conv_res = self.converter.convert(pdf_path)
        doc = conv_res.document
        
        raw_markdown = doc.export_to_markdown()
        
        # Apply OCR text cleaning
        markdown_text = clean_ocr_text(raw_markdown)
        
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Cache page dimensions
        page_dims: Dict[int, tuple] = {}
        
        page_children: Dict[int, List[StructureItem]] = {}
        char_pointer = 0
        
        # Iterate over text items
        for idx, item in enumerate(doc.texts):
            text_val = getattr(item, "text", "").strip()
            if not text_val:
                continue
                
            prov = getattr(item, "prov", [])
            page_no = 1
            bbox = BoundingBox(xmin=0.1, ymin=0.1, xmax=0.9, ymax=0.2)
            
            if prov and len(prov) > 0:
                p = prov[0]
                page_no = getattr(p, "page_no", 1)
                b = getattr(p, "bbox", None)
                
                # Get cached page dimensions
                if page_no not in page_dims:
                    page_dims[page_no] = self._get_page_dimensions(doc, page_no)
                page_w, page_h = page_dims[page_no]
                
                if b:
                    bbox = self._normalize_bbox(b, page_w, page_h)
            
            start_pos = markdown_text.find(text_val, max(0, char_pointer - 50))
            if start_pos == -1:
                start_pos = char_pointer
            end_pos = start_pos + len(text_val)
            char_pointer = end_pos
            
            label = getattr(item, "label", "text").lower()
            elem_type = "paragraph"
            if "header" in label or "title" in label or text_val.startswith("#"):
                elem_type = "header"
            elif "table" in label:
                elem_type = "table"
            elif "list" in label:
                elem_type = "list_item"
            elif ":" in text_val and len(text_val) < 100:
                elem_type = "key_value"
                
            # Compute confidence based on source
            confidence = 0.97 if not self.do_ocr else 0.90
                
            st_item = StructureItem(
                type=elem_type,
                id=f"{elem_type}-{idx}",
                text=text_val,
                grounding=Grounding(
                    page=page_no,
                    range=TextRange(start=start_pos, end=end_pos),
                    box=bbox,
                    confidence=confidence
                ),
                atomic_grounding=[
                    AtomicGrounding(
                        page=page_no,
                        range=TextRange(start=start_pos, end=end_pos),
                        box=bbox,
                        text=text_val,
                        confidence=confidence
                    )
                ],
                confidence=confidence
            )
            page_children.setdefault(page_no, []).append(st_item)
            
        # Iterate over tables — extract table text for grounding
        for t_idx, table in enumerate(doc.tables):
            prov = getattr(table, "prov", [])
            page_no = 1
            bbox = BoundingBox(xmin=0.1, ymin=0.3, xmax=0.9, ymax=0.7)
            
            if prov and len(prov) > 0:
                p = prov[0]
                page_no = getattr(p, "page_no", 1)
                b = getattr(p, "bbox", None)
                
                if page_no not in page_dims:
                    page_dims[page_no] = self._get_page_dimensions(doc, page_no)
                page_w, page_h = page_dims[page_no]
                
                if b:
                    bbox = self._normalize_bbox(b, page_w, page_h)
            
            # Extract table text for grounding linker
            table_text = ""
            try:
                table_df = table.export_to_dataframe()
                table_text = " | ".join(str(c) for c in table_df.columns)
                for _, row in table_df.iterrows():
                    table_text += " | " + " | ".join(str(v) for v in row.values)
            except Exception:
                table_text = "[TABLE]"
            
            table_item = StructureItem(
                type="table",
                id=f"table-{t_idx}",
                text=table_text[:500],  # Cap for grounding
                grounding=Grounding(
                    page=page_no,
                    range=TextRange(start=0, end=len(markdown_text)),
                    box=bbox,
                    confidence=0.95
                ),
                atomic_grounding=[
                    AtomicGrounding(
                        page=page_no,
                        range=TextRange(start=0, end=len(markdown_text)),
                        box=bbox,
                        text=table_text[:200],
                        confidence=0.95
                    )
                ],
                confidence=0.95
            )
            page_children.setdefault(page_no, []).append(table_item)
            
        pages_structure = []
        for p_no in sorted(page_children.keys()):
            pages_structure.append(
                StructureItem(
                    type="page",
                    id=f"page-{p_no}",
                    grounding=Grounding(
                        page=p_no,
                        range=TextRange(start=0, end=len(markdown_text)),
                        box=BoundingBox(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0),
                        confidence=1.0
                    ),
                    children=page_children[p_no],
                    confidence=1.0
                )
            )
        
        engine_name = "ibm-docling" + ("+ocr" if self.do_ocr else "")
        logger.info(f"✅ Docling selesai [{engine_name}]: {len(pages_structure)} halaman, {len(markdown_text)} karakter, {duration_ms}ms")
            
        return LandingAIParsedResponse(
            markdown=markdown_text,
            metadata=ParseMetadata(
                job_id=f"parse-docling-{int(time.time())}",
                page_count=len(pages_structure) if pages_structure else 1,
                output_markdown_chars=len(markdown_text),
                duration_ms=duration_ms,
                is_scanned=self.do_ocr,
                parser_engine=engine_name
            ),
            structure=DocumentStructure(children=pages_structure)
        )
