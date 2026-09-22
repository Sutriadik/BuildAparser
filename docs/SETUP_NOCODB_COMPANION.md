# Yang perlu disiapkan di NocoDB (model companion)

Dokumen ini menjawab satu pertanyaan: **apa saja yang harus saya klik/siapkan di NocoDB
supaya JSON hasil parser bisa masuk?** Urutannya sudah sesuai urutan kerja, bukan sesuai
urutan konsep.

> Ini menggantikan `docs/INTEGRASI_NOCODB.md` untuk BAST. Dokumen lama masih berlaku untuk
> exporter 13-tabel yang dipakai kontrak/SPH, dan keduanya akan hidup berdampingan sampai
> kontrak ikut dimigrasi.

---

## Ringkasnya: ada 9 tabel, dan 1 di antaranya bukan milik pipeline

| Tabel | Isi | Siapa yang menulis |
|---|---|---|
| `document` | tiap berkas yang masuk | mesin |
| `contract` | sementara, baru sasaran FK | mesin |
| `extraction_run` | riwayat tiap pemrosesan | mesin |
| `bast` | inti BAST | mesin |
| `bast_party` | 2 pihak per BAST | mesin |
| `bast_item` | baris barang/pekerjaan | mesin |
| `bast_condition` | fakta tambahan (progres, dokumen pendukung, …) | mesin |
| `extracted_field` | nilai per field + bukti halaman | mesin |
| **`field_review`** | **keputusan PM per field** | **MANUSIA** |

Pemisahan baris terakhir itu yang paling penting untuk dipahami sebelum menyiapkan apa pun.
Briefing hlm. 11 & 20 meminta konfirmasi manusia per field, dan harga **selalu** diverifikasi
manusia. Karena keputusan PM disimpan di **tabel terpisah**, memproses ulang dokumen secara
struktural tidak bisa menimpanya — bukan karena kodenya hati-hati, tapi karena pipeline
memang tidak pernah menulis ke tabel itu. Pusher akan **menolak** payload yang memuat
`field_review` (diuji di `tests/test_companion_model.py`).

---

## Langkah 1 — Pilih dulu: PostgreSQL + NocoDB, atau NocoDB saja

Ini keputusan pertama dan paling menentukan.

**Opsi A — PostgreSQL sebagai basis data, NocoDB sebagai UI (disarankan).**
Briefing hlm. 13 memang sudah memakai PostgreSQL. Constraint yang menjaga data —
`UNIQUE (bast_id, role)`, `ON DELETE RESTRICT` pada dokumen yang sudah dikonfirmasi PM,
CHECK untuk nilai enum — hanya benar-benar ditegakkan di sini. NocoDB lalu menyambung ke
basis data itu sebagai *external data source* dan tetap memberi UI untuk PM.

**Opsi B — Tabel dibuat manual lewat UI NocoDB.**
Lebih cepat dimulai, tapi relasi di UI NocoDB **belum terkonfirmasi** menjadi foreign key
sungguhan, dan UNIQUE gabungan tidak tersedia. Artinya: satu BAST bisa punya 3 pihak,
dokumen berisi keputusan PM bisa terhapus diam-diam, dan `direction` bisa berisi nilai
apa pun. Validator di `app/companion/validate.py` menangkap sebagian besar itu **sebelum**
kirim, tapi tidak menjaga apa yang diketik orang langsung di UI.

Saran: pakai **Opsi A**. Kalau hanya ingin melihat hasilnya dulu hari ini, Opsi B cukup,
asal disadari itu sementara.

### Opsi A — langkah konkretnya

```bash
# 1. PostgreSQL + NocoDB
docker run -d --name pg-ade -p 5432:5432 \
  -e POSTGRES_PASSWORD=ade -e POSTGRES_DB=deliveryops postgres:16

docker run -d --name nocodb -p 8080:8080 \
  -v "$(pwd)/nocodb-data:/usr/app/data" nocodb/nocodb:latest

# 2. Bangkitkan skema dari model, lalu jalankan
.venv311/bin/python companion.py --ddl --write
docker exec -i pg-ade psql -U postgres -d deliveryops \
  < storage/outputs/companion/schema.sql
```

Lalu di NocoDB: **New Base → Connect to external database → PostgreSQL**
(host `host.docker.internal`, port `5432`, db `deliveryops`, user `postgres`, password `ade`).
Kesembilan tabel langsung muncul beserta relasinya.

### Opsi B — buat tabel manual

Ikuti daftar kolom di bagian **Lampiran** di bawah. Untuk tiap kolom bertanda
`SingleSelect`, isi pilihannya persis seperti tertulis — pusher mengirim nilai itu apa
adanya, dan NocoDB menolak nilai di luar daftar.

---

## Langkah 2 — API Token

NocoDB → foto profil (kanan atas) → **Account Settings** → **Tokens** → **Add New Token**.

