import json
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.config import config
from app.services.engine import OpenADEEngine

app = FastAPI(
    title="Open ADE - Document Parser & Extractor",
    description="Evidence-grounded extraction untuk Kontrak/SPK dan SPH (Docling, PaddleOCR, Ollama Qwen 2.5)",
    version="1.1.0",
)

# Engine memuat model secara lazy. Lock memastikan satu dokumen diproses dalam satu waktu:
# Docling/Paddle/Ollama lokal tidak aman dan tidak lebih cepat bila dipanggil paralel di satu mesin.
engine = OpenADEEngine()
_engine_lock = threading.Lock()
ALLOWED_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


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


@app.get("/")
def read_root():
    return {"status": "online", "service": "Open ADE Document AI Service", "model": config.OLLAMA_MODEL,
            "engine": "Docling + PaddleOCR + Ollama"}


# Endpoint sengaja `def` (bukan `async def`): FastAPI menjalankannya di threadpool sehingga
# parsing/LLM yang berjalan menit-an tidak memblokir event loop.
@app.post("/api/v1/parse")
def parse_document_endpoint(file: UploadFile = File(...), max_pages: int = Form(None), parser: str = Form("auto")):
    temp_path = _save_upload(file)
    try:
        with _engine_lock:
            return engine.parse(str(temp_path), max_pages=max_pages, parser=parser).model_dump()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _cleanup(temp_path)


@app.post("/api/v1/extract")
def extract_document_endpoint(markdown_text: str = Form(...), doc_type: str = Form("auto")):
    try:
        with _engine_lock:
            extracted, resolved_type = engine.extract(markdown_text, doc_type=doc_type)
        return {"document_type": resolved_type, "data": extracted.model_dump(by_alias=True)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/process-all")
def process_full_endpoint(file: UploadFile = File(...), doc_type: str = Form("auto"),
                          max_pages: int = Form(None), parser: str = Form("auto")):
    temp_path = _save_upload(file)
    try:
        with _engine_lock:
            result = engine.process_full(str(temp_path), doc_type=doc_type, max_pages=max_pages, parser=parser)
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
        parser_engine = sys.argv[3] if len(sys.argv) > 3 else "auto"
        print(f"🚀 Open ADE: {target_pdf} (Parser: {parser_engine.upper()}, Type: {doc_type.upper()})")
        res = engine.process_full(target_pdf, doc_type=doc_type, parser=parser_engine)
        print(json.dumps(res["extracted"].model_dump(by_alias=True), indent=2, ensure_ascii=False))
    else:
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=os.getenv("UVICORN_RELOAD", "0") == "1")
