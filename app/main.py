import sys
import os
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path
import shutil

from app.config import config
from app.services.engine import OpenADEEngine

app = FastAPI(
    title="Open ADE - LandingAI Clone Document Parser & Extractor",
    description="Open-source Agentic Document Extraction API powered by Docling, PaddleOCR, PyMuPDF, and Ollama Qwen 2.5",
    version="1.0.0"
)

engine = OpenADEEngine()

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "Open ADE Document AI Service",
        "model": config.OLLAMA_MODEL,
        "engine": "Docling + PaddleOCR + Ollama"
    }

@app.post("/api/v1/parse")
async def parse_document_endpoint(
    file: UploadFile = File(...),
    max_pages: int = Form(None)
):
    """Stage 1: Parse Document (Layout, Tables, Text & Bounding Boxes)"""
    temp_path = config.TEMP_UPLOADS / file.filename
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        parsed_res = engine.parse(str(temp_path), max_pages=max_pages)
        return parsed_res.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path.exists():
            temp_path.unlink()

@app.post("/api/v1/extract")
async def extract_document_endpoint(
    markdown_text: str = Form(...),
    doc_type: str = Form("contract")
):
    """Stage 2: Schema Extraction (Pydantic + Ollama)"""
    try:
        extracted = engine.extract(markdown_text, doc_type=doc_type)
        return extracted.model_dump(by_alias=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/process-all")
async def process_full_endpoint(
    file: UploadFile = File(...),
    doc_type: str = Form("contract"),
    max_pages: int = Form(None)
):
    """End-to-End Pipeline: Parse + Extract + Export JSON"""
    temp_path = config.TEMP_UPLOADS / file.filename
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        result = engine.process_full(str(temp_path), doc_type=doc_type, max_pages=max_pages)
        return {
            "status": "success",
            "extracted_data": result["extracted"].model_dump(by_alias=True),
            "metadata": result["parsed"].metadata.model_dump(),
            "files": result["files"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path.exists():
            temp_path.unlink()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # CLI Mode
        target_pdf = sys.argv[1]
        doc_type = sys.argv[2] if len(sys.argv) > 2 else "contract"
        print(f"🚀 Menjalankan Open ADE Engine pada: {target_pdf}")
        res = engine.process_full(target_pdf, doc_type=doc_type)
        print("\n✨ Data Hasil Ekstraksi:")
        import json
        print(json.dumps(res["extracted"].model_dump(by_alias=True), indent=2, ensure_ascii=False))
    else:
        # Run Server
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
