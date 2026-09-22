#!/usr/bin/env python3
"""
Open ADE — Uji OCR murni: apakah string penting benar-benar TERBACA di teks hasil OCR?

Kenapa perlu terpisah dari skor field: skor field mengukur OCR + LLM sekaligus. Kalau
angka salah, tidak ketahuan itu salah baca OCR atau salah pungut LLM. Di sini yang dicek
hanya teks mentah hasil parse -- LLM belum terlibat sama sekali.

String yang dicari diambil dari dokumen aslinya (lihat eval/golden/), dan sengaja yang
sulit: nomor kontrak berformat aneh, angka rupiah berpemisah titik, dan singkatan.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from bench_ocr import BENCH, DOCS, ENGINES     # noqa: E402

# (label, beberapa bentuk yang sama-sama dianggap benar)
PROBE = {
    "kl": [
        ("Grandtotal 306.180.000", ["306.180.000", "306180000"]),
        ("Subtotal/bln 25.515.000", ["25.515.000", "25515000"]),
        ("SSL total 7.560.000",     ["7.560.000", "7560000"]),
        ("Fortigate 249.480.000",   ["249.480.000", "249480000"]),
        ("singkatan APJII",         ["APJII"]),
        ("No. kontrak K.TEL.010222", ["K.TEL.010222", "K.TEL010222", "KTEL.010222"]),
        ("No. internal 46/00/HK-08/BUT/2025", ["46/00/HK-08/BUT/2025"]),
        ("NPWP BUT 0211.1642.9944.1000", ["0211.1642.9944.1000"]),
        ("Rekening 131-00-8888818-7", ["131-00-8888818-7", "131008888818 7", "131-00-8888818"]),
        ("Penandatangan NOFRIL",    ["NOFRIL"]),
    ],
    "spk": [
        ("Total 174.825.000",   ["174.825.000", "174825000"]),
        ("Subtotal 157.500.000", ["157.500.000", "157500000"]),
        ("PPN 17.325.000",      ["17.325.000", "17325000"]),
        ("No. SPK 687/AST11/AST-SET/2026", ["687/AST11/AST-SET/2026"]),
        ("Rekening 131.00.8888818.7", ["131.00.8888818.7", "131.00.8888818-7"]),
        ("CSI 21235270",        ["21235270"]),
        ("Service No 15370065", ["15370065"]),
    ],
    "np": [
        ("Total 23.976.000",  ["23.976.000", "23976000"]),
        ("Satuan 2.997.000",  ["2.997.000", "2997000"]),
        ("No. NP K.TEL.030304", ["K.TEL.030304", "K.TEL030304"]),
        ("Rekening 1310088888187", ["1310088888187"]),
        ("Penandatangan NOFRIL", ["NOFRIL"]),
        ("Cpanel Premier 100 akun", ["Premier 100"]),
    ],
}


def _bersih(s: str) -> str:
    """Samakan spasi saja. Titik/koma TIDAK dibuang -- justru di situ OCR sering salah."""
    return re.sub(r"[ \t]+", " ", s)


def _telanjang(s: str) -> str:
    """Buang SEMUA yang bukan huruf/angka. Dipakai untuk tingkat 'meleset tipis'."""
    return re.sub(r"[^0-9A-Za-z]+", "", s).upper()


def cek(engine: str, doc_key: str):
    md = BENCH / engine / "parsing" / f"{Path(DOCS[doc_key]).stem}.parse.md"
    if not md.exists():
        return None
    mentah = md.read_text(encoding="utf-8")
    teks = _bersih(mentah)
    # Penting: ditelanjangi PER BARIS, bukan sekaligus seluruh dokumen. Kalau seluruh
    # dokumen dijadikan satu, dua sel tabel bersebelahan menyatu ("7.560" + "000" jadi
    # "7560000") dan mesin OCR dapat nilai yang tidak pernah ia baca.
    baris_telanjang = [_telanjang(b) for b in mentah.splitlines()]
    hasil = []
    for label, bentuk in PROBE[doc_key]:
        if any(_bersih(b) in teks for b in bentuk):
            nilai = "persis"
        elif any(_telanjang(b) in ln for b in bentuk for ln in baris_telanjang):
            # Semua huruf/angkanya benar, hanya titik/spasi/hubung yang beda. Dibedakan
            # karena akibatnya beda: ini biasanya masih bisa dipulihkan di tahap
            # berikutnya, sedangkan digit yang salah baca tidak bisa.
            nilai = "tipis"
        else:
            nilai = "gagal"
        hasil.append((label, nilai))
    return hasil


def main() -> None:
    for doc_key in DOCS:
        ada = {e: cek(e, doc_key) for e in ENGINES}
        ada = {e: v for e, v in ada.items() if v}
        if not ada:
            continue
        print(f"\n=== {DOCS[doc_key]} ===")
        lebar = max(len(l) for l, _ in PROBE[doc_key])
        print(f"{'string kunci':{lebar}} " + " ".join(f"{e:>10}" for e in ada))
        lambang = {"persis": "✅", "tipis": "〜", "gagal": "❌"}
        for i, (label, _) in enumerate(PROBE[doc_key]):
            sel = " ".join(f"{lambang[ada[e][i][1]]:>9}" for e in ada)
            print(f"{label:{lebar}} {sel}")
        n = len(PROBE[doc_key])
        print(f"{'SKOR persis':{lebar}} " + " ".join(
            f"{str(sum(x[1] == 'persis' for x in ada[e]))+'/'+str(n):>9}" for e in ada))
        print(f"{'+ meleset tipis':{lebar}} " + " ".join(
            f"{str(sum(x[1] != 'gagal' for x in ada[e]))+'/'+str(n):>9}" for e in ada))
    print("\n✅ terbaca persis   〜 huruf/angka benar, tanda baca beda   ❌ salah baca")


if __name__ == "__main__":
    main()
