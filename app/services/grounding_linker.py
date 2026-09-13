"""
Open ADE — Visual Grounding Linker (compatibility layer).

Logika pencocokan dipindah ke app.evidence (matcher + locator) yang bekerja di atas
DocumentIR. Modul ini mempertahankan API lama (`find_best_bounding_box`,
`link_visual_groundings`) agar pemanggil & test lama tetap jalan tanpa ada dua
implementasi grounding yang berbeda.
"""
from typing import Any, Dict, Optional

from app.config import config
from app.document_ir.adapter import from_parsed_response
from app.document_ir.models import DocumentIR
from app.evidence.locator import iter_leaf_fields, locate_value
from app.evidence.matcher import (  # noqa: F401  (re-export untuk kompatibilitas)
    _fuzzy_ratio,
    _normalize_text,
    _token_overlap_f1,
    _tokenize,
    calculate_match_confidence,
)
from app.logger import logger
from app.schemas.common import BoundingBox, FieldVisualGrounding

_token_overlap_score = _token_overlap_f1
_fuzzy_score = _fuzzy_ratio


def _validate_and_order_box(box: BoundingBox) -> BoundingBox:
    """Menjamin xmin <= xmax, ymin <= ymax, dan seluruh nilai di rentang [0, 1]."""
    x1, x2 = (round(max(0.0, min(1.0, v)), 5) for v in (box.xmin, box.xmax))
    y1, y2 = (round(max(0.0, min(1.0, v)), 5) for v in (box.ymin, box.ymax))
    return BoundingBox(xmin=min(x1, x2), ymin=min(y1, y2), xmax=max(x1, x2), ymax=max(y1, y2))


def _as_ir(parsed: Any) -> DocumentIR:
    return parsed if isinstance(parsed, DocumentIR) else from_parsed_response(parsed, file_name="document")


def find_best_bounding_box(value_str: Any, parsed_res: Any) -> Optional[FieldVisualGrounding]:
    if value_str is None or not str(value_str).strip():
        return None
    match = locate_value(value_str, _as_ir(parsed_res), min_score=config.GROUNDING_MIN_SCORE)
    if match is None or match.bbox is None:
        return None
    box = _validate_and_order_box(BoundingBox(xmin=match.bbox[0], ymin=match.bbox[1], xmax=match.bbox[2], ymax=match.bbox[3]))
    return FieldVisualGrounding(
        field_name="",
        extracted_value=value_str,
        page=match.page,
        box=box,
        confidence=match.score,
        source_snippet=match.text[:200],
    )


def link_visual_groundings(extracted_data: Dict[str, Any], parsed_res: Any) -> Dict[str, FieldVisualGrounding]:
    ir = _as_ir(parsed_res)
    groundings: Dict[str, FieldVisualGrounding] = {}
    total = 0
    for path, value in iter_leaf_fields(extracted_data):
        if value is None or isinstance(value, bool) or str(value).strip().lower() in ("", "null", "none", "n/a", "-"):
            continue
        total += 1
        match = find_best_bounding_box(value, ir)
        if match:
            match.field_name = path
            groundings[path] = match
    logger.info(f"🔗 Grounding linked: {len(groundings)}/{total} fields ({len(groundings) / max(total, 1):.0%})")
    return groundings
