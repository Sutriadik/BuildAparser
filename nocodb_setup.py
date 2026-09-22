#!/usr/bin/env python3
"""
Open ADE — utilitas penyiapan & uji sambungan NocoDB.

    python nocodb_setup.py --list-tables
        Tampilkan seluruh base & tabel beserta tableId-nya, siap disalin ke
        env NOCODB_TABLE_IDS. NocoDB v2 memakai id acak (mis. "m1a2b3c4d5"),
        bukan nama tabel, jadi id ini tidak bisa ditebak.

    python nocodb_setup.py --check
        Periksa satu per satu: URL terjangkau, token valid, dan setiap tableId di
        NOCODB_TABLE_IDS benar-benar ada. Menyebutkan yang mana yang gagal --
        bukan gagal diam-diam.

    python nocodb_setup.py --push <file.nocodb.json>
        Kirim satu payload ke NocoDB tanpa menjalankan server FastAPI. Berguna
        untuk mencoba integrasi sebelum merangkai n8n.

Env yang dibaca: NOCODB_URL, NOCODB_API_TOKEN, NOCODB_TABLE_IDS.
Lihat docs/INTEGRASI_NOCODB.md untuk langkah lengkapnya.
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.companion.model import ALL_TABLES  # noqa: E402
from app.config import config  # noqa: E402
from app.exporters.nocodb import _PRIMARY_KEYS  # noqa: E402

# Dua model hidup berdampingan: exporter 13-tabel (kontrak & SPH) dan model companion
# (BAST). Keduanya harus dikenali di sini, kalau tidak tabel companion tampil tanpa tanda
# dan tidak ikut masuk ke NOCODB_TABLE_IDS -- push-nya lalu gagal dengan alasan yang
# menyesatkan ("tabel belum dipetakan"), padahal tabelnya ada.
_COMPANION_TABLES = {t.name for t in ALL_TABLES}


def _kunci(nama: str) -> str:
    """Label kunci upsert untuk ditampilkan. Model companion memakai kunci alami dari
    model.py, bukan `document_id` seperti exporter lama."""
    if nama in _COMPANION_TABLES:
        from app.companion.model import table
        from app.companion.nocodb_push import APPEND_ONLY, natural_key
        if nama in APPEND_ONLY:
            return "tambah-saja"
        k = natural_key(table(nama))
        return "+".join(k) if k else "ganti-anak"
    return _PRIMARY_KEYS.get(nama, "document_id")


def _headers() -> dict:
    return {"xc-token": config.NOCODB_API_TOKEN, "Content-Type": "application/json"}


def _require_token() -> bool:
    if not config.NOCODB_API_TOKEN:
        print("❌ NOCODB_API_TOKEN kosong.")
        print("   Ambil di NocoDB: foto profil > Account Settings > Tokens > Add New Token")
        print('   Lalu: export NOCODB_API_TOKEN="<token>"')
        return False
    return True


def cmd_list_tables() -> int:
    """Telusuri base -> tabel, tampilkan id-nya dalam format siap salin."""
    if not _require_token():
        return 1
    base_url = config.NOCODB_URL.rstrip("/")
    try:
        with httpx.Client(timeout=15.0) as client:
            bases = client.get(f"{base_url}/api/v2/meta/bases", headers=_headers())
            bases.raise_for_status()
            base_list = bases.json().get("list", [])
            if not base_list:
                print("⚠️  Tidak ada base. Buat dulu satu Base di NocoDB.")
                return 1

            pairs = []
            for base in base_list:
                print(f"\n📁 Base: {base.get('title')}  (id={base.get('id')})")
                tables = client.get(f"{base_url}/api/v2/meta/bases/{base['id']}/tables",
                                    headers=_headers())
                tables.raise_for_status()
                for t in tables.json().get("list", []):
                    title, tid = t.get("title"), t.get("id")
                    if title in _COMPANION_TABLES:
                        dikenal = "✅ companion"
                    elif title in _PRIMARY_KEYS:
                        dikenal = "✅ exporter  "
                    else:
                        dikenal = "            "
                    print(f"   {dikenal} {title:26} {tid}")
                    if title in _COMPANION_TABLES or title in _PRIMARY_KEYS:
                        pairs.append(f"{title}:{tid}")

            if pairs:
                print("\n📋 Salin baris ini:\n")
                print(f'export NOCODB_TABLE_IDS="{",".join(pairs)}"')
            else:
                print("\n⚠️  Belum ada tabel yang namanya cocok dengan payload kita.")
                print("   BAST  : docs/SETUP_NOCODB_COMPANION.md")
                print("   lainnya: python nocodb_export.py --schema")
    except httpx.HTTPError as e:
        print(f"❌ Gagal menghubungi NocoDB di {base_url}: {e}")
        return 1
    return 0


def cmd_check() -> int:
    """Uji setiap prasyarat secara terpisah supaya jelas mana yang gagal."""
    base_url = config.NOCODB_URL.rstrip("/")
    print(f"NOCODB_URL   : {base_url}")

    try:
        with httpx.Client(timeout=10.0) as client:
            client.get(f"{base_url}/api/v1/health")
        print("  ✅ server terjangkau")
    except httpx.HTTPError as e:
        print(f"  ❌ tidak terjangkau: {e}")
        return 1

    if not _require_token():
        return 1
    print(f"TOKEN        : ✅ terpasang ({len(config.NOCODB_API_TOKEN)} karakter)")

    table_ids = config.nocodb_table_ids()
    if not table_ids:
        print("TABEL        : ❌ NOCODB_TABLE_IDS kosong")
        print("   Jalankan dulu: python nocodb_setup.py --list-tables")
        return 1

    print(f"TABEL        : {len(table_ids)} dipetakan")
    gagal = 0
    with httpx.Client(timeout=15.0) as client:
        for name, tid in sorted(table_ids.items()):
            try:
                r = client.get(f"{base_url}/api/v2/tables/{tid}/records",
                               headers=_headers(), params={"limit": 1})
                r.raise_for_status()
                jumlah = r.json().get("pageInfo", {}).get("totalRows", "?")
                pk = _kunci(name)
                print(f"  ✅ {name:26} {tid}  ({jumlah} baris, kunci: {pk})")
            except httpx.HTTPError as e:
                print(f"  ❌ {name:26} {tid}  -> {e}")
                gagal += 1
    if gagal:
        print(f"\n{gagal} tabel bermasalah. Cek ulang tableId-nya lewat --list-tables.")
        return 1
    print("\n✅ Semua siap. Push bisa dijalankan.")
    return 0


def cmd_push(path: Path) -> int:
    if not path.exists():
        print(f"❌ File tidak ditemukan: {path}")
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    from app.services.nocodb_client import NocoDBClient, NocoDBError
    try:
        hasil = NocoDBClient().push_payload(payload)
    except NocoDBError as e:
        print(f"❌ {e}")
        return 1
    for table, info in sorted(hasil["tables"].items()):
        print(f"  {table:26} {info}")
    print(f"\n✅ {hasil['inserted']} baris baru, {hasil['updated']} diperbarui")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-tables", action="store_true", help="tampilkan base & tableId")
    ap.add_argument("--check", action="store_true", help="uji sambungan & pemetaan tabel")
    ap.add_argument("--push", metavar="FILE", help="kirim satu file *.nocodb.json")
    args = ap.parse_args()

    if args.list_tables:
        sys.exit(cmd_list_tables())
    if args.check:
        sys.exit(cmd_check())
    if args.push:
        sys.exit(cmd_push(Path(args.push)))
    ap.print_help()


if __name__ == "__main__":
    main()
