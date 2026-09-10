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
    max_pages: int = Form(None),
    parser: str = Form("auto")
):
    """Stage 1: Parse Document (Layout, Tables, Text & Bounding Boxes via Docling/PaddleOCR)"""
    temp_path = config.TEMP_UPLOADS / file.filename
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        parsed_res = engine.parse(str(temp_path), max_pages=max_pages, parser=parser)
        return parsed_res.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if temp_path.exists():
            temp_path.unlink()

@app.post("/api/v1/extract")
async def extract_document_endpoint(
    markdown_text: str = Form(...),
    doc_type: str = Form("auto")
):
    """Stage 2: Schema Extraction (Pydantic + Ollama)"""
    try:
        extracted, resolved_type = engine.extract(markdown_text, doc_type=doc_type)
        return {
            "document_type": resolved_type,
            "data": extracted.model_dump(by_alias=True)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/process-all")
async def process_full_endpoint(
    file: UploadFile = File(...),
    doc_type: str = Form("auto"),
    max_pages: int = Form(None),
    parser: str = Form("auto")
):
    """End-to-End Pipeline: Parse (Docling/Paddle) + Extract + Export JSON"""
    temp_path = config.TEMP_UPLOADS / file.filename
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        result = engine.process_full(str(temp_path), doc_type=doc_type, max_pages=max_pages, parser=parser)
        return {
            "status": "success",
            "document_type": result["document_type"],
            "extracted_data": result["extracted"].model_dump(by_alias=True),
            "quality_report": result.get("quality_report", {}),
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
        doc_type = sys.argv[2] if len(sys.argv) > 2 else "auto"
        parser_engine = sys.argv[3] if len(sys.argv) > 3 else "auto"
        print(f"🚀 Menjalankan Open ADE Engine pada: {target_pdf} (Parser: {parser_engine.upper()}, Type: {doc_type.upper()})")
        res = engine.process_full(target_pdf, doc_type=doc_type, parser=parser_engine)
        print("\n✨ Data Hasil Ekstraksi:")
        import json
        print(json.dumps(res["extracted"].model_dump(by_alias=True), indent=2, ensure_ascii=False))
    else:
        # Run Server
        uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
