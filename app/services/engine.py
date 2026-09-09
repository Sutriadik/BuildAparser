"""
Open ADE — Engine Orchestrator
End-to-end pipeline: Parse → Auto-Classify → Extract → Visual Grounding.
"""
import os
import json
import time
from pathlib import Path
from typing import Optional, Union, Dict, Any, Tuple

from app.config import config
from app.logger import logger
from app.schemas.common import LandingAIParsedResponse
from app.schemas.contract import ContractExtractionSchema
from app.schemas.sph import SPHExtractionSchema
from app.parsers.utils import is_scanned_pdf
from app.parsers.docling_parser import DoclingParser
from app.parsers.paddle_parser import PaddleOCRParser
from app.extractors.ollama_client import OllamaExtractor
from app.services.classifier import DocumentClassifier
from app.services.grounding_linker import link_visual_groundings


class ParsingError(Exception):
    """Raised when document parsing fails."""
    pass


class ExtractionError(Exception):
    """Raised when data extraction fails."""
    pass


class OpenADEEngine:
    """
    Open-Source Agentic Document Extraction (ADE) Engine
    Pengganti LandingAI ADE 100% Free, Auto-Classifying, & Visually-Grounded.
    """
    def __init__(self):
        self.docling_parser = DoclingParser()
        self.paddle_parser = PaddleOCRParser()
        self.classifier = DocumentClassifier()
        self.extractor = OllamaExtractor()

    def parse(self, pdf_path: str, max_pages: int = None) -> LandingAIParsedResponse:
        """
        Stage 1: Parse Document
        Mendeteksi layout, tabel, teks, dan visual grounding bounding box.
        """
        pdf_path_obj = Path(pdf_path)
        if not pdf_path_obj.exists():
            raise ParsingError(f"File PDF tidak ditemukan: {pdf_path}")

        logger.info(f"📄 Memproses Dokumen: {pdf_path_obj.name}")
        
        try:
            scanned = is_scanned_pdf(pdf_path)
        except Exception as e:
            raise ParsingError(f"Gagal mendeteksi tipe PDF: {e}")
        
        parse_start = time.time()
        
        if scanned:
            logger.info("🔍 Mode: [SCANNED / IMAGE-BASED PDF] → PaddleOCR Engine")
            try:
                parsed_res = self.paddle_parser.parse(pdf_path, max_pages=max_pages)
            except Exception as e:
                raise ParsingError(f"PaddleOCR parsing gagal: {e}")
        else:
            logger.info("🔍 Mode: [DIGITAL NATIVE PDF] → IBM Docling Engine")
            try:
                parsed_res = self.docling_parser.parse(pdf_path)
            except Exception as e:
                raise ParsingError(f"Docling parsing gagal: {e}")
        
        parse_duration = time.time() - parse_start
        logger.info(
            f"✅ Parsing selesai: {parsed_res.metadata.page_count} halaman | "
            f"{parsed_res.metadata.output_markdown_chars} karakter | {parse_duration:.1f}s"
        )
        return parsed_res

    def extract(
        self, 
        markdown_text: str, 
        doc_type: str = "auto"
    ) -> Tuple[Union[ContractExtractionSchema, SPHExtractionSchema], str]:
        """
        Stage 2: Schema-Driven Extraction dengan Auto-Classifier
        """
        resolved_doc_type = doc_type.lower()
        
        # 1. Auto-Classification jika mode "auto"
        if resolved_doc_type == "auto":
            classify_start = time.time()
            classification = self.classifier.classify(markdown_text)
            resolved_doc_type = classification.document_type
            classify_duration = time.time() - classify_start
            logger.info(
                f"🏷️  Auto-Classified: [{resolved_doc_type.upper()}] "
                f"(Confidence: {classification.confidence * 100:.1f}%) "
                f"({classify_duration:.1f}s)"
            )
            
        logger.info(f"🤖 Menjalankan Ekstraksi AI [{config.OLLAMA_MODEL}] untuk tipe: [{resolved_doc_type.upper()}]")
        
        extract_start = time.time()
        
        try:
            if resolved_doc_type in ["contract", "spk", "perjanjian"]:
                extracted = self.extractor.extract_contract(markdown_text)
            elif resolved_doc_type in ["sph", "penawaran", "quote"]:
                extracted = self.extractor.extract_sph(markdown_text)
            else:
                logger.warning(f"⚠️  Tipe dokumen '{resolved_doc_type}' tidak dikenal, fallback ke contract extraction")
                extracted = self.extractor.extract_contract(markdown_text)
        except Exception as e:
            raise ExtractionError(f"Extraction gagal untuk tipe '{resolved_doc_type}': {e}")
        
        extract_duration = time.time() - extract_start
        logger.info(f"✅ Extraction selesai ({extract_duration:.1f}s)")
        
        return extracted, resolved_doc_type

    def _generate_quality_report(self, extracted_dict: Dict[str, Any], grounding_map: Dict) -> Dict[str, Any]:
        """
        Menghasilkan quality report untuk extraction results.
        """
        # Count fields
        total_fields = 0
        filled_fields = 0
        null_fields = []
        
        def count_fields(data, prefix=""):
            nonlocal total_fields, filled_fields
            if isinstance(data, dict):
                for k, v in data.items():
                    key = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, dict):
                        count_fields(v, key)
                    elif isinstance(v, list):
                        total_fields += 1
                        if v:
                            filled_fields += 1
                        else:
                            null_fields.append(key)
                    else:
                        total_fields += 1
                        if v is not None and v != "" and v != 0.0:
                            filled_fields += 1
                        else:
                            null_fields.append(key)
        
        count_fields(extracted_dict)
        
        fill_rate = filled_fields / max(total_fields, 1)
        grounding_rate = len(grounding_map) / max(filled_fields, 1)
        
        report = {
            "total_fields": total_fields,
            "filled_fields": filled_fields,
            "null_fields_count": len(null_fields),
            "null_fields": null_fields,
            "fill_rate": round(fill_rate, 3),
            "grounding_match_count": len(grounding_map),
            "grounding_rate": round(grounding_rate, 3),
        }
        
        # Log quality summary
        quality_emoji = "🟢" if fill_rate > 0.8 else "🟡" if fill_rate > 0.6 else "🔴"
        logger.info(
            f"\n{'='*60}\n"
            f"📊 QUALITY REPORT\n"
            f"{'='*60}\n"
            f"  {quality_emoji} Field fill rate : {filled_fields}/{total_fields} ({fill_rate:.0%})\n"
            f"  🔗 Grounding rate  : {len(grounding_map)}/{filled_fields} ({grounding_rate:.0%})"
        )
        
        if null_fields:
            logger.warning(f"  ⚠️  Null fields     : {', '.join(null_fields)}")
        
        logger.info(f"{'='*60}")
        
        return report

    def process_full(
        self, 
        pdf_path: str, 
        doc_type: str = "auto", 
        output_dir: str = None,
        max_pages: int = None
    ) -> Dict[str, Any]:
        """
        End-to-End Pipeline: Parse + Auto-Classify + Extract + Visual Grounding Linker
        """
        pipeline_start = time.time()
        pdf_path_obj = Path(pdf_path)
        base_name = pdf_path_obj.stem
        
        parsing_dir = Path(output_dir) / "parsing" if output_dir else config.PARSING_OUTPUT_DIR
        extraction_dir = Path(output_dir) / "extraction" if output_dir else config.EXTRACTION_OUTPUT_DIR
        
        parsing_dir.mkdir(parents=True, exist_ok=True)
        extraction_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Parse Stage
        parsed_res = self.parse(pdf_path, max_pages=max_pages)
        
        parse_md_file = parsing_dir / f"{base_name}.parse.md"
        parse_json_file = parsing_dir / f"{base_name}.parse.json"
        
        with open(parse_md_file, "w", encoding="utf-8") as f:
            f.write(parsed_res.markdown)
            
        with open(parse_json_file, "w", encoding="utf-8") as f:
            json.dump(parsed_res.model_dump(), f, indent=2, ensure_ascii=False)
            
        # 2. Extract Stage (Auto-Classify & Extract)
        extracted_data, detected_type = self.extract(parsed_res.markdown, doc_type=doc_type)
        extracted_dict = extracted_data.model_dump(by_alias=True)
        
        # 3. Visual Grounding Linking
        logger.info("🔗 Menghubungkan visual grounding bounding boxes ke setiap field...")
        grounding_map = link_visual_groundings(extracted_dict, parsed_res)
        
        # 4. Quality Report
        quality_report = self._generate_quality_report(extracted_dict, grounding_map)
        
        final_extraction_payload = {
            "document_name": pdf_path_obj.name,
            "document_type": detected_type,
            "data": extracted_dict,
            "visual_groundings": {k: v.model_dump() for k, v in grounding_map.items()},
            "quality_report": quality_report,
        }
        
        # Save extract file
        extract_json_file = extraction_dir / f"{base_name}.extract.json"
        with open(extract_json_file, "w", encoding="utf-8") as f:
            json.dump(final_extraction_payload, f, indent=2, ensure_ascii=False)
        
        pipeline_duration = time.time() - pipeline_start
            
        logger.info(
            f"\n🎉 Pipeline selesai dalam {pipeline_duration:.1f}s\n"
            f"  📁 Parsing    : {parsing_dir}\n"
            f"     ├── {parse_md_file.name}\n"
            f"     └── {parse_json_file.name}\n"
            f"  📁 Extraction : {extraction_dir}\n"
            f"     └── {extract_json_file.name}"
        )
        
        return {
            "parsed": parsed_res,
            "extracted": extracted_data,
            "document_type": detected_type,
            "grounding_map": grounding_map,
            "quality_report": quality_report,
            "files": {
                "markdown": str(parse_md_file),
                "parse_json": str(parse_json_file),
                "extract_json": str(extract_json_file)
            }
        }
