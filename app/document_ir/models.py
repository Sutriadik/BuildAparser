"""
Open ADE — Document Intermediate Representation (Plan §9).

Satu format internal untuk semua perception engine. Layer extraction, evidence,
dan validation hanya membaca DocumentIR, tidak lagi bergantung pada format
output masing-masing parser.
"""
from typing import Iterator, List, Optional, Tuple

from pydantic import BaseModel, Field

BBox = Tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax), ternormalisasi 0..1

CANONICAL_BLOCK_TYPES = {
    "title", "section_header", "paragraph", "list", "table", "table_cell",
    "header", "footer", "caption", "image", "signature", "key_value", "unknown",
}


def union_bbox(boxes: List[BBox]) -> Optional[BBox]:
    """Bounding box gabungan (xmin/ymin minimum, xmax/ymax maksimum) dari sekumpulan box."""
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


class DocumentBlock(BaseModel):
    document_id: str
    block_id: str
    page: int
    block_type: str = "unknown"
    raw_type: Optional[str] = Field(None, description="Tipe asli dari engine sebelum dikanonisasi")
    text: str
    bbox: Optional[BBox] = None
    reading_order: int
    source_engine: str
    source_confidence: Optional[float] = None
    char_range: Optional[Tuple[int, int]] = Field(None, description="Rentang karakter pada markdown hasil parsing")


class DocumentPage(BaseModel):
    page_number: int
    width: Optional[float] = None
    height: Optional[float] = None
    blocks: List[DocumentBlock] = Field(default_factory=list)


class DocumentIR(BaseModel):
    document_id: str
    file_name: str
    document_type: str = "unknown"
    source_engine: str
    is_scanned: bool = False
    markdown: str = ""
    pages: List[DocumentPage] = Field(default_factory=list)

    def iter_blocks(self) -> Iterator[DocumentBlock]:
        for page in self.pages:
            yield from page.blocks

    @property
    def block_count(self) -> int:
        return sum(len(p.blocks) for p in self.pages)
