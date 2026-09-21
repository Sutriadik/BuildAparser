# Integrasi FastAPI → NocoDB

Panduan praktik untuk menyambungkan parser ini ke NocoDB. Dibuat untuk dijalankan
sendiri langkah demi langkah, bukan cuma dibaca.

## Gambaran besar: siapa mengirim ke NocoDB

Pada arsitektur briefing (hlm. 8), **n8n yang mengorkestrasi**. Jadi jalur normalnya:

```
FastAPI  ──(kembalikan JSON)──>  n8n  ──(POST)──>  NocoDB
```

FastAPI **tidak** menulis ke NocoDB dalam alur normal. Ia mengembalikan
`nocodb_payload` di responsnya, dan n8n yang mengirimkannya. Alasannya: kalau dua
pihak sama-sama menulis, sulit melacak siapa yang mengubah baris.

Push langsung dari FastAPI tetap tersedia untuk uji coba dan deployment tanpa n8n,
tapi **mati secara default** dan harus diaktifkan sadar lewat `NOCODB_PUSH_ENABLED=1`.

---

## Langkah 1 — Jalankan NocoDB

Cara tercepat, dengan Docker:

```bash
docker run -d --name nocodb -p 8080:8080 \
  -v "$(pwd)/nocodb-data:/usr/app/data" \
  nocodb/nocodb:latest
```

Buka `http://localhost:8080`, buat akun, lalu buat satu **Base** baru
(misal namanya `DeliveryOps`).

## Langkah 2 — Ambil API Token

Di NocoDB: klik foto profil (kanan atas) → **Account Settings** → **Tokens** →
**Add New Token**. Salin tokennya.

```bash
export NOCODB_URL="http://localhost:8080"
export NOCODB_API_TOKEN="<token-yang-tadi-disalin>"
```

## Langkah 3 — Buat tabelnya

Skema tabel sudah dihasilkan otomatis dari hasil ekstraksi:

```bash
.venv311/bin/python nocodb_export.py --schema
cat storage/outputs/nocodb/_schema.json
```

File itu berisi 13 tabel beserta tipe kolomnya (`SingleLineText`, `LongText`,
`Decimal`, `Number`, `Checkbox`, `DateTime`). Buat tabelnya di NocoDB sesuai daftar
itu — mulai dari yang paling penting dulu:

| Tabel | Isinya | Kunci upsert |
|---|---|---|
| `contracts` | Satu baris per kontrak + kolom `markdown_dokumen` | `document_id` |
| `contract_items` | Baris tabel harga/BoQ | `item_key` |
| `field_confirmations` | **Satu baris per field** untuk dikonfirmasi PM | `confirmation_key` |
| `extraction_runs` | Telemetri waktu proses (untuk bukti "Terukur") | `run_id` |

> **Penting soal tipe kolom.** `markdown_dokumen` harus **LongText**. Kolom yang
> namanya terdengar angka tapi isinya teks — `durasi_kerja` ("30 hari kalender"),
> `persentase_penalti` ("1/1000"), `nilai_ekstraksi` — juga harus teks, bukan
> Decimal. Kalau salah tipe, barisnya gagal masuk **tanpa pesan error yang jelas**.

## Langkah 4 — Ambil tableId

NocoDB v2 memakai **tableId acak** (mis. `m1a2b3c4d5`), bukan nama tabel. Cara
melihatnya: buka tabelnya, lihat URL di browser — bagian setelah `/table/`.

Atau pakai skrip bantu:

```bash
.venv311/bin/python nocodb_setup.py --list-tables
```

Lalu isi pemetaannya:

```bash
export NOCODB_TABLE_IDS="contracts:m1abc,contract_items:m2def,field_confirmations:m3ghi,extraction_runs:m4jkl"
```

## Langkah 5 — Cek sambungan

```bash
.venv311/bin/python nocodb_setup.py --check
```

Perintah ini memastikan: URL terjangkau, token valid, dan setiap tableId benar-benar
ada. Kalau ada yang salah, ia menyebutkan yang mana — bukan gagal diam-diam.

## Langkah 6 — Kirim satu dokumen

**Cara A — lewat skrip (tanpa server):**

```bash
.venv311/bin/python nocodb_setup.py --push "storage/outputs/nocodb/SPK_ATS_ORACLE_FULL_SIGNED.nocodb.json"
```

**Cara B — lewat FastAPI:**

```bash
export NOCODB_PUSH_ENABLED=1          # aktifkan sadar; default mati
.venv311/bin/uvicorn app.main:app --reload

curl -X POST http://localhost:8000/api/v1/process-all \
  -F "file=@sample_pdfs/SPK_ATS_ORACLE_FULL_SIGNED.pdf" \
  -F "ocr=rapidocr" \
  -F "push_to_nocodb=true"
```

**Cara C — lewat n8n (yang dipakai di produksi):**

1. Node **HTTP Request** → POST ke `/api/v1/process-all` (tanpa `push_to_nocodb`)
2. Ambil `nocodb_payload` dari responsnya
3. Node **HTTP Request** kedua → POST ke
   `{{$env.NOCODB_URL}}/api/v2/tables/<tableId>/records`
   dengan header `xc-token` dan body `nocodb_payload.contracts`
4. Ulangi untuk tiap tabel — **tabel dokumen dulu**, baru tabel anaknya

---

## Aturan yang dijaga kode ini

Tiga hal dari briefing yang ditegakkan di `app/services/nocodb_client.py`:

**1. Upsert, bukan insert (idempoten).** Dokumen yang sama diproses ulang akan
**memperbarui** barisnya, bukan menggandakan. Kuncinya `document_id` — sha1 dari isi
dokumen, jadi stabil antar-run.

**2. Kolom milik manusia tidak pernah ditimpa.** Saat memperbarui baris lama, kolom
ini dibuang dari payload:

```
pm_confirmed · nilai_final · harga_terverifikasi · dikonfirmasi_oleh
dikonfirmasi_pada · catatan_pm · semua_field_terkonfirmasi · sudah_terpenuhi
```

Kalau PM sudah mengunci sebuah field (hlm. 11 & 20), menjalankan ulang parser
**tidak boleh** mengembalikannya jadi "belum dikonfirmasi".

**3. MyBhakti tidak diduplikasi.** Kolom `mybhakti_client_id`, `mybhakti_vendor_id`,
`mybhakti_project_id`, `mybhakti_po_id` disediakan tapi **selalu null** — n8n yang
menautkannya nanti. Klien/Vendor/Proyek/PO milik MyBhakti (hlm. 7).

---

## Kalau gagal

| Gejala | Kemungkinan sebabnya |
|---|---|
| `NOCODB_API_TOKEN kosong` | Env belum di-export di shell yang sama |
| `Tabel 'X' dilewati` | Nama tabel belum ada di `NOCODB_TABLE_IDS` |
| Baris masuk tapi kolom kosong | Nama kolom di NocoDB beda dengan di payload |
| Baris tergandakan | Kolom kunci (`document_id`/`*_key`) belum dibuat di NocoDB |
| `502 Push tabel gagal` | Tipe kolom tidak cocok — cek `nocodb_export.py --check` |
