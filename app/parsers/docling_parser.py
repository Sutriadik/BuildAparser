"""
Open ADE — Docling Parser (v3: routed OCR)

- PDF digital: Docling tanpa OCR (layout + TableFormer saja).
- PDF scan / mixed: Docling dengan OCR.
Converter dibuat lazy per mode, sehingga import docling/torch dan pemuatan model OCR
hanya terjadi saat benar-benar dibutuhkan (startup API tidak lagi memuat semua model).
"""
import time
from typing import Dict, List, Optional

from app.logger import logger
from app.parsers.text_cleaner import clean_ocr_text
from app.schemas.common import (
    AtomicGrounding,
    BoundingBox,
    DocumentStructure,
    Grounding,
    LandingAIParsedResponse,
    ParseMetadata,
    StructureItem,
    TextRange,
)

NATIVE_CONFIDENCE = 0.97
OCR_CONFIDENCE = 0.90


class DoclingParser:
    def __init__(self) -> None:
        self._converters: Dict[bool, object] = {}

    def _get_converter(self, do_ocr: bool):
        if do_ocr not in self._converters:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions()
            options.do_ocr = do_ocr
            options.do_table_structure = True
            self._converters[do_ocr] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
            logger.info(f"📄 Docling converter dimuat (OCR: {do_ocr}, Table Structure: True)")
        return self._converters[do_ocr]

    @staticmethod
    def _get_page_dimensions(doc, page_no: int) -> tuple:
        try:
            page = (getattr(doc, "pages", {}) or {}).get(page_no)
            size = getattr(page, "size", None)
            if size:
                return max(getattr(size, "width", 612), 1), max(getattr(size, "height", 792), 1)
        except Exception:
            pass
        return 612, 792

    @staticmethod
    def _normalize_bbox(bbox_obj, page_w: float, page_h: float) -> Optional[BoundingBox]:
        """Docling memakai origin kiri-bawah; dikonversi ke origin kiri-atas ternormalisasi 0..1."""
        try:
            if hasattr(bbox_obj, "to_top_left_origin"):
                bbox_obj = bbox_obj.to_top_left_origin(page_height=page_h)
            l, t, r, b = bbox_obj.l, bbox_obj.t, bbox_obj.r, bbox_obj.b
        except Exception:
            return None
        xs = sorted(round(max(0.0, min(1.0, v / page_w)), 5) for v in (l, r))
        ys = sorted(round(max(0.0, min(1.0, v / page_h)), 5) for v in (t, b))
        return BoundingBox(xmin=xs[0], ymin=ys[0], xmax=xs[1], ymax=ys[1])

    def parse(self, pdf_path: str, do_ocr: bool = True, max_pages: int = None) -> LandingAIParsedResponse:
        start_time = time.time()
        converter = self._get_converter(do_ocr)
        kwargs = {"page_range": (1, max_pages)} if max_pages else {}
        doc = converter.convert(pdf_path, **kwargs).document

        markdown_text = clean_ocr_text(doc.export_to_markdown())
        confidence = OCR_CONFIDENCE if do_ocr else NATIVE_CONFIDENCE
        page_dims: Dict[int, tuple] = {}
        page_children: Dict[int, List[StructureItem]] = {}
        search_from = 0

        def locate(prov_list):
            if not prov_list:
                return 1, None
            prov = prov_list[0]
            page_no = getattr(prov, "page_no", 1)
            if page_no not in page_dims:
                page_dims[page_no] = self._get_page_dimensions(doc, page_no)
            bbox = getattr(prov, "bbox", None)
            return page_no, (self._normalize_bbox(bbox, *page_dims[page_no]) if bbox is not None else None)

        def make_item(elem_type: str, item_id: str, text: str, page_no: int, bbox: Optional[BoundingBox],
                      start: int, end: int, conf: float) -> StructureItem:
            box = bbox or BoundingBox(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0)
            grounding = Grounding(page=page_no, range=TextRange(start=start, end=end), box=box, confidence=conf)
            return StructureItem(
                type=elem_type, id=item_id, text=text, grounding=grounding, confidence=conf,
                atomic_grounding=[AtomicGrounding(page=page_no, range=grounding.range, box=box, text=text, confidence=conf)],
            )

        for idx, item in enumerate(doc.texts):
            text_val = (getattr(item, "text", "") or "").strip()
            if not text_val:
                continue
            page_no, bbox = locate(getattr(item, "prov", []))

            start_pos = markdown_text.find(text_val, max(0, search_from - 50))
            if start_pos == -1:
                start_pos, end_pos = search_from, search_from  # teks berubah oleh cleaner: range kosong, bukan range palsu
            else:
                end_pos = start_pos + len(text_val)
                search_from = end_pos

            label = str(getattr(item, "label", "text")).lower()
            if "header" in label or "title" in label:
                elem_type = "header"
            elif "list" in label:
                elem_type = "list_item"
            elif "footer" in label:
                elem_type = "footer"
            elif ":" in text_val and len(text_val) < 100:
                elem_type = "key_value"
            else:
                elem_type = "paragraph"
            page_children.setdefault(page_no, []).append(
                make_item(elem_type, f"{elem_type}-{idx}", text_val, page_no, bbox, start_pos, end_pos, confidence)
            )

        for t_idx, table in enumerate(doc.tables):
            page_no, bbox = locate(getattr(table, "prov", []))
            try:
                table_df = table.export_to_dataframe(doc=doc)
                rows = [" | ".join(str(c) for c in table_df.columns)]
                rows += [" | ".join(str(v) for v in row) for row in table_df.itertuples(index=False)]
                table_text = "\n".join(rows)
            except Exception:
                table_text = ""
            if not table_text:
                continue
            page_children.setdefault(page_no, []).append(
                make_item("table", f"table-{t_idx}", table_text[:2000], page_no, bbox, 0, 0, confidence)
            )

        pages_structure = [
            StructureItem(
                type="page", id=f"page-{p_no}",
                grounding=Grounding(page=p_no, range=TextRange(start=0, end=len(markdown_text)),
                                    box=BoundingBox(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0), confidence=1.0),
                children=page_children[p_no], confidence=1.0,
            )
            for p_no in sorted(page_children)
        ]

        duration_ms = int((time.time() - start_time) * 1000)
        engine_name = "ibm-docling" + ("+ocr" if do_ocr else "")
        logger.info(f"✅ Docling selesai [{engine_name}]: {len(pages_structure)} halaman, {len(markdown_text)} karakter, {duration_ms}ms")
        return LandingAIParsedResponse(
            markdown=markdown_text,
            metadata=ParseMetadata(
                job_id=f"parse-docling-{int(time.time())}",
                page_count=len(pages_structure) or 1,
                output_markdown_chars=len(markdown_text),
                duration_ms=duration_ms,
                is_scanned=do_ocr,
                parser_engine=engine_name,
            ),
            structure=DocumentStructure(children=pages_structure),
        )
