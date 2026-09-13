"""
Open ADE — Engine Orchestrator.

Profile → Parse (routed) → Document IR → Classify → Extract → Validate → Evidence → Output.

Setiap run menyimpan provenance (versi parser/schema/prompt/rule/model, jumlah LLM call,
durasi per tahap) supaya perubahan akurasi bisa ditelusuri sumbernya (Plan §26–27).
"""
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from app.config import config
from app.document_ir.adapter import from_parsed_response
from app.document_ir.models import DocumentIR
from app.evidence.locator import build_field_evidence
from app.extractors.prompts import PROMPT_VERSION
from app.ingestion.profiler import DocumentProfile, profile_document
from app.logger import logger
from app.schemas.common import BoundingBox, FieldVisualGrounding, LandingAIParsedResponse
from app.schemas.contract import ContractExtractionSchema
from app.schemas.evidence import FieldEvidence, FieldStatus, ValidationReport
from app.schemas.sph import SPHExtractionSchema
from app.services.classifier import DocumentClassifier
from app.validation.rules import RULE_VERSION, validate_extraction

MIN_CHARS_PER_PAGE_AFTER_OCR = 50
CONTRACT_TYPES = {"contract", "spk", "perjanjian"}
SPH_TYPES = {"sph", "penawaran", "quote"}


class ParsingError(Exception):
    """Raised when document parsing fails."""


class ExtractionError(Exception):
    """Raised when data extraction fails."""


