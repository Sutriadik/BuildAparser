import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path

import uvicorn
import urllib.request
import urllib.error

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import config
from app.exporters.nocodb import build_nocodb_payload
from app.services.engine import OpenADEEngine

tags_metadata = [
    {
        "name": "System & Health",
        "description": "Health check, konfigurasi, dan status ketersediaan model/engine.",
    },
    {
        "name": "Document Parsing",
        "description": "Ekstraksi layout, reading order, struktur tabel, dan Markdown (Docling / PaddleOCR).",
    },
    {
        "name": "Document Extraction",
        "description": "Ekstraksi data terstruktur berbasis skema Pydantic & LLM Qwen 2.5.",
    },
    {
        "name": "End-to-End Pipeline",
        "description": "Pipeline terpadu (Ingest -> Parse -> Classify -> Extract -> Validate -> Evidence Grounding).",
    },
]

app = FastAPI(
    title="Open ADE - Document Parser & Extractor",
    description="Evidence-grounded Document AI & Extraction untuk Kontrak/SPK, SPH, dan BAST (Docling, PaddleOCR, Ollama Qwen 2.5)",
    version="1.1.0",
    openapi_tags=tags_metadata,
)

# CORS Middleware agar API dapat diakses dari Web Client / Dashboard frontend (React/Next.js/Vue/dll.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Engine memuat model secara lazy. Lock memastikan satu dokumen diproses dalam satu waktu:
# Docling/Paddle/Ollama lokal tidak aman dan tidak lebih cepat bila dipanggil paralel di satu mesin.
engine = OpenADEEngine()
_engine_lock = threading.Lock()
ALLOWED_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def _check_ollama_status() -> dict:
    """Cek konektivitas ke Ollama server secara cepat (timeout 2s)."""
    ollama_url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(ollama_url, headers={"User-Agent": "OpenADE-HealthCheck"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                model_ready = any(config.OLLAMA_MODEL in m for m in models)
                return {
                    "online": True,
                    "target_model": config.OLLAMA_MODEL,
                    "target_model_installed": model_ready,
                    "available_models": models,
                }
    except Exception as e:
        return {
            "online": False,
            "target_model": config.OLLAMA_MODEL,
            "target_model_installed": False,
            "error": str(e),
        }
    return {"online": False, "target_model": config.OLLAMA_MODEL, "target_model_installed": False}


def _save_upload(file: UploadFile) -> Path:
    """Nama file dari klien tidak dipakai sebagai path (mencegah path traversal & tabrakan nama)."""
    original = Path(file.filename or "upload.pdf").name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"Format tidak didukung: {suffix or '(tanpa ekstensi)'}")
    upload_dir = config.TEMP_UPLOADS / uuid.uuid4().hex
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / original
    with open(target, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return target


def _cleanup(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)


@app.get("/", tags=["System & Health"], summary="Root status service")
def read_root():
    return {
        "status": "online",
        "service": "Open ADE Document AI Service",
        "version": "1.1.0",
        "model": config.OLLAMA_MODEL,
        "engine": "Docling + PaddleOCR + Ollama",
        "docs_url": "/docs",
    }


@app.get("/health", tags=["System & Health"], summary="Health check endpoint")
@app.get("/api/v1/health", tags=["System & Health"], summary="API v1 Health check endpoint")
def health_check():
    ollama_info = _check_ollama_status()
    storage_ok = config.TEMP_UPLOADS.exists() and config.OUTPUT_DIR.exists()
    status = "healthy" if (storage_ok and ollama_info.get("online")) else "degraded" if storage_ok else "unhealthy"
    return {
        "status": status,
        "storage": {
            "ready": storage_ok,
            "temp_dir": str(config.TEMP_UPLOADS),
            "output_dir": str(config.OUTPUT_DIR),
        },
        "ollama": ollama_info,
        "version": {
            "parser_version": config.PARSER_VERSION,
            "schema_version": config.SCHEMA_VERSION,
        },
    }


@app.get("/api/v1/info", tags=["System & Health"], summary="Informasi konfigurasi dan kapabilitas API")
def get_system_info():
    return {
        "service": "Open ADE (Autonomous Document Extraction)",
        "allowed_file_types": sorted(list(ALLOWED_SUFFIXES)),
        "supported_document_types": ["CONTRACT", "SPK", "SPH", "BAST", "AUTO"],
        "ollama_config": {
            "model": config.OLLAMA_MODEL,
            "context_window": config.OLLAMA_NUM_CTX,
            "base_url": config.OLLAMA_BASE_URL,
            "temperature": config.OLLAMA_TEMPERATURE,
        },
        "parser_config": {
            "ocr_engine_default": config.OCR_ENGINE,
            "ocr_engine_choices": ["rapidocr", "mac", "tesseract", "easyocr", "paddle", "auto"],
            "ocr_lang": config.OCR_LANG,
            "default_dpi": config.DEFAULT_DPI,
            "scanned_char_threshold": config.SCANNED_CHAR_THRESHOLD,
        },
        "nocodb": {
            "url": config.NOCODB_URL,
            "push_enabled": config.NOCODB_PUSH_ENABLED,
            "token_configured": bool(config.NOCODB_API_TOKEN),
            "tables_mapped": sorted(config.nocodb_table_ids()),
        },
    }


@app.post(
    "/api/v1/parse",
    tags=["Document Parsing"],
    summary="Parse dokumen ke format LandingAI Compatible Markdown & JSON IR",
    description="Mengekstrak layout, tabel, dan struktur teks dari file PDF/DOCX/Gambar menggunakan Docling / PaddleOCR.",
)
def parse_document_endpoint(
    file: UploadFile = File(..., description="File PDF/DOCX/Gambar dokumen yang ingin diproses"),
    max_pages: int = Form(None, description="Batas maksimal halaman (opsional)"),
    ocr: str = Form(None, description="Mesin OCR: 'rapidocr' (default), 'mac', 'tesseract', 'easyocr', atau 'paddle'"),
):
    temp_path = _save_upload(file)
    try:
        with _engine_lock:
            return engine.parse(str(temp_path), max_pages=max_pages, ocr=ocr).model_dump()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _cleanup(temp_path)


@app.post(
    "/api/v1/extract",
    tags=["Document Extraction"],
    summary="Ekstraksi data terstruktur dari teks Markdown",
    description="Mengekstrak field dan tabel menggunakan model LLM Ollama (Qwen 2.5) dengan skema Pydantic.",
)
def extract_document_endpoint(
    markdown_text: str = Form(..., description="Teks hasil parsing dokumen dalam format Markdown"),
    doc_type: str = Form("auto", description="Tipe dokumen: 'auto', 'contract', 'sph', atau 'bast'"),
):
    try:
        with _engine_lock:
            extracted, resolved_type = engine.extract(markdown_text, doc_type=doc_type)
        return {"document_type": resolved_type, "data": extracted.model_dump(by_alias=True)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/api/v1/process-all",
    tags=["End-to-End Pipeline"],
    summary="Proses End-to-End Dokumen (Parse + Extract + Validate + Evidence Grounding)",
    description="Menjalankan seluruh pipeline: Ingestion/Profiling, Layout Parsing, LLM Extraction, Business Rule Validation, dan Visual Grounding.",
)
def process_full_endpoint(
    file: UploadFile = File(..., description="File PDF/DOCX/Gambar yang ingin diproses"),
    doc_type: str = Form("auto", description="Tipe dokumen: 'auto', 'contract', 'sph', atau 'bast'"),
    max_pages: int = Form(None, description="Batas maksimal halaman (opsional)"),
    ocr: str = Form(None, description="Mesin OCR: 'rapidocr' (default), 'mac', 'tesseract', 'easyocr', atau 'paddle'"),
    push_to_nocodb: bool = Form(False, description="Kirim hasil ke NocoDB. Default mati: pada arsitektur briefing, n8n yang mengorkestrasi push."),
):
    temp_path = _save_upload(file)
    try:
        with _engine_lock:
            result = engine.process_full(str(temp_path), doc_type=doc_type, max_pages=max_pages, ocr=ocr)
        # Payload NocoDB SELALU disertakan di respons. Pada arsitektur briefing (hlm. 8)
        # n8n yang mem-POST-nya ke NocoDB; push langsung di bawah hanya jalur alternatif.
        nocodb_payload = build_nocodb_payload({
            "document_name": Path(result["files"]["extract_json"]).stem.replace(".extract", ""),
            "document_type": result["document_type"],
            "data": result["extracted"].model_dump(by_alias=True),
            "validation": result["validation"].model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in result["evidence"]],
            "quality_report": result["quality_report"],
            "run_info": result["run_info"],
            "markdown": result["parsed"].markdown,
        })

        nocodb_push = None
        if push_to_nocodb:
            if not config.NOCODB_PUSH_ENABLED:
                raise HTTPException(
                    status_code=409,
                    detail="push_to_nocodb diminta tapi NOCODB_PUSH_ENABLED=false. "
                           "Aktifkan env NOCODB_PUSH_ENABLED=1 secara sadar sebelum menulis ke NocoDB.",
                )
            from app.services.nocodb_client import NocoDBClient, NocoDBError
            try:
                nocodb_push = NocoDBClient().push_payload(nocodb_payload)
            except NocoDBError as e:
                raise HTTPException(status_code=502, detail=str(e))

        return {
            "status": "success",
            "document_type": result["document_type"],
            "extracted_data": result["extracted"].model_dump(by_alias=True),
            "validation": result["validation"].model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in result["evidence"]],
            "quality_report": result["quality_report"],
            "run_info": result["run_info"],
            "metadata": result["parsed"].metadata.model_dump(),
            "files": result["files"],
            "nocodb_payload": nocodb_payload,
            "nocodb_push": nocodb_push,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _cleanup(temp_path)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_pdf = sys.argv[1]
        doc_type = sys.argv[2] if len(sys.argv) > 2 else "auto"
        parser_engine = sys.argv[3] if len(sys.argv) > 3 else config.DEFAULT_PARSER
        print(f"🚀 Open ADE: {target_pdf} (Parser: {parser_engine.upper()}, Type: {doc_type.upper()})")
        res = engine.process_full(target_pdf, doc_type=doc_type, parser=parser_engine)
        print(json.dumps(res["extracted"].model_dump(by_alias=True), indent=2, ensure_ascii=False))
    else:
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=os.getenv("UVICORN_RELOAD", "0") == "1")
