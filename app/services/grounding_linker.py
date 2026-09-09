"""
Open ADE — Visual Grounding Linker (v2)
Menghubungkan field hasil extraction ke bounding box di dokumen asli.
Menggunakan multi-strategy matching: exact → token overlap → fuzzy.
"""
from difflib import SequenceMatcher
from typing import Dict, Any, List, Optional, Tuple

from app.config import config
from app.logger import logger
from app.schemas.common import LandingAIParsedResponse, FieldVisualGrounding, BoundingBox


def _tokenize(text: str) -> List[str]:
    """Tokenize teks menjadi kata-kata lowercase, filter token pendek."""
    return [w for w in text.lower().split() if len(w) >= 2]


def _token_overlap_score(query_tokens: List[str], item_tokens: List[str]) -> float:
    """
    Menghitung Jaccard-like token overlap score.
    Berapa persen token query yang ditemukan di item text.
    """
    if not query_tokens or not item_tokens:
        return 0.0
    
    query_set = set(query_tokens)
    item_set = set(item_tokens)
    
    overlap = query_set & item_set
    if not overlap:
        return 0.0
    
    # Weighted: overlap / ukuran query (berapa persen query yang cocok)
    recall = len(overlap) / len(query_set)
    # Precision: seberapa relevan item (bukan terlalu panjang)
    precision = len(overlap) / len(item_set)
    
    # F1-like score
    if recall + precision == 0:
        return 0.0
    return 2 * (recall * precision) / (recall + precision)


def _fuzzy_score(query: str, item_text: str) -> float:
    """
    Menghitung fuzzy similarity menggunakan SequenceMatcher.
    """
    return SequenceMatcher(None, query, item_text).ratio()


def find_best_bounding_box(
    value_str: str, 
    parsed_res: LandingAIParsedResponse
) -> Optional[FieldVisualGrounding]:
    """
    Mencari koordinat Bounding Box terbaik di seluruh halaman dokumen 
    untuk suatu nilai teks yang diekstrak.
    
    Multi-strategy matching:
    1. Exact substring match → skor tertinggi
    2. Token overlap (Jaccard) → untuk multi-word values
    3. Fuzzy matching (SequenceMatcher) → fallback
    
    Prioritas: match yang paling spesifik (item text terpendek yang mengandung query)
    """
    if not value_str or len(str(value_str).strip()) < 2:
        return None
        
    query = str(value_str).strip().lower()
    query_tokens = _tokenize(query)
    
    best_match = None
    best_score = 0.0
    best_item_len = float('inf')
    
    min_score = config.GROUNDING_MIN_SCORE
    
    # Telusuri setiap halaman dan elemen structure
    for page_item in parsed_res.structure.children:
        page_num = page_item.grounding.page if page_item.grounding else 1
        
        for item in (page_item.children or []):
            item_text = (item.text or "").strip()
            if not item_text:
                continue
                
            item_text_lower = item_text.lower()
            item_tokens = _tokenize(item_text)
            
            score = 0.0
            
            # Strategy 1: Exact substring match
            if query in item_text_lower:
                # Query is a substring of item → score based on coverage
                coverage = len(query) / len(item_text_lower)
                score = 0.5 + (coverage * 0.5)  # Range: 0.5 - 1.0
                
            elif item_text_lower in query:
                # Item is a substring of query → lower score (partial match)
                coverage = len(item_text_lower) / len(query)
                score = 0.3 + (coverage * 0.3)  # Range: 0.3 - 0.6
                
            else:
                # Strategy 2: Token overlap
                token_score = _token_overlap_score(query_tokens, item_tokens)
                
                # Strategy 3: Fuzzy matching (only if token overlap found something)
                if token_score > 0.1:
                    fuzzy = _fuzzy_score(query, item_text_lower)
                    # Combine token overlap and fuzzy with weights
                    score = (token_score * 0.6) + (fuzzy * 0.4)
                elif len(query) > 5:
                    # For longer queries, try pure fuzzy as last resort
                    score = _fuzzy_score(query, item_text_lower) * 0.7
            
            # Minimum threshold check
            if score < min_score:
                continue
            
            # Tiebreaker: prefer shorter item texts (more specific match)
            # Jika score sama, pilih item yang paling pendek (paling spesifik)
            is_better = (
                score > best_score or 
                (score == best_score and len(item_text) < best_item_len)
            )
            
            if is_better and item.grounding:
                best_score = score
                best_item_len = len(item_text)
                best_match = FieldVisualGrounding(
                    field_name="",
                    extracted_value=value_str,
                    page=page_num,
                    box=item.grounding.box,
                    confidence=round(min(score, item.grounding.confidence or 0.95), 3),
                    source_snippet=item.text
                )
                    
    return best_match


def link_visual_groundings(
    extracted_data: Dict[str, Any], 
    parsed_res: LandingAIParsedResponse
) -> Dict[str, FieldVisualGrounding]:
    """
    Menghubungkan seluruh field hasil ekstraksi JSON ke koordinat Bounding Box visual dokumen.
    """
    groundings_map: Dict[str, FieldVisualGrounding] = {}
    matched_count = 0
    total_count = 0
    
    def traverse_and_link(data: Any, prefix: str = ""):
        nonlocal matched_count, total_count
        
        if isinstance(data, dict):
            for k, v in data.items():
                current_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (str, int, float)) and v is not None:
                    total_count += 1
                    match = find_best_bounding_box(str(v), parsed_res)
                    if match:
                        match.field_name = current_key
                        groundings_map[current_key] = match
                        matched_count += 1
                elif isinstance(v, (dict, list)):
                    traverse_and_link(v, current_key)
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                traverse_and_link(item, f"{prefix}[{idx}]")

    traverse_and_link(extracted_data)
    
    match_rate = matched_count / max(total_count, 1)
    logger.info(f"🔗 Grounding linked: {matched_count}/{total_count} fields ({match_rate:.0%})")
    
    return groundings_map
