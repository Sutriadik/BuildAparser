"""
Open ADE — Context Analyzer (VLM Pre-processor)

Menganalisis markdown dokumen sebelum dikirim ke LLM untuk extraction.
Menambahkan context annotations yang membantu LLM kecil (Qwen 2.5:7b)
memahami struktur dan konteks dokumen.

Features:
1. Section Detection — mendeteksi pasal-pasal dalam dokumen kontrak/SPH
2. Key Entity Pre-extraction — regex-based extraction untuk nama, angka, tanggal
3. Context Enrichment — menambahkan annotation [SECTION:], [KEY:] ke markdown
"""
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from app.logger import logger


@dataclass
class DocumentSection:
    """Satu bagian/pasal dokumen."""
    title: str
    content: str
    section_type: str  # preamble, scope, price, payment, duration, penalty, guarantee, closing, attachment
    start_line: int
    end_line: int


@dataclass
class PreExtractedEntity:
    """Entity yang diekstrak via regex sebelum LLM processing."""
    field_name: str
    value: str
    source_line: str
    confidence: float = 0.8


class ContextAnalyzer:
    """
    Pre-processor yang menganalisis struktur dokumen dan menambahkan
    context annotations sebelum dikirim ke LLM extraction.
    """
    
    # Section patterns untuk dokumen kontrak Indonesia
    SECTION_PATTERNS = [
        (r'(?:^|\n)\s*\d+\.\s*LINGKUP\s+PEKERJAAN', 'scope'),
        (r'(?:^|\n)\s*\d+\.\s*HARGA', 'price'),
        (r'(?:^|\n)\s*\d+\.\s*WAKTU\s+PELAKSANAAN', 'duration'),
        (r'(?:^|\n)\s*\d+\.\s*CARA\s+PEMBAYARAN', 'payment'),
        (r'(?:^|\n)\s*\d+\.\s*GARANSI', 'guarantee'),
        (r'(?:^|\n)\s*\d+\.\s*SANKSI', 'penalty'),
        (r'(?:^|\n)\s*\d+\.\s*LAIN[- ]?LAIN', 'closing'),
        (r'(?:^|\n)\s*Lampiran\s*:', 'attachment'),
        (r'(?:^|\n)\s*SURAT\s+PERINTAH\s+KERJA', 'preamble'),
        (r'(?:^|\n)\s*SURAT\s+PENAWARAN', 'preamble'),
        (r'(?:^|\n)\s*Demikian\s+Surat', 'closing'),
    ]
    
    # Entity extraction patterns
    ENTITY_PATTERNS = [
        # Bank info
        (r'(?:rekening\s+)?[Bb]ank\s+(\w[\w\s]*?)(?:\s+[Cc]abang|\s+[Nn]o\.?|\s*,)', 'Nama Bank'),
        (r'[Cc]abang\s+([\w\s]+?)(?:\s+No\.?|\s*,)', 'Lokasi Cabang Bank'),
        (r'No\.?\s*([\d]+[.\-\d]*)', 'Nomor Rekening Bank'),
        (r'(?:a\.?\s*n\.?|atas\s+nama)\s+([\w\s.]+?)(?:\s*[.,]|\s*$)', 'Nama Rekening Bank'),
        
        # Dates
        (r'negosiasi\s+(?:harga\s+)?(?:pada\s+)?(?:tanggal\s+)?(\d{1,2}\s+\w+\s+\d{4})', 'Tanggal Negosiasi'),
        (r'[Dd]ibuat\s+di\s*:\s*(\w+)', 'Lokasi'),
        (r'[Tt]anggal\s*:\s*(\d{1,2}\s+\w+\s+\d{4})', 'Tanggal Pembuatan Dokumen'),
        
        # Duration
        (r'(?:jangka\s+waktu|akses)\s+selama\s+([\d]+\s+\w+\s+\d{4}\s*[-–]\s*\d+\s+\w+\s+\d{4})', 'Jangka Waktu'),
        (r'(?:lama\s+pekerjaan|durasi)\s+selama\s+(\d+\s+hari\s+kalender)', 'Durasi Kerja'),
        
        # Penalty
        (r'(?:denda|sanksi)\s+sebesar\s+([\d/]+\s*(?:\([^)]+\))?)', 'Persentase Sanksi/Penalti'),
        
        # Financial
        (r'[Ss]ub\s*[Tt]otal\s*:?\s*(?:Rp\.?\s*)?([\d.,]+)', 'sub total'),
        (r'PPN\s*\d+%?\s*:?\s*(?:Rp\.?\s*)?([\d.,]+)', 'Total PPN'),
        (r'[Tt]otal\s*:?\s*(?:Rp\.?\s*)?([\d.,]+)', 'Total Harga Pekerjaan'),
    ]

    def detect_sections(self, markdown_text: str) -> List[DocumentSection]:
        """
        Mendeteksi section/pasal dalam dokumen.
        Returns list of DocumentSection sorted by position.
        """
        lines = markdown_text.split('\n')
        sections = []
        
        for pattern, section_type in self.SECTION_PATTERNS:
            for match in re.finditer(pattern, markdown_text, re.IGNORECASE | re.MULTILINE):
                pos = match.start()
                line_num = markdown_text[:pos].count('\n')
                title = match.group(0).strip()
                
                sections.append(DocumentSection(
                    title=title,
                    content="",  # Will be filled below
                    section_type=section_type,
                    start_line=line_num,
                    end_line=line_num
                ))
        
        # Sort by position
        sections.sort(key=lambda s: s.start_line)
        
        # Fill content for each section (from its start to next section start)
        for i, section in enumerate(sections):
            start = section.start_line
            end = sections[i + 1].start_line if i + 1 < len(sections) else len(lines)
            section.end_line = end
            section.content = '\n'.join(lines[start:end])
        
        return sections

    def pre_extract_entities(self, markdown_text: str) -> List[PreExtractedEntity]:
        """
        Regex-based pre-extraction untuk entities yang sering di-miss oleh LLM.
        """
        entities = []
        
        for pattern, field_name in self.ENTITY_PATTERNS:
            for match in re.finditer(pattern, markdown_text, re.IGNORECASE):
                value = match.group(1).strip()
                if value and len(value) > 1:
                    # Find source line
                    pos = match.start()
                    line_start = markdown_text.rfind('\n', 0, pos) + 1
                    line_end = markdown_text.find('\n', pos)
                    if line_end == -1:
                        line_end = len(markdown_text)
                    source_line = markdown_text[line_start:line_end].strip()
                    
                    entities.append(PreExtractedEntity(
                        field_name=field_name,
                        value=value,
                        source_line=source_line[:120],
                        confidence=0.75
                    ))
        
        return entities

    @staticmethod
    def normalize_indonesian_numbers(text: str) -> str:
        """
        Mengkonversi format angka Indonesia ke plain numbers sebelum dikirim ke LLM.
        
        Indonesian format: 13.500.000 (dots as thousand separators)
        Output: 13500000
        
        Rules:
        - Match patterns: digits.digits.digits (at least 2 groups separated by dots)
        - Each group must be exactly 3 digits (except the first which can be 1-3)
        - Preserve numbers that are NOT Indonesian format (e.g., IP addresses, versions)
        - Handle optional "Rp", "Rp.", "IDR" prefixes  
        - Handle trailing ",- " or ", -" suffixes
        """
        def _replace_id_number(match):
            full = match.group(0)
            # Extract the number part (remove Rp prefix and trailing suffix)
            prefix = match.group(1) or ""  # "Rp. " etc
            number_part = match.group(2)
            suffix = match.group(3) or ""  # ",- " etc
            
            # Remove dots to get plain number
            plain = number_part.replace(".", "")
            return f"{prefix}{plain}{suffix}"
        
        # Pattern: optional Rp prefix + Indonesian formatted number + optional trailing
        # Indonesian number: 1-3 digits, then groups of exactly 3 digits separated by dots
        pattern = r'((?:Rp\.?\s*)?)((?:\d{1,3})(?:\.\d{3}){2,})((?:\s*,\s*-)?)'
        
        result = re.sub(pattern, _replace_id_number, text)
        
        # Count changes for logging
        changes = len(re.findall(pattern, text))
        if changes > 0:
            logger.info(f"  💱 Normalized {changes} Indonesian-formatted numbers")
        
        return result

    def enrich_markdown(self, markdown_text: str) -> str:
        """
        Menambahkan context annotations ke markdown untuk membantu LLM.
        
        Steps:
        1. Normalize Indonesian numbers (13.500.000 → 13500000)
        2. Detect sections and entities
        3. Add [SECTION:] and [KEY:] annotations
        """
        # Step 1: Normalize Indonesian number format FIRST
        normalized_text = self.normalize_indonesian_numbers(markdown_text)
        
        sections = self.detect_sections(normalized_text)
        entities = self.pre_extract_entities(normalized_text)
        
        if not sections and not entities:
            return normalized_text
        
        logger.info(f"🧠 Context Analyzer: {len(sections)} sections, {len(entities)} pre-extracted entities")
        
        # Build enriched version
        lines = normalized_text.split('\n')
        enriched_lines = list(lines)
        
        # Add section markers (insert from bottom to top to preserve line numbers)
        section_inserts = []
        for section in sections:
            label = section.section_type.upper()
            section_inserts.append((section.start_line, f"[SECTION: {label}]"))
        
        # Add entity markers
        entity_inserts = []
        for entity in entities:
            # Find the line containing the entity
            for i, line in enumerate(lines):
                if entity.value in line:
                    entity_inserts.append((i + 1, f"[KEY: {entity.field_name} = {entity.value}]"))
                    break
        
        # Combine and sort inserts (bottom to top for stable insertion)
        all_inserts = section_inserts + entity_inserts
        all_inserts.sort(key=lambda x: x[0], reverse=True)
        
        for line_num, marker in all_inserts:
            enriched_lines.insert(line_num, marker)
        
        enriched = '\n'.join(enriched_lines)
        
        # Log summary
        for entity in entities:
            logger.debug(f"  🔑 Pre-extracted: {entity.field_name} = {entity.value}")
        
        return enriched

    def get_section_content(self, markdown_text: str, section_type: str) -> Optional[str]:
        """
        Mengambil konten dari section tertentu.
        Berguna untuk targeted extraction.
        """
        sections = self.detect_sections(markdown_text)
        for section in sections:
            if section.section_type == section_type:
                return section.content
        return None

    def get_entity_hints(self, markdown_text: str) -> Dict[str, str]:
        """
        Mengembalikan dict field_name → value dari pre-extraction.
        Digunakan sebagai fallback jika LLM miss field tertentu.
        """
        entities = self.pre_extract_entities(markdown_text)
        hints = {}
        for entity in entities:
            if entity.field_name not in hints:
                hints[entity.field_name] = entity.value
        return hints