```bash
export NOCODB_URL="http://localhost:8080"
export NOCODB_API_TOKEN="<token-yang-disalin>"
```

## Langkah 3 — Ambil tableId

NocoDB v2 memakai id acak (mis. `m1a2b3c4d5`), **bukan** nama tabel, pada endpoint-nya.

```bash
.venv311/bin/python nocodb_setup.py --list-tables
```

Perintah itu mencetak baris `export NOCODB_TABLE_IDS="..."` yang tinggal disalin. Pastikan
kesembilan nama tabel companion ada di sana.

```bash
.venv311/bin/python nocodb_setup.py --check      # uji sambungan & pemetaan
```

## Langkah 4 — Coba jalankan, tanpa mengirim apa pun

```bash
# proses dokumen
.venv311/bin/python run.py "sample_pdfs/BAST_LISENSI_ATS_ORACLE_FULL_SIGNED.pdf"

# ubah hasilnya jadi baris tabel companion
.venv311/bin/python companion.py --map \
  "storage/outputs/extraction/BAST_LISENSI_ATS_ORACLE_FULL_SIGNED.extract.json"

# lihat RENCANA kirim — belum ada yang dikirim
.venv311/bin/python companion.py --push \
  "storage/outputs/companion/BAST_LISENSI_ATS_ORACLE_FULL_SIGNED.companion.json" --dry-run
```

Keluaran `--dry-run` menunjukkan urutan kirim dan cara tulis tiap tabel:

```
document             1 baris   upsert per content_hash
extraction_run       1 baris   tambah-saja
bast                 1 baris   upsert per document_id
bast_party           2 baris   upsert per bast_id+role
bast_item            1 baris   upsert per bast_id+line_no
bast_condition       4 baris   ganti-anak
extracted_field     27 baris   upsert per document_id+field_path

field_review        —       tidak disentuh (milik PM)
```

Baru setelah itu, kirim sungguhan dengan menghapus `--dry-run`.

## Langkah 5 — Siapkan tampilan untuk PM

Yang membuat NocoDB berguna bagi PM bukan tabelnya, tapi tampilannya:

1. **Grid `extracted_field`** difilter `system_status = review_required`, diurutkan per
   `document_id`. Ini antrean kerja PM.
2. **Form/Grid `field_review`** — satu-satunya tempat PM mengetik. Kolom `decision`
   (`confirmed` / `corrected` / `rejected`), `final_value_text`, `reviewed_by`,
   `reviewed_at`, `reviewer_note`.
3. Kalau memakai Opsi A, dua **view** sudah ikut terpasang dari `schema.sql`:
   - `document_review_status` — status verifikasi tiap dokumen, **dihitung**, bukan
     disimpan. Tidak ada dua sumber kebenaran yang bisa berbeda.
   - `field_review_stale` — konfirmasi PM yang sudah basi karena nilai AI-nya berubah
     setelah dikonfirmasi. Ini yang menjaga agar "sudah dikonfirmasi" tetap bermakna.

## Langkah 6 — Hak akses

Beri PM peran **Editor** pada base tersebut, lalu (kalau memakai Opsi A) kunci di sisi
PostgreSQL: berikan `INSERT/UPDATE` hanya pada `field_review`, dan `SELECT` pada sisanya.
Token pipeline sebaliknya: tanpa akses tulis ke `field_review`.

---

## Yang TIDAK perlu, dan sebaiknya tidak dibuat

- **Tabel klien / vendor / PO.** MyBhakti adalah sumber kebenaran untuk itu dan bersifat
  **read-only** bagi kita (briefing hlm. 6-7). Rujukannya disimpan sebagai kolom teks opak
  `mybhakti_po_ref` / `mybhakti_party_ref` yang diisi n8n — sengaja **bukan** foreign key,
  supaya tidak ada ilusi bahwa kita memiliki data itu.
- **Kolom "status verifikasi" di `document`.** Itu turunan; sudah jadi view.
- **Kolom baru tiap kali ada fakta baru di BAST.** Fakta Tier 3 masuk sebagai *baris* di
  `bast_condition` dengan `condition_type` baru. Skema tidak ikut melebar tiap ketemu
  format dokumen baru.

## Batas yang masih ada

- Pemeta baru mendukung **BAST**. Kontrak/SPK dan SPH masih lewat exporter 13-tabel yang
  lama.
- `bast_number_internal` dan `handover_city` belum diekstrak — kolomnya ada, isinya NULL.
- Arah BAST (`direction`) terbaca dari kalimat "menyerahkan"; pada 14 BAST asli pola itu
  ketemu di 11 dokumen. Tiga sisanya jadi `unknown`, bukan ditebak.

---

## Lampiran — daftar kolom (untuk Opsi B)

Dibangkitkan dari `app/companion/model.py`. Kolom `id`, `created_at`, `updated_at` dibuat
NocoDB/PostgreSQL sendiri dan tidak perlu ditambahkan manual.

