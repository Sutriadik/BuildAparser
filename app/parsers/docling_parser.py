import time
from typing import Dict, Any, List
from docling.document_converter import DocumentConverter
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
    def __init__(self):
        self.converter = DocumentConverter()

    def parse(self, pdf_path: str) -> LandingAIParsedResponse:
        """
        Memproses PDF Digital Native menggunakan IBM Docling.
        Menghasilkan Markdown dan LandingAI-compatible Structure JSON.
        """
        start_time = time.time()
        conv_res = self.converter.convert(pdf_path)
        doc = conv_res.document
        
        raw_markdown = doc.export_to_markdown()
        
        # Apply OCR text cleaning (Docling juga bisa menghasilkan teks kotor)
        markdown_text = clean_ocr_text(raw_markdown)
        
        duration_ms = int((time.time() - start_time) * 1000)
        
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
                if b:
                    bbox = BoundingBox(
                        xmin=round(max(0.0, min(1.0, getattr(b, "l", 0.1) / max(getattr(b, "width", 600), 1))), 5),
                        ymin=round(max(0.0, min(1.0, getattr(b, "t", 0.1) / max(getattr(b, "height", 800), 1))), 5),
                        xmax=round(max(0.0, min(1.0, getattr(b, "r", 0.9) / max(getattr(b, "width", 600), 1))), 5),
                        ymax=round(max(0.0, min(1.0, getattr(b, "b", 0.2) / max(getattr(b, "height", 800), 1))), 5)
                    )
            
            start_pos = markdown_text.find(text_val, max(0, char_pointer - 50))
            if start_pos == -1:
                start_pos = char_pointer
            end_pos = start_pos + len(text_val)
            char_pointer = end_pos
            
            label = getattr(item, "label", "text").lower()
            elem_type = "paragraph"
            if "header" in label or "title" in label or text_val.startswith("#"):
                elem_type = "header"
            elif "list" in label:
                elem_type = "list_item"
            elif ":" in text_val and len(text_val) < 80:
                elem_type = "key_value"
                
            st_item = StructureItem(
                type=elem_type,
                id=f"{elem_type}-{idx}",
                text=text_val,
                grounding=Grounding(
                    page=page_no,
                    range=TextRange(start=start_pos, end=end_pos),
                    box=bbox,
                    confidence=0.96
                ),
                atomic_grounding=[
                    AtomicGrounding(
                        page=page_no,
                        range=TextRange(start=start_pos, end=end_pos),
                        box=bbox,
                        text=text_val,
                        confidence=0.96
                    )
                ],
                confidence=0.96
            )
            page_children.setdefault(page_no, []).append(st_item)
            
        # Iterate over tables
        for t_idx, table in enumerate(doc.tables):
            prov = getattr(table, "prov", [])
            page_no = 1
            bbox = BoundingBox(xmin=0.1, ymin=0.3, xmax=0.9, ymax=0.7)
            if prov and len(prov) > 0:
                p = prov[0]
                page_no = getattr(p, "page_no", 1)
                b = getattr(p, "bbox", None)
                if b:
                    bbox = BoundingBox(
                        xmin=round(max(0.0, min(1.0, getattr(b, "l", 0.1) / 600)), 5),
                        ymin=round(max(0.0, min(1.0, getattr(b, "t", 0.3) / 800)), 5),
                        xmax=round(max(0.0, min(1.0, getattr(b, "r", 0.9) / 600)), 5),
                        ymax=round(max(0.0, min(1.0, getattr(b, "b", 0.7) / 800)), 5)
                    )
            
            table_item = StructureItem(
                type="table",
                id=f"table-{t_idx}",
                grounding=Grounding(
                    page=page_no,
                    range=TextRange(start=0, end=len(markdown_text)),
                    box=bbox,
                    confidence=0.98
                ),
                confidence=0.98
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
        
        logger.info(f"✅ Docling selesai: {len(pages_structure)} halaman, {len(markdown_text)} karakter, {duration_ms}ms")
            
        return LandingAIParsedResponse(
            markdown=markdown_text,
            metadata=ParseMetadata(
                job_id=f"parse-docling-{int(time.time())}",
                page_count=len(pages_structure) if pages_structure else 1,
                output_markdown_chars=len(markdown_text),
                duration_ms=duration_ms,
                is_scanned=False,
                parser_engine="ibm-docling"
            ),
            structure=DocumentStructure(children=pages_structure)
        )
