#!/usr/bin/env python3
"""
Open ADE — utilitas payload NocoDB.

    python nocodb_export.py --schema
        Tulis storage/outputs/nocodb_schema.json — definisi tabel + tipe kolom, dipakai
        SEKALI untuk membuat base NocoDB. Skema dibangun dari gabungan seluruh
        *.extract.json yang ada, supaya tidak ada kolom yang terlewat hanya karena satu
        dokumen contoh kebetulan tidak memuatnya.

    python nocodb_export.py --backfill
        Buat ulang *.nocodb.json untuk semua hasil ekstraksi yang sudah ada,
        tanpa menjalankan parser/LLM lagi.

    python nocodb_export.py --check
        Verifikasi tiap nilai cocok dengan tipe kolomnya. Jalankan ini setiap kali
        exporter diubah — kolom bertipe salah gagal masuk NocoDB tanpa pesan yang jelas.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.exporters.nocodb import build_nocodb_payload, build_nocodb_schema  # noqa: E402

# Sumber: hasil ekstraksi lengkap (berisi evidence + bounding box).
EXTRACTION_DIR = ROOT / "storage" / "outputs" / "extraction"
# Tujuan: turunan datar untuk NocoDB, dipisah agar tidak tercampur dengan sumbernya.
NOCODB_DIR = ROOT / "storage" / "outputs" / "nocodb"
SCHEMA_FILE = NOCODB_DIR / "_schema.json"

_OK_TYPES = {
    "Decimal": (int, float), "Number": (int, float),
    "Checkbox": (bool,), "SingleLineText": (str,), "LongText": (str,), "DateTime": (str,),
}


def _extractions():
    return sorted(EXTRACTION_DIR.glob("*.extract.json"))


def _merged_payload():
    """Gabungan seluruh dokumen — agar skema memuat kolom dari contract, sph, dan bast."""
    merged = {}
    for path in _extractions():
        try:
            payload = build_nocodb_payload(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"  ⚠️  dilewati {path.name}: {e}")
            continue
        for table, rows in payload.items():
            merged.setdefault(table, []).extend(rows)
    return merged


def cmd_schema() -> None:
    merged = _merged_payload()
    schema = build_nocodb_schema(merged)
    NOCODB_DIR.mkdir(parents=True, exist_ok=True)
    SCHEMA_FILE.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✅ {SCHEMA_FILE}")
    for table in schema["tables"]:
        print(f"   {table['table_name']:24} pk={table['primary_key']:18} {len(table['columns'])} kolom")


def cmd_backfill() -> None:
    for path in _extractions():
        try:
            payload = build_nocodb_payload(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"  ⚠️  {path.name}: {e}")
            continue
        NOCODB_DIR.mkdir(parents=True, exist_ok=True)
        out = NOCODB_DIR / path.name.replace(".extract.json", ".nocodb.json")
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  ✅ {out.name}  ({sum(len(r) for r in payload.values())} baris)")


def cmd_check() -> int:
    merged = _merged_payload()
    types = {t["table_name"]: {c["column_name"]: c["uidt"] for c in t["columns"]}
             for t in build_nocodb_schema(merged)["tables"]}
    problems = []
    for table, rows in merged.items():
        for row in rows:
            for key, value in row.items():
                if value is None:
                    continue
                uidt = types[table][key]
                expected = _OK_TYPES[uidt]
                # bool adalah subclass int di Python — jangan sampai lolos sebagai angka
                if isinstance(value, bool) != (uidt == "Checkbox") or not isinstance(value, expected):
                    problems.append(f"{table}.{key}: tipe {uidt} tapi nilainya "
                                    f"{type(value).__name__} ({value!r:.40})")
    if problems:
        print("❌ Tipe kolom tidak cocok dengan nilainya:")
        for p in sorted(set(problems)):
            print("   ", p)
        return 1
    total = sum(len(r) for r in merged.values())
    print(f"✅ {total} baris dari {len(_extractions())} dokumen, semua nilai cocok dengan tipe kolomnya")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schema", action="store_true", help="tulis definisi tabel NocoDB")
    parser.add_argument("--backfill", action="store_true", help="buat ulang semua *.nocodb.json")
    parser.add_argument("--check", action="store_true", help="verifikasi tipe kolom vs nilai")
    args = parser.parse_args()

    if not (args.schema or args.backfill or args.check):
        parser.print_help()
        return
    if args.schema:
        cmd_schema()
    if args.backfill:
        cmd_backfill()
    if args.check:
        sys.exit(cmd_check())


if __name__ == "__main__":
    main()