### `document` — ditulis mesin

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `content_hash` | SingleLineText | ya | UNIQUE |
| `doc_type` | SingleSelect | ya | pilihan: contract / sph / bast |
| `source_filename` | SingleLineText | ya |  |
| `page_count` | Number | — |  |
| `markdown` | LongText | — |  |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |

### `contract` — ditulis mesin

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `document_id` | Number | ya | FK → document.id; UNIQUE |
| `contract_number` | SingleLineText | — |  |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |

### `extraction_run` — ditulis mesin

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `document_id` | Number | ya | FK → document.id |
| `ocr_engine` | SingleLineText | — |  |
| `llm_model` | SingleLineText | — |  |
| `prompt_version` | SingleLineText | — |  |
| `schema_version` | SingleLineText | — |  |
| `llm_call_count` | Number | — |  |
| `parse_seconds` | Decimal | — |  |
| `extract_seconds` | Decimal | — |  |
| `validation_status` | SingleLineText | — |  |
| `started_at` | DateTime | — |  |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |

### `bast` — ditulis mesin

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `document_id` | Number | ya | FK → document.id; UNIQUE |
| `direction` | SingleSelect | ya | pilihan: customer / vendor / unknown |
| `contract_id` | Number | — | FK → contract.id |
| `mybhakti_po_ref` | SingleLineText | — |  |
| `bast_number_customer` | SingleLineText | — |  |
| `bast_number_internal` | SingleLineText | — |  |
| `handover_date_text` | SingleLineText | — |  |
| `handover_date` | Date | — |  |
| `handover_city` | SingleLineText | — |  |
| `work_title` | LongText | ya |  |
| `basis_doc_type` | SingleSelect | — | pilihan: contract / pks / spk / order_note / purchase_order / other |
| `basis_doc_number` | SingleLineText | — |  |
| `basis_doc_date_text` | SingleLineText | — |  |
| `basis_doc_date` | Date | — |  |
| `basis_doc_value` | Decimal | — |  |
| `basis_doc_value_vat` | SingleSelect | — | pilihan: included / excluded / unstated |
| `currency` | SingleLineText | ya |  |
| `acceptance_statement` | LongText | — |  |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |

### `bast_party` — ditulis mesin

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `bast_id` | Number | ya | FK → bast.id |
| `role` | SingleSelect | ya | pilihan: handover / receiver |
| `org_name_text` | SingleLineText | ya |  |
| `signer_name` | SingleLineText | ya |  |
| `signer_title` | SingleLineText | — |  |
| `org_address_text` | LongText | — |  |
| `mybhakti_party_ref` | SingleLineText | — |  |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |
| _(constraint)_ | UNIQUE gabungan | | `bast_id + role` |

### `bast_item` — ditulis mesin

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `bast_id` | Number | ya | FK → bast.id |
| `line_no` | Number | ya |  |
| `description` | LongText | ya |  |
| `quantity` | Decimal | — |  |
| `unit` | SingleLineText | — |  |
| `unit_price` | Decimal | — |  |
| `line_total` | Decimal | — |  |
| `test_result` | SingleLineText | — |  |
| `remarks` | SingleLineText | — |  |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |
| _(constraint)_ | UNIQUE gabungan | | `bast_id + line_no` |

### `bast_condition` — ditulis mesin

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `bast_id` | Number | ya | FK → bast.id |
| `condition_type` | SingleSelect | ya | pilihan: progress_percent / service_active_since / acceptance_test_ref / delivery_reconciliation_ref / supporting_document / amount_in_words |
| `value_text` | LongText | — |  |
| `value_number` | Decimal | — |  |
| `value_date` | Date | — |  |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |

### `extracted_field` — ditulis mesin

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `document_id` | Number | ya | FK → document.id |
| `field_path` | SingleLineText | ya |  |
| `ai_value_text` | LongText | — |  |
| `evidence_page` | Number | — |  |
| `evidence_quote` | LongText | — |  |
| `evidence_score` | Decimal | — |  |
| `system_status` | SingleSelect | ya | pilihan: auto_verified / auto_accepted / review_required / conflict / missing |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |
| _(constraint)_ | UNIQUE gabungan | | `document_id + field_path` |

### `field_review` — ditulis MANUSIA (PM)

| Kolom | Tipe NocoDB | Wajib | Catatan |
|---|---|---|---|
| `document_id` | Number | ya | FK → document.id |
| `field_path` | SingleLineText | ya |  |
| `reviewed_ai_value_text` | LongText | — |  |
| `decision` | SingleSelect | ya | pilihan: confirmed / corrected / rejected |
| `final_value_text` | LongText | — |  |
| `reviewed_by` | SingleLineText | ya |  |
| `reviewed_at` | DateTime | ya |  |
| `reviewer_note` | LongText | — |  |
| `created_at` | DateTime | ya |  |
| `updated_at` | DateTime | ya |  |
| _(constraint)_ | UNIQUE gabungan | | `document_id + field_path` |
