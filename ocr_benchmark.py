#!/usr/bin/env python3
"""
Open ADE — benchmark engine OCR.

    python ocr_benchmark.py                       # dokumen & engine default
    python ocr_benchmark.py --engines mac,rapidocr,tesseract
    python ocr_benchmark.py --docs "sample_pdfs/KL FULL SIGNED.pdf"

Hanya menjalankan tahap PARSING — tanpa LLM, tanpa ekstraksi. Tujuannya membandingkan
kualitas pembacaan huruf, bukan kualitas ekstraksi. Refiner LLM dimatikan supaya tidak
menutupi perbedaan antar engine.

Kenapa ini perlu: seluruh angka kualitas yang kita punya dihasilkan Apple Vision, yang
hanya ada di macOS. Tanpa benchmark, kita tidak tahu apakah sistem ini bagus karena
rancangannya atau karena kebetulan berjalan di Mac.

Metrik yang dipakai dan alasannya:
  chars       jumlah karakter terbaca. Turun drastis = banyak teks hilang.
  tables      jumlah tabel terbentuk. Tabel harga adalah inti dokumen ini.
  headings    jumlah judul section terdeteksi (dipakai LLM untuk menemukan pasal).
  numbers     jumlah pola nominal rupiah. Salah baca angka = kerugian nyata.
  garble      % token huruf sepanjang 1-2 karakter. Bahasa Indonesia hampir tidak punya
              kata sependek itu, jadi nilai tinggi = teks hancur ("e ean eaan ean an ea").
              Metrik ini ditambahkan setelah RapidOCR lolos semua metrik lain padahal
              satu pasal terbaca berantakan -- jumlah karakter bisa utuh walau isinya
              tidak terbaca manusia.
  sim_vs_ref  kemiripan teks terhadap engine referensi (default: mac).
"""
import argparse
import difflib
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Refiner LLM dimatikan SEBELUM app.config di-import: kalau aktif, ia memperbaiki teks
# rusak dan justru menyamarkan engine mana yang sebenarnya membaca lebih buruk.
os.environ["ENABLE_LLM_MARKDOWN_REFINER"] = "0"

DEFAULT_DOCS = [
    "sample_pdfs/KL FULL SIGNED.pdf",
    "sample_pdfs/Data Project BUT/SPH Bapenda Jabar 2026.pdf",
]
DEFAULT_ENGINES = ["mac", "rapidocr", "paddle"]

_MONEY = re.compile(r"\b\d{1,3}(?:[.,]\d{3}){1,}\b")
_WORD = re.compile(r"[A-Za-z]+")


def garble_ratio(markdown: str) -> float:
    """% token huruf sepanjang <=2. Tinggi = OCR pecah jadi potongan tak terbaca."""
    words = _WORD.findall(markdown)
    if not words:
        return 0.0
    return 100.0 * sum(1 for w in words if len(w) <= 2) / len(words)


def measure(markdown: str) -> dict:
    return {
        "chars": len(markdown),
        "tables": markdown.count("|---") + markdown.count("|:--"),
        "headings": len(re.findall(r"^##\s", markdown, re.M)),
        "numbers": len(_MONEY.findall(markdown)),
        "garble": garble_ratio(markdown),
    }


def run_one(pdf: Path, engine: str) -> dict:
    """Satu dokumen, satu engine. Proses terpisah tidak perlu: converter di-cache per instance."""
    os.environ["OCR_ENGINE"] = engine
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.parsers.docling_parser as dp
    importlib.reload(dp)
    from app.ingestion.profiler import profile_document

    profile = profile_document(str(pdf))
    started = time.time()
    if engine == "paddle":
        # PaddleOCR bukan opsi OCR di dalam Docling, melainkan jalur parser tersendiri
        # (PP-Structure + SLANet). Jadi yang dibandingkan di sini adalah dua pipeline utuh,
        # bukan sekadar dua mesin pembaca huruf.
        from app.parsers.paddle_parser import PaddleOCRParser
        parsed = PaddleOCRParser().parse(str(pdf))
    else:
        parsed = dp.DoclingParser().parse(str(pdf), do_ocr=profile.needs_ocr)
    elapsed = time.time() - started
    return {"engine": engine, "seconds": elapsed, "markdown": parsed.markdown,
            "pages": parsed.metadata.page_count, **measure(parsed.markdown)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", default=",".join(DEFAULT_DOCS))
    ap.add_argument("--engines", default=",".join(DEFAULT_ENGINES))
    ap.add_argument("--ref", default="mac", help="engine pembanding untuk sim_vs_ref")
    ap.add_argument("--out", default="storage/outputs/ocr_benchmark", help="folder simpan markdown tiap engine")
    args = ap.parse_args()

    docs = [Path(d.strip()) for d in args.docs.split(",") if d.strip()]
    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    for pdf in docs:
        if not pdf.exists():
            print(f"❌ tidak ditemukan: {pdf}")
            continue
        print(f"\n{'=' * 92}\n📄 {pdf.name}\n{'=' * 92}")
        results = {}
        for engine in engines:
            try:
                r = run_one(pdf, engine)
            except Exception as e:
                print(f"  {engine:12} GAGAL: {type(e).__name__}: {str(e)[:70]}")
                continue
            results[engine] = r
            (out_dir / f"{pdf.stem}.{engine}.md").write_text(r["markdown"], encoding="utf-8")

        ref = results.get(args.ref)
        print(f"  {'engine':12} {'detik':>7} {'chars':>8} {'tabel':>6} {'judul':>6} {'nominal':>8} {'garble%':>8} {'sim_vs_' + args.ref:>12}")
        for engine, r in results.items():
            if ref and engine != args.ref:
                sim = difflib.SequenceMatcher(None, ref["markdown"], r["markdown"]).quick_ratio()
                sim_s = f"{sim:.3f}"
            else:
                sim_s = "—" if ref else "n/a"
            print(f"  {engine:12} {r['seconds']:7.1f} {r['chars']:8d} {r['tables']:6d} "
                  f"{r['headings']:6d} {r['numbers']:8d} {r['garble']:8.1f} {sim_s:>12}")
    print(f"\n💾 Markdown tiap engine disimpan di {out_dir} — bandingkan manual untuk menilai kualitas.")


if __name__ == "__main__":
    main()
