#!/usr/bin/env python3
"""
Open ADE — CLI model data companion (BAST lebih dulu).

    python companion.py --ddl                  # cetak schema.sql PostgreSQL
    python companion.py --map FILE.extract.json    # hasilkan payload companion
    python companion.py --check FILE.companion.json # validasi terhadap model
    python companion.py --tables               # ringkas tabel & kolom
    python companion.py --push FILE.companion.json --dry-run   # rencana kirim ke NocoDB
    python companion.py --push FILE.companion.json             # kirim sungguhan

Lihat docs/SKEMA_BAST_NOCODB.md (model) dan docs/RENCANA_IMPLEMENTASI_COMPANION.md (rencana).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.companion.bast_mapper import file_content_hash, map_bast  # noqa: E402
from app.companion.ddl import generate_ddl              # noqa: E402
from app.companion.model import ALL_TABLES, insert_order  # noqa: E402
from app.companion.validate import validate_payload     # noqa: E402

OUT_DIR = ROOT / "storage" / "outputs" / "companion"


def cmd_ddl(write: bool) -> int:
    ddl = generate_ddl()
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / "schema.sql"
        path.write_text(ddl, encoding="utf-8")
        print(f"✅ {path}  ({len(ddl.splitlines())} baris)")
    else:
        print(ddl)
    return 0


def cmd_tables() -> int:
    print(f"{len(ALL_TABLES)} tabel — urutan insert: {' -> '.join(insert_order())}\n")
    for t in ALL_TABLES:
        penulis = "MANUSIA" if t.written_by == "human" else "mesin"
        print(f"  {t.name:18} {len(t.columns):2d} kolom   ditulis: {penulis}")
    return 0


def _find_source_pdf(document_name: str) -> Path | None:
    """Cari berkas asli untuk dihitung sha1-nya. Dicari, bukan diminta, supaya perintahnya
    tetap satu argumen; kalau tidak ketemu kita bilang dan pakai cadangan."""
    if not document_name:
        return None
    for folder in (ROOT / "sample_pdfs", ROOT / "storage" / "uploads"):
        if not folder.exists():
            continue
        hit = next((p for p in folder.rglob(document_name) if p.is_file()), None)
        if hit:
            return hit
    return None


def cmd_map(path: Path, pdf: str | None = None) -> int:
    result = json.loads(path.read_text(encoding="utf-8"))
    if (result.get("document_type") or "").lower() != "bast":
        print(f"❌ {path.name}: document_type='{result.get('document_type')}', "
              f"pemeta ini baru mendukung BAST.")
        return 1
    md = ROOT / "storage" / "outputs" / "parsing" / path.name.replace(".extract.json", ".parse.md")
    if md.exists() and not result.get("markdown"):
        result["markdown"] = md.read_text(encoding="utf-8")

    src = Path(pdf) if pdf else _find_source_pdf(result.get("document_name") or "")
    if src and src.exists():
        content_hash = file_content_hash(src)
        print(f"  kunci idempotensi : {content_hash[:19]}…  (sha1 isi {src.name})")
    else:
        content_hash = None
        print(f"  kunci idempotensi : run_info.document_id (cadangan — berkas asli "
              f"'{result.get('document_name')}' tidak ditemukan; hash ini ikut berubah "
              f"kalau OCR diganti)")

    payload = map_bast(result, content_hash=content_hash)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / path.name.replace(".extract.json", ".companion.json")
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    for t, rows in payload.items():
        print(f"  {t:18} {len(rows):3d} baris")
    print(f"\n✅ {out}")
    problems = validate_payload(payload)
    print(("⚠️  " + str(len(problems)) + " masalah — jalankan --check untuk detail")
          if problems else "✅ payload lolos validasi")
    return 0


def cmd_check(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_payload(payload)
    if not problems:
        total = sum(len(v) for v in payload.values())
        print(f"✅ {total} baris / {len(payload)} tabel — semua sesuai model")
        return 0
    print(f"❌ {len(problems)} masalah:")
    for p in problems:
        print(f"   {p}")
    return 1


def cmd_push(path: Path, dry_run: bool) -> int:
    from app.companion.nocodb_push import CompanionPushError, CompanionPusher
    from app.config import config

    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        pusher = CompanionPusher(config.NOCODB_URL, config.NOCODB_API_TOKEN,
                                 config.nocodb_table_ids())
        hasil = pusher.push(payload, dry_run=dry_run)
    except CompanionPushError as e:
        print(f"❌ {e}")
        return 1

    if dry_run:
        print(f"RENCANA (tidak ada yang dikirim) — {config.NOCODB_URL}\n")
        for t in hasil["urutan"]:
            print(f"  {t:18} {hasil['baris'][t]:3d} baris   {hasil['strategi'][t]}")
        print("\n  field_review        —       tidak disentuh (milik PM)")
        return 0
    for t, s in hasil["tables"].items():
        print(f"  {t:18} {s}")
    print(f"\n✅ {hasil['inserted']} baris baru, {hasil['updated']} diperbarui")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ddl", action="store_true", help="cetak DDL PostgreSQL")
    ap.add_argument("--write", action="store_true", help="dengan --ddl: simpan ke file")
    ap.add_argument("--tables", action="store_true", help="ringkas tabel")
    ap.add_argument("--map", metavar="FILE", help="petakan *.extract.json BAST")
    ap.add_argument("--check", metavar="FILE", help="validasi *.companion.json")
    ap.add_argument("--pdf", metavar="PATH", help="berkas asli untuk kunci idempotensi")
    ap.add_argument("--push", metavar="FILE", help="kirim *.companion.json ke NocoDB")
    ap.add_argument("--dry-run", action="store_true", help="dengan --push: hanya rencana")
    a = ap.parse_args()
    if a.ddl:     sys.exit(cmd_ddl(a.write))
    if a.tables:  sys.exit(cmd_tables())
    if a.map:     sys.exit(cmd_map(Path(a.map), a.pdf))
    if a.check:   sys.exit(cmd_check(Path(a.check)))
    if a.push:    sys.exit(cmd_push(Path(a.push), a.dry_run))
    ap.print_help()


if __name__ == "__main__":
    main()