class OpenADEEngine:
    """Parser & model dimuat lazy: server FastAPI tidak memuat torch/paddle sebelum ada request."""

    def __init__(self):
        self._docling = None
        self._paddle = None
        self._extractor = None
        self.classifier = DocumentClassifier()

    @property
    def docling_parser(self):
        if self._docling is None:
            from app.parsers.docling_parser import DoclingParser
            self._docling = DoclingParser()
        return self._docling

    @property
    def paddle_parser(self):
        if self._paddle is None:
            from app.parsers.paddle_parser import PaddleOCRParser
            self._paddle = PaddleOCRParser()
        return self._paddle

    @property
    def extractor(self):
        if self._extractor is None:
            from app.extractors.ollama_client import OllamaExtractor
            self._extractor = OllamaExtractor()
        return self._extractor

    # ------------------------------------------------------------------ stage 1: parse
    def profile(self, pdf_path: str, max_pages: int = None) -> Optional[DocumentProfile]:
        try:
            return profile_document(pdf_path, max_pages=max_pages)
        except Exception as e:
            logger.warning(f"⚠️  Profiling gagal ({e}); dokumen diperlakukan sebagai non-PDF")
            return None

    def parse(self, pdf_path: str, max_pages: int = None, parser: str = "auto",
              profile: Optional[DocumentProfile] = None) -> LandingAIParsedResponse:
        path = Path(pdf_path)
        if not path.exists():
            raise ParsingError(f"File tidak ditemukan: {pdf_path}")

        profile = profile or self.profile(pdf_path, max_pages)
        if profile is not None:
            needs_ocr = profile.needs_ocr
            logger.info(f"📄 {path.name}: {profile.kind.upper()} ({profile.page_count} hlm, OCR hlm: {profile.ocr_pages[:10] or '-'})")
        else:
            needs_ocr = path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

        choice = parser.lower()
        started = time.time()
        if choice == "paddle":
            parsed = self.paddle_parser.parse(pdf_path, max_pages=max_pages)
        else:
            try:
                parsed = self.docling_parser.parse(pdf_path, do_ocr=needs_ocr, max_pages=max_pages)
            except Exception as e:
                if not needs_ocr and choice == "auto":
                    raise ParsingError(f"Docling parsing gagal: {e}")
                logger.warning(f"⚠️  Docling gagal ({e}), fallback ke PaddleOCR...")
                parsed = self.paddle_parser.parse(pdf_path, max_pages=max_pages)
            else:
                real_chars = len(re.sub(r'<!--.*?-->|\s+', '', parsed.markdown))
                too_little = real_chars < MIN_CHARS_PER_PAGE_AFTER_OCR * max(parsed.metadata.page_count, 1)
                if choice == "auto" and needs_ocr and too_little:
                    logger.info("ℹ️  Output OCR Docling terlalu sedikit → PaddleOCR")
                    parsed = self.paddle_parser.parse(pdf_path, max_pages=max_pages)

        logger.info(f"✅ Parsing [{parsed.metadata.parser_engine}]: {parsed.metadata.page_count} hlm | "
                    f"{parsed.metadata.output_markdown_chars} karakter | {time.time() - started:.1f}s")
        return parsed

    @staticmethod
    def ensure_enough_text(parsed: LandingAIParsedResponse) -> None:
        """
        Jangan kirim dokumen (hampir) kosong ke LLM: model cenderung menghasilkan JSON tanpa henti
        sampai timeout, dan hasilnya pasti halusinasi. Gagal cepat dengan pesan yang jelas.
        """
        pages = max(parsed.metadata.page_count, 1)
        real_chars = len(re.sub(r'\[IMAGE[^\]]*\]|<!--.*?-->|\s+', '', parsed.markdown))
        if real_chars < MIN_CHARS_PER_PAGE_AFTER_OCR * pages:
            raise ParsingError(
                f"Teks hasil parsing terlalu sedikit ({real_chars} karakter untuk {pages} halaman, "
                f"engine {parsed.metadata.parser_engine}). Ekstraksi LLM dibatalkan. "
                f"Coba parser 'auto' atau 'docling', dan cek kualitas scan."
            )

    # ------------------------------------------------------------------ stage 2: extract
    def extract(self, markdown_text: str, doc_type: str = "auto") -> Tuple[Union[ContractExtractionSchema, SPHExtractionSchema], str]:
        resolved = doc_type.lower()
        if resolved == "auto":
            classification = self.classifier.classify(markdown_text)
            resolved = classification.document_type
            logger.info(f"🏷️  Auto-Classified: [{resolved.upper()}] ({classification.confidence * 100:.0f}%)")

        self.extractor.reset_stats()
        started = time.time()
        try:
            if resolved in SPH_TYPES:
                extracted, resolved = self.extractor.extract_sph(markdown_text), "sph"
            else:
                if resolved not in CONTRACT_TYPES:
                    logger.warning(f"⚠️  Belum ada schema untuk tipe '{resolved}', memakai schema kontrak")
                extracted, resolved = self.extractor.extract_contract(markdown_text), "contract"
        except Exception as e:
            raise ExtractionError(f"Extraction gagal untuk tipe '{resolved}': {e}")
        logger.info(f"✅ Extraction selesai ({time.time() - started:.1f}s, {self.extractor.llm_calls} LLM call)")
        return extracted, resolved

    # ------------------------------------------------------------------ reporting
    @staticmethod
    def build_quality_report(evidence: List[FieldEvidence], validation: ValidationReport) -> Dict[str, Any]:
        total = len(evidence)
        filled = [e for e in evidence if e.status != FieldStatus.MISSING]
        grounded = [e for e in filled if e.evidence_score > 0]
        status_counts = Counter(e.status.value for e in evidence)
        report = {
            "total_fields": total,
            "filled_fields": len(filled),
            "null_fields": [e.field for e in evidence if e.status == FieldStatus.MISSING],
            "fill_rate": round(len(filled) / max(total, 1), 3),
            "grounded_fields": len(grounded),
            "grounding_rate": round(len(grounded) / max(len(filled), 1), 3),
            "status_counts": dict(status_counts),
            "review_required_fields": [e.field for e in evidence if e.status in (FieldStatus.REVIEW_REQUIRED, FieldStatus.CONFLICT)],
            "validation_status": validation.status,
            "validation_issue_count": len(validation.issues),
            "requires_pm_confirmation": True,
        }
        emoji = "🟢" if validation.status == "pass" else "🟡" if validation.status == "warn" else "🔴"
        logger.info(
            f"\n{'=' * 60}\n📊 QUALITY REPORT\n{'=' * 60}\n"
            f"  Fill rate      : {len(filled)}/{total} ({report['fill_rate']:.0%})\n"
            f"  Grounding rate : {len(grounded)}/{len(filled)} ({report['grounding_rate']:.0%})\n"
            f"  Status         : {dict(status_counts)}\n"
            f"  {emoji} Validation   : {validation.status} ({len(validation.issues)} issue)\n{'=' * 60}"
        )
        for issue in validation.issues:
            logger.warning(f"  [{issue.severity.value}] {issue.rule}: {issue.message} (expected={issue.expected}, actual={issue.actual})")
        return report

    @staticmethod
    def evidence_to_visual_groundings(evidence: List[FieldEvidence]) -> Dict[str, Dict[str, Any]]:
        """Format lama `visual_groundings` tetap disediakan untuk konsumen yang sudah ada."""
        result = {}
        for e in evidence:
            if e.bbox is None or e.page is None:
                continue
            result[e.field] = FieldVisualGrounding(
                field_name=e.field, extracted_value=e.value, page=e.page,
                box=BoundingBox(**e.bbox.model_dump()), confidence=e.evidence_score, source_snippet=(e.evidence_text or "")[:200],
            ).model_dump()
        return result

    # ------------------------------------------------------------------ end-to-end
    def process_full(self, pdf_path: str, doc_type: str = "auto", output_dir: str = None,
                     max_pages: int = None, parser: str = "auto") -> Dict[str, Any]:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        timings: Dict[str, float] = {}
        t0 = time.time()
        path = Path(pdf_path)

        parsing_dir = Path(output_dir) / "parsing" if output_dir else config.PARSING_OUTPUT_DIR
        extraction_dir = Path(output_dir) / "extraction" if output_dir else config.EXTRACTION_OUTPUT_DIR
        parsing_dir.mkdir(parents=True, exist_ok=True)
        extraction_dir.mkdir(parents=True, exist_ok=True)

        t = time.time()
        profile = self.profile(pdf_path, max_pages)
        timings["profile_s"] = round(time.time() - t, 2)

        t = time.time()
        parsed = self.parse(pdf_path, max_pages=max_pages, parser=parser, profile=profile)
        timings["parse_s"] = round(time.time() - t, 2)
        parse_md_file = parsing_dir / f"{path.stem}.parse.md"
        parse_json_file = parsing_dir / f"{path.stem}.parse.json"
        parse_md_file.write_text(parsed.markdown, encoding="utf-8")
        parse_json_file.write_text(json.dumps(parsed.model_dump(exclude_none=True), ensure_ascii=False), encoding="utf-8")

        ir: DocumentIR = from_parsed_response(parsed, file_name=path.name)
        self.ensure_enough_text(parsed)

        t = time.time()
        extracted, detected_type = self.extract(parsed.markdown, doc_type=doc_type)
        timings["extract_s"] = round(time.time() - t, 2)
        ir.document_type = detected_type
        data = extracted.model_dump(by_alias=True)

        t = time.time()
        validation = validate_extraction(detected_type, data)
        evidence = build_field_evidence(data, ir, validation, min_score=config.GROUNDING_MIN_SCORE)
        timings["validate_and_ground_s"] = round(time.time() - t, 2)
        quality_report = self.build_quality_report(evidence, validation)
        timings["total_s"] = round(time.time() - t0, 2)

        run_info = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "document_id": ir.document_id,
            "parser_engine": parsed.metadata.parser_engine,
            "parser_version": config.PARSER_VERSION,
            "schema_version": config.SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "rule_version": RULE_VERSION,
            "model": config.OLLAMA_MODEL,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "page_count": parsed.metadata.page_count,
            "block_count": ir.block_count,
            "llm_calls": self.extractor.llm_calls,
            "llm_seconds": round(self.extractor.llm_seconds, 1),
            "timings": timings,
        }
        payload = {
            "document_name": path.name,
            "document_type": detected_type,
            "data": data,
            "validation": validation.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in evidence],
            "visual_groundings": self.evidence_to_visual_groundings(evidence),
            "quality_report": quality_report,
            "document_profile": profile.to_dict() if profile else None,
            "run_info": run_info,
        }
        extract_json_file = extraction_dir / f"{path.stem}.extract.json"
        extract_json_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info(f"\n🎉 Pipeline selesai dalam {timings['total_s']}s ({run_info['llm_calls']} LLM call)\n"
                    f"  📁 {parse_md_file}\n  📁 {parse_json_file}\n  📁 {extract_json_file}")
        return {
            "parsed": parsed,
            "ir": ir,
            "extracted": extracted,
            "document_type": detected_type,
            "validation": validation,
            "evidence": evidence,
            "quality_report": quality_report,
            "run_info": run_info,
            "files": {"markdown": str(parse_md_file), "parse_json": str(parse_json_file), "extract_json": str(extract_json_file)},
        }
