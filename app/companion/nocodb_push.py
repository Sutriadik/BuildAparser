"""
Open ADE — Kirim payload companion ke NocoDB.

Klien lama (app/services/nocodb_client.py) tidak bisa dipakai di sini karena bentuk datanya
beda mendasar: di exporter lama setiap tabel membawa `document_id` berupa string hash, jadi
tabel bisa dikirim dalam urutan apa pun. Model companion memakai foreign key berupa ID BARIS
sungguhan, yang baru diketahui SETELAH baris induknya masuk. Jadi modul ini:

1. mengirim tabel menurut `insert_order()` -- induk dulu, anak menyusul;
2. mencatat Id NocoDB tiap induk, lalu menukar `_document_ref` / `_bast_ref` pada baris anak
   menjadi `document_id` / `bast_id` yang nyata.

Tiga cara penulisan, dipilih dari bentuk tabel di model.py, bukan dari daftar hardcode:

- **Upsert** bila tabel punya kunci alami (UNIQUE / UNIQUE gabungan). Dokumen yang diproses
  ulang memperbarui baris, tidak menggandakannya.
- **Ganti-anak** bila tabel anak tidak punya kunci alami (`bast_condition`): baris lama milik
  BAST itu dihapus lalu diisi ulang. Tanpa ini, proses ulang menumpuk kondisi kembar.
- **Tambah-saja** untuk `extraction_run`: tabel itu riwayat. Menimpanya menghapus bukti
  "Terukur" (briefing hlm. 21) -- justru run sebelumnya yang jadi pembanding.

`field_review` TIDAK pernah disentuh. Itu tabel milik PM (hlm. 11 & 20); bukan cuma
tidak dikirim, tapi ditolak kalau sampai ada di payload.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.companion.model import Table, insert_order, table
from app.companion.validate import validate_payload
from app.logger import logger

# Riwayat: tiap pemrosesan menambah baris baru, tidak menimpa yang lama.
APPEND_ONLY = {"extraction_run"}


class CompanionPushError(RuntimeError):
    pass


def natural_key(t: Table) -> Optional[Tuple[str, ...]]:
    """Kunci alami tabel menurut model: UNIQUE gabungan, atau satu kolom UNIQUE."""
    if t.unique_together:
        return tuple(t.unique_together[0])
    uniq = [c.name for c in t.columns if c.unique]
    return (uniq[0],) if uniq else None


class CompanionPusher:
    def __init__(self, base_url: str, api_token: str, table_ids: Dict[str, str],
                 timeout: float = 30.0, transport: Optional[httpx.BaseTransport] = None):
        # Token dicek saat push sungguhan, bukan di sini: rencana (--dry-run) harus bisa
        # dilihat sebelum NocoDB dipasang, supaya tabel yang perlu dibuat sudah diketahui.
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.table_ids = table_ids
        self.timeout = timeout
        self.transport = transport      # diisi tes dengan NocoDB tiruan
        # (nama_tabel, nilai_ref) -> Id baris NocoDB
        self._ids: Dict[Tuple[str, str], int] = {}

    # ------------------------------------------------------------------ HTTP
    def _headers(self) -> Dict[str, str]:
        return {"xc-token": self.api_token, "Content-Type": "application/json"}

    def _url(self, table_name: str) -> str:
        tid = self.table_ids.get(table_name)
        if not tid:
            raise CompanionPushError(
                f"Tabel '{table_name}' belum ada di NOCODB_TABLE_IDS. "
                f"Jalankan: python nocodb_setup.py --list-tables"
            )
        return f"{self.base_url}/api/v2/tables/{tid}/records"

    @staticmethod
    def _esc(value: Any) -> str:
        """Nilai untuk klausa `where` NocoDB. Tanda kurung dan koma adalah sintaks di sana,
        jadi harus di-escape -- `field_path` seperti "bast.Nilai (Rp)" akan memotong query
        dan mencocokkan baris yang salah, bukan gagal dengan jelas."""
        return str(value).replace("\\", "\\\\").replace(",", "\\,").replace("(", "\\(").replace(")", "\\)")

    def _get(self, client: httpx.Client, table_name: str, where: str) -> List[Dict[str, Any]]:
        r = client.get(self._url(table_name), headers=self._headers(),
                       params={"where": where, "limit": 200})
        r.raise_for_status()
        return r.json().get("list", [])

    @staticmethod
    def _ids_from_response(resp: httpx.Response) -> List[int]:
        body = resp.json()
        rows = body if isinstance(body, list) else [body]
        return [int(r["Id"]) for r in rows if isinstance(r, dict) and r.get("Id") is not None]

    # ------------------------------------------------------------------ ref FK
    def _resolve_refs(self, table_name: str, row: Dict[str, Any]) -> Dict[str, Any]:
        """`_bast_ref: "sha1-..."` -> `bast_id: 41`. Baris yatim dihentikan, bukan dikirim
        dengan FK kosong -- baris tanpa induk tidak bisa ditemukan lagi oleh PM."""
        out = {}
        for key, value in row.items():
            if not key.startswith("_"):
                out[key] = value
                continue
            parent = key[1:-4]                       # _bast_ref -> bast
            real = self._ids.get((parent, str(value)))
            if real is None:
                raise CompanionPushError(
                    f"{table_name}: induk '{parent}' untuk ref {value!r} tidak ditemukan. "
                    f"Baris induk gagal masuk lebih dulu."
                )
            out[f"{parent}_id"] = real
        return out

    # ------------------------------------------------------------------ strategi tulis
    def _push_upsert(self, client: httpx.Client, table_name: str, key: Tuple[str, ...],
                     rows: List[Dict[str, Any]], ref_of: Dict[int, str]) -> Dict[str, int]:
        inserted = updated = 0
        for i, row in enumerate(rows):
            where = "~and".join(f"({k},eq,{self._esc(row[k])})" for k in key)
            found = self._get(client, table_name, where)
            if found:
                patch = dict(row, Id=found[0]["Id"])
                r = client.patch(self._url(table_name), headers=self._headers(), json=[patch])
                r.raise_for_status()
                new_id, updated = int(found[0]["Id"]), updated + 1
            else:
                r = client.post(self._url(table_name), headers=self._headers(), json=[row])
                r.raise_for_status()
                got = self._ids_from_response(r)
                if not got:
                    raise CompanionPushError(
                        f"{table_name}: NocoDB tidak mengembalikan Id untuk baris baru; "
                        f"baris anak tidak bisa menunjuk ke sini."
                    )
                new_id, inserted = got[0], inserted + 1
            if i in ref_of:
                self._ids[(table_name, ref_of[i])] = new_id
        return {"inserted": inserted, "updated": updated}

    def _push_replace_children(self, client: httpx.Client, table_name: str, fk_col: str,
                               rows: List[Dict[str, Any]]) -> Dict[str, int]:
        induk = {row[fk_col] for row in rows}
        deleted = 0
        for parent_id in induk:
            lama = self._get(client, table_name, f"({fk_col},eq,{parent_id})")
            if lama:
                r = client.request("DELETE", self._url(table_name), headers=self._headers(),
                                   json=[{"Id": x["Id"]} for x in lama])
                r.raise_for_status()
                deleted += len(lama)
        r = client.post(self._url(table_name), headers=self._headers(), json=rows)
        r.raise_for_status()
        return {"inserted": len(rows), "deleted": deleted}

    def _push_append(self, client: httpx.Client, table_name: str,
                     rows: List[Dict[str, Any]]) -> Dict[str, int]:
        r = client.post(self._url(table_name), headers=self._headers(), json=rows)
        r.raise_for_status()
        return {"inserted": len(rows)}

    # ------------------------------------------------------------------ entri
    def push(self, payload: Dict[str, List[Dict[str, Any]]],
             dry_run: bool = False) -> Dict[str, Any]:
        masalah = validate_payload(payload)
        if masalah:
            raise CompanionPushError(
                "Payload tidak lolos validasi, tidak dikirim. Perbaiki dulu:\n  "
                + "\n  ".join(masalah[:10])
            )

        urut = [t for t in insert_order() if payload.get(t)]
        rencana: Dict[str, str] = {}
        for name in urut:
            t = table(name)
            if t.written_by == "human":
                raise CompanionPushError(
                    f"'{name}' ditulis manusia; pipeline tidak boleh mengirim ke sini."
                )
            if name in APPEND_ONLY:
                rencana[name] = "tambah-saja"
            elif natural_key(t):
                rencana[name] = f"upsert per {'+'.join(natural_key(t))}"
            else:
                rencana[name] = "ganti-anak"
        if dry_run:
            return {"dry_run": True, "urutan": urut, "strategi": rencana,
                    "baris": {t: len(payload[t]) for t in urut}}
        if not self.api_token:
            raise CompanionPushError(
                "NOCODB_API_TOKEN kosong. Ambil di NocoDB: foto profil > Account Settings "
                "> Tokens > Add New Token, lalu: export NOCODB_API_TOKEN=\"<token>\""
            )

        summary: Dict[str, Any] = {}
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            for name in urut:
                t = table(name)
                # Nilai ref baris induk dicatat supaya anaknya bisa menunjuk ke Id nyata.
                ref_of = {i: str(r["content_hash"]) for i, r in enumerate(payload[name])
                          if "content_hash" in r}
                if name == "bast":
                    ref_of = {i: str(r["_document_ref"]) for i, r in enumerate(payload[name])
                              if "_document_ref" in r}
                rows = [self._resolve_refs(name, r) for r in payload[name]]
                try:
                    key = natural_key(t)
                    if name in APPEND_ONLY:
                        summary[name] = self._push_append(client, name, rows)
                    elif key:
                        summary[name] = self._push_upsert(client, name, key, rows, ref_of)
                    else:
                        fk = next(c.name for c in t.columns if c.fk)
                        summary[name] = self._push_replace_children(client, name, fk, rows)
                except httpx.HTTPError as e:
                    body = getattr(getattr(e, "response", None), "text", "")[:400]
                    raise CompanionPushError(f"Tabel '{name}' gagal: {e} {body}") from e

        total = sum(v.get("inserted", 0) for v in summary.values())
        up = sum(v.get("updated", 0) for v in summary.values())
        logger.info(f"🗄  NocoDB companion: {total} baris baru, {up} diperbarui")
        return {"tables": summary, "inserted": total, "updated": up}
