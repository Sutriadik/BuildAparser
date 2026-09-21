#!/usr/bin/env python3
"""Bandingkan pipeline penuh (parse+extract) untuk KL FULL SIGNED per engine, tanpa
menimpa baseline Apple Vision yang sudah tersimpan di storage/outputs/."""
import json
import os
import sys
import time
from pathlib import Path

engine_key = sys.argv[1]  # "mac" | "rapidocr" | "paddle"
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "storage" / "outputs" / "_ocr_compare" / engine_key
OUT.mkdir(parents=True, exist_ok=True)

if engine_key == "paddle":
    os.environ["DEFAULT_PARSER"] = "paddle"
else:
    os.environ["DEFAULT_PARSER"] = "docling"
    os.environ["OCR_ENGINE"] = engine_key
os.environ.setdefault("OLLAMA_NUM_CTX", "16384")
os.environ.setdefault("OLLAMA_KEEP_ALIVE", "30m")

sys.path.insert(0, str(ROOT))
from app.services.engine import OpenADEEngine  # noqa: E402

pdf = str(ROOT / "sample_pdfs" / "KL FULL SIGNED.pdf")
t0 = time.time()
result = OpenADEEngine().process_full(pdf, output_dir=str(OUT))
elapsed = time.time() - t0

r = result["run_info"]
q = json.loads(Path(result["files"]["extract_json"]).read_text())["quality_report"]
print(f"RESULT engine={engine_key:10} total={elapsed:7.1f}s parse={r['timings']['parse_s']:7.1f}s "
      f"extract={r['timings']['extract_s']:7.1f}s llm_calls={r['llm_calls']} "
      f"fill={q['filled_fields']}/{q['total_fields']} type={result['document_type']}")
