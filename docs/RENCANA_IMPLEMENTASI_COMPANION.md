# Rencana Implementasi: Companion Data Model (BAST lebih dulu)

Menindaklanjuti `docs/SKEMA_BAST_NOCODB.md`. Tujuan konkret: **JSON hasil ekstraksi bisa masuk
NocoDB**, dengan struktur yang sesuai kaidah data engineering, dan bisa Anda uji sendiri.

## Konteks dua percakapan sebelumnya

1. Kode dinilai sudah terlalu besar untuk dipelihara (13 tabel, 46 kolom di `contracts`).
2. Skema dibalik arahnya: dibaca dari **14 BAST asli**, bukan dari asumsi.
3. Diminta tabel NocoDB yang profesional: penamaan, relasi, kaidah data engineering.

## Keputusan yang mendasari rencana ini

| Keputusan | Alasan |
|---|---|
| **Satu definisi deklaratif**, bukan DDL + mapper ditulis tangan | Sumber utama beban maintenance sekarang: nama kolom ditulis ulang di beberapa tempat. Satu definisi → DDL, mapper, dan validator dibangkitkan. |
| **BAST dulu, kontrak/SPH menyusul** | Migrasi bertahap. Exporter lama tetap melayani kontrak/SPH sampai dimigrasi — **duplikasi sementara yang disengaja**, bukan permanen. |
| **PostgreSQL sebagai pemilik struktur, NocoDB sebagai UI** | Belum terkonfirmasi NocoDB UI membuat FK/UNIQUE sungguhan. Briefing hlm. 13 sudah memakai PostgreSQL. |
| **Payload JSON tetap dihasilkan** | Supaya bisa diuji tanpa database, dan supaya n8n tetap bisa mem-POST (briefing hlm. 8). |

## Tahapan

### Tahap 1 — Definisi deklaratif  ✔ target: `app/companion/model.py`
Satu file mendeklarasikan 8 tabel: kolom, tipe, nullability, unique, FK, check.
Dari sini dibangkitkan DDL, urutan insert, dan validasi tipe.

### Tahap 2 — Generator DDL  ✔ target: `app/companion/ddl.py`
Menghasilkan `schema.sql` PostgreSQL: PK, FK, UNIQUE, CHECK, index, trigger `updated_at`.

### Tahap 3 — Pemeta BAST  ✔ target: `app/companion/bast_mapper.py`
`*.extract.json` (BAST) → baris-baris 8 tabel. Termasuk:
- menurunkan `direction` dari siapa BUT (penyerah → `customer`, penerima → `vendor`);
- memisahkan nilai mentah (`*_text`) dan terparse (date/numeric);
- memindahkan fakta jarang ke `bast_condition`.

### Tahap 4 — Validator  ✔ target: `app/companion/validate.py`
Mengecek payload terhadap definisi **sebelum** menyentuh NocoDB: tipe, NOT NULL, UNIQUE,
FK yang menggantung, nilai enum. Ini yang mencegah "baris gagal masuk tanpa pesan jelas".

### Tahap 5 — CLI  ✔ target: `companion.py` (root)
`--ddl` cetak schema.sql · `--map FILE` hasilkan payload · `--check FILE` validasi ·
`--push FILE` kirim ke NocoDB.

### Tahap 6 — Uji ujung-ke-ujung
Jalankan BAST asli → payload → validasi → laporkan.

## Yang TIDAK dikerjakan di rencana ini

- Kontrak dan SPH belum dimigrasi (masih memakai exporter lama).
- Tabel lama di `app/exporters/nocodb.py` belum dihapus — menunggu migrasi selesai.
- Push sungguhan ke NocoDB belum bisa diverifikasi (belum ada instance).
