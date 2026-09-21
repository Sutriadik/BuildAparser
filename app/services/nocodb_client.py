"""
Open ADE — Klien NocoDB (push hasil ekstraksi ke tabel companion).

Batas yang dijaga modul ini, mengikuti briefing Delivery Ops Layer:

- **Hanya menulis ke base kita sendiri** (hlm. 6-7). NocoDB adalah penyimpanan companion
  milik tim ini; MyBhakti tidak pernah disentuh dari sini, baik baca maupun tulis.
- **Upsert, bukan insert** (hlm. 20 "semua di repo" & idempotensi n8n). Dokumen yang sama
  diproses ulang harus MEMPERBARUI baris, bukan menggandakannya. Kunci upsert diambil dari
  `_PRIMARY_KEYS` di app/exporters/nocodb.py (`document_id` untuk tabel dokumen, `*_key`
  untuk tabel anak) -- semuanya stabil antar-run karena berasal dari sha1 isi dokumen.
- **Tidak pernah menimpa kolom milik manusia** (hlm. 11 & 20). `pm_confirmed`,
  `nilai_final`, `harga_terverifikasi`, `dikonfirmasi_*` dan `semua_field_terkonfirmasi`
  dibuang dari payload update pada baris yang SUDAH ADA -- kalau PM sudah mengunci sebuah
  field, menjalankan ulang parser tidak boleh mengembalikannya jadi "belum dikonfirmasi".
  Pada baris baru, kolom itu tetap dikirim dengan nilai awalnya (False/None).

Push bersifat OPSIONAL dan mati secara default: pada arsitektur briefing (hlm. 8) n8n yang
mengorkestrasi, jadi jalur normalnya adalah FastAPI mengembalikan payload dan n8n yang
mem-POST-nya. Push langsung disediakan untuk uji coba dan untuk deployment tanpa n8n.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from app.config import config
from app.exporters.nocodb import _PRIMARY_KEYS
from app.logger import logger

# Kolom yang HANYA boleh diisi manusia. Tidak pernah dikirim saat memperbarui baris lama.
HUMAN_OWNED_COLUMNS = {
    "pm_confirmed", "nilai_final", "dikonfirmasi_oleh", "dikonfirmasi_pada", "catatan_pm",
    "harga_terverifikasi", "diverifikasi_oleh", "semua_field_terkonfirmasi",
    "sudah_terpenuhi", "arah_bast", "acuan_kebenaran",
}


class NocoDBError(RuntimeError):
    pass


class NocoDBClient:
    """
    Pemetaan nama tabel -> tableId NocoDB diberikan lewat `table_ids`, bukan ditebak:
    NocoDB memakai id acak (mis. `m1a2b3c4d5`), bukan nama tabel, pada endpoint v2.
    """

    def __init__(self, base_url: Optional[str] = None, api_token: Optional[str] = None,
                 table_ids: Optional[Dict[str, str]] = None, timeout: float = 30.0):
        self.base_url = (base_url or config.NOCODB_URL).rstrip("/")
        self.api_token = api_token or config.NOCODB_API_TOKEN
        self.table_ids = table_ids or config.nocodb_table_ids()
        self.timeout = timeout
        if not self.api_token:
            raise NocoDBError(
                "NOCODB_API_TOKEN kosong. Isi env NOCODB_API_TOKEN dengan token NocoDB "
                "(Account Settings > Tokens) sebelum melakukan push."
            )

    # ------------------------------------------------------------------ HTTP dasar
    def _headers(self) -> Dict[str, str]:
        return {"xc-token": self.api_token, "Content-Type": "application/json"}

    def _url(self, table_id: str) -> str:
        return f"{self.base_url}/api/v2/tables/{table_id}/records"

    def _existing_keys(self, client: httpx.Client, table_id: str, pk: str,
                       values: List[str]) -> Dict[str, Any]:
        """
        Ambil baris yang sudah ada untuk daftar nilai kunci. Dipakai untuk memutuskan
        insert vs update, dan untuk mendapatkan `Id` internal NocoDB yang wajib ada
        pada operasi PATCH.
        """
        found: Dict[str, Any] = {}
        # NocoDB membatasi panjang query; pecah per 50 kunci.
        for i in range(0, len(values), 50):
            chunk = [v for v in values[i:i + 50] if v]
            if not chunk:
                continue
            where = "~or".join(f"({pk},eq,{v})" for v in chunk)
            try:
                resp = client.get(self._url(table_id), headers=self._headers(),
                                  params={"where": where, "limit": len(chunk)})
                resp.raise_for_status()
                for row in resp.json().get("list", []):
                    if row.get(pk) is not None:
                        found[str(row[pk])] = row
            except httpx.HTTPError as e:
                # Gagal membaca bukan alasan untuk menduplikasi data: lebih baik berhenti
                # dan laporkan daripada meng-insert baris kembar diam-diam.
                raise NocoDBError(f"Gagal membaca baris lama tabel '{table_id}': {e}") from e
        return found

    # ------------------------------------------------------------------ push
    def push_table(self, client: httpx.Client, table_name: str,
                   rows: List[Dict[str, Any]]) -> Dict[str, int]:
        table_id = self.table_ids.get(table_name)
        if not table_id:
            logger.warning(f"⏭️  Tabel '{table_name}' dilewati: tidak ada di NOCODB_TABLE_IDS")
            return {"skipped": len(rows)}
        if not rows:
            return {"inserted": 0, "updated": 0}

        pk = _PRIMARY_KEYS.get(table_name, "document_id")
        existing = self._existing_keys(client, table_id, pk, [str(r.get(pk)) for r in rows])

        to_insert, to_update = [], []
        for row in rows:
            key = str(row.get(pk))
            old = existing.get(key)
            if old is None:
                to_insert.append(row)
            else:
                # Baris lama: buang kolom milik manusia supaya konfirmasi PM tidak tertimpa.
                patch = {k: v for k, v in row.items() if k not in HUMAN_OWNED_COLUMNS}
                patch["Id"] = old.get("Id")
                to_update.append(patch)

        result = {"inserted": 0, "updated": 0}
        try:
            if to_insert:
                r = client.post(self._url(table_id), headers=self._headers(), json=to_insert)
                r.raise_for_status()
                result["inserted"] = len(to_insert)
            if to_update:
                r = client.patch(self._url(table_id), headers=self._headers(), json=to_update)
                r.raise_for_status()
                result["updated"] = len(to_update)
        except httpx.HTTPError as e:
            body = getattr(getattr(e, "response", None), "text", "")[:300]
            raise NocoDBError(f"Push tabel '{table_name}' gagal: {e} {body}") from e
        return result

    def push_payload(self, payload: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Kirim seluruh payload (hasil build_nocodb_payload) tabel per tabel."""
        summary: Dict[str, Any] = {}
        with httpx.Client(timeout=self.timeout) as client:
            # Tabel dokumen didahulukan supaya baris induk ada sebelum baris anaknya.
            order = sorted(payload, key=lambda t: 0 if _PRIMARY_KEYS.get(t) == "document_id" else 1)
            for table_name in order:
                summary[table_name] = self.push_table(client, table_name, payload[table_name])
        total_in = sum(v.get("inserted", 0) for v in summary.values())
        total_up = sum(v.get("updated", 0) for v in summary.values())
        logger.info(f"🗄  NocoDB: {total_in} baris baru, {total_up} baris diperbarui "
                    f"({len(summary)} tabel)")
        return {"tables": summary, "inserted": total_in, "updated": total_up}
