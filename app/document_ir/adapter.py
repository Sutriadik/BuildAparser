"""
Open ADE — Adapter: parser output -> DocumentIR.

Docling, PaddleOCR (spatial), dan PP-Structure saat ini sama-sama menghasilkan
LandingAIParsedResponse, sehingga satu adapter ini sudah mencakup ketiganya.
Output parse LandingAI ADE asli (tanpa field `text`, hanya `range` ke markdown)
juga didukung, sehingga hasil LandingAI bisa dipakai sebagai pembanding grounding.
"""
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.document_ir.models import DocumentBlock, DocumentIR, DocumentPage
from app.schemas.common import LandingAIParsedResponse

_TYPE_MAP = {
    "header": "section_header",
    "section_header": "section_header",
    "title": "title",
    "paragraph": "paragraph",
    "text": "paragraph",
    "key_value": "key_value",
    "list_item": "list",
    "list": "list",
    "table": "table",
    "table_cell": "table_cell",
    "figure": "image",
    "logo": "image",
    "image": "image",
    "signature": "signature",
    "stamp": "signature",
    "attestation": "signature",
    "caption": "caption",
    "footer": "footer",
    "page_footer": "footer",
    "page_header": "header",
    "marginalia": "footer",
}


def _document_id(file_name: str, markdown: str) -> str:
    digest = hashlib.sha1(f"{file_name}:{len(markdown)}:{markdown[:500]}".encode("utf-8")).hexdigest()
    return f"doc-{digest[:12]}"


def canonical_block_type(raw_type: Optional[str]) -> str:
    return _TYPE_MAP.get((raw_type or "").lower(), "unknown")


def from_parsed_response(
    parsed: Union[LandingAIParsedResponse, Dict[str, Any]],
    file_name: str,
    document_id: Optional[str] = None,
    document_type: str = "unknown",
) -> DocumentIR:
    if isinstance(parsed, dict):
        parsed = LandingAIParsedResponse.model_validate(_fill_missing_metadata(parsed))

    markdown = parsed.markdown or ""
    doc_id = document_id or _document_id(Path(file_name).name, markdown)
    engine = parsed.metadata.parser_engine
    pages: List[DocumentPage] = []
    order = 0

    for page_item in parsed.structure.children:
        page_no = page_item.grounding.page if page_item.grounding else len(pages) + 1
        page = DocumentPage(page_number=page_no)
        for idx, item in enumerate(page_item.children or []):
            text = (item.text or "").strip()
            char_range = None
            if item.grounding and item.grounding.range:
                start, end = item.grounding.range.start, item.grounding.range.end
                # Range yang mencakup seluruh dokumen bukan range blok (dulu dipakai untuk tabel Docling).
                if 0 <= start < end <= len(markdown) and (end - start) < len(markdown):
                    char_range = (start, end)
                    if not text:
                        text = markdown[start:end].strip()
            if not text:
                continue

            box = item.grounding.box if item.grounding else None
            bbox = None
            if box is not None:
                bbox = (
                    min(box.xmin, box.xmax), min(box.ymin, box.ymax),
                    max(box.xmin, box.xmax), max(box.ymin, box.ymax),
                )
            page.blocks.append(DocumentBlock(
                document_id=doc_id,
                block_id=item.id or f"p{page_no}-b{idx}",
                page=page_no,
                block_type=canonical_block_type(item.type),
                raw_type=item.type,
                text=text,
                bbox=bbox,
                reading_order=order,
                source_engine=engine,
                source_confidence=(item.grounding.confidence if item.grounding else None) or item.confidence,
                char_range=char_range,
            ))
            order += 1
        pages.append(page)

    return DocumentIR(
        document_id=doc_id,
        file_name=Path(file_name).name,
        document_type=document_type,
        source_engine=engine,
        is_scanned=parsed.metadata.is_scanned,
        markdown=markdown,
        pages=pages,
    )


def _fill_missing_metadata(raw: Dict[str, Any]) -> Dict[str, Any]:
    """LandingAI parse JSON punya metadata yang sedikit berbeda (tanpa parser_engine)."""
    data = dict(raw)
    meta = dict(data.get("metadata") or {})
    meta.setdefault("job_id", "external")
    meta.setdefault("page_count", len((data.get("structure") or {}).get("children", [])))
    meta.setdefault("output_markdown_chars", len(data.get("markdown", "")))
    meta.setdefault("parser_engine", meta.get("model_version", "external"))
    data["metadata"] = meta
    return data
