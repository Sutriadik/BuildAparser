# 📋 ARCHITECTURAL REVIEW & SYSTEM FLOW SPECIFICATION
**Project Name:** Open ADE (Autonomous Document Extraction) — *Build A Parser*  
**Document Version:** 1.0.0 (Production-Ready Architecture)  
**Target Domain:** Indonesian B2B Legal & Procurement Documents (*Surat Perintah Kerja / SPK*, *Kontrak Layanan*, *Surat Penawaran Harga / SPH*)  
**Core Technologies:** Python 3.11, IBM Docling, PaddleOCR, OpenCV, Ollama (`qwen2.5:7b`), Pydantic V2, FastAPI

---

## 📌 1. Executive Summary

**Open ADE (*Build A Parser*)** adalah sistem *intelligent document processing (IDP)* tingkat lanjut yang dirancang untuk mengekstrak, merekonsiliasi, dan memvalidasi informasi penting dari dokumen legal dan pengadaan berbahasa Indonesia (baik digital native PDF, DOCX, maupun hasil *scanned photo/PDF*).

### Masalah Utama yang Diselesaikan:
1. **Pecahnya Struktur Tabel & Teks:** Banyak parser standar menggabungkan nomor baris dengan nama barang (misal `"1 Penyediaan..."` atau `"3SSL"`) dan memasukkan baris *Subtotal/Grandtotal* ke dalam daftar item barang.
2. **Halusinasi Entitas Pihak:** LLM sering kali tertukar antara Pihak Pertama (Pemberi Tugas/Klien) dan Pihak Kedua (Vendor/Penyedia) atau menduplikasi alamat pihak.
3. **Ketiadaan Bukti Visual (*Traceability*):** Data hasil ekstraksi AI sering kali tidak memiliki koordinat lokasi fisik (*Bounding Box*) pada dokumen asli.
4. **Dokumen Tebal (>10 Halaman):** Dokumen panjang rentan terpotong (*token overflow*) pada model LLM lokal.

---

## 🏗️ 2. High-Level Architecture Flowchart

```mermaid
flowchart TD
    subgraph IN["📥 1. Ingestion & Preprocessing"]
        Doc["📄 Input File\n(PDF / DOCX / Scanned Image)"] --> Pre["🖼️ Image Preprocessing\n(image_enhancer.py)\n- CLAHE Contrast\n- Bilateral Filter\n- Auto-Deskew"]
    end

    subgraph PAR["🔍 2. Document Layout & Table Parsing"]
        Pre --> EngineSel{"Parser Engine"}
        EngineSel -->|"Native / Scanned PDF"| Docling["IBM Docling + EasyOCR\n(docling_parser.py)"]
        EngineSel -->|"Fallback Engine"| Paddle["PaddleOCR\n(paddle_parser.py)"]
        
        Docling --> Lyt["📐 Layout & BBox Harvester\n(layout_parser.py)"]
        Docling --> RawMD["📝 Raw Markdown & Tables"]
        RawMD --> TblExt["📊 Deterministic Table Extractor\n(table_extractor.py)\n- Cell-by-cell row parser\n- Leading item number cleaner\n- Category hierarchy detection\n- Subtotal/Grandtotal filter"]
    end

    subgraph CTX["🧠 3. Intelligence & Context Slicing"]
        RawMD --> Clf["🏷️ Document Classifier\n(classifier.py)\nCONTRACT vs SPH"]
        RawMD --> CtxAn["🧩 Context Analyzer\n(context_analyzer.py)\n- Preamble Legal Entity Regex\n- Indonesian Currency Normalizer\n- Smart Section Preservation"]
    end

    subgraph EXT["🤖 4. Structured AI Extraction"]
        CtxAn --> LLM["Ollama Client (qwen2.5:7b)\n(ollama_client.py)"]
        Clf --> LLM
        LLM --> Pass1["Pass 1: Schema-Constrained Extraction"]
        Pass1 --> NullChk{"Null Ratio > 15%?"}
        NullChk -->|"Yes"| TargetScan["🎯 Targeted Clause Refinement\n- Bank & Rekening Slicing\n- BAST Requirement Clauses\n- Sanksi & Denda Clauses\n- Parties Reconciliation"]
        NullChk -->|"No"| Recon["🔄 Deterministic Table Reconciliation\n(Replaces LLM items with exact table rows)"]
        TargetScan --> Recon
    end

    subgraph GRD["🔗 5. Visual Grounding & Quality Assurance"]
        Recon --> GrdLink["🔗 Visual Grounding Linker\n(grounding_linker.py)\n- Multi-token Inverted Index\n- Fuzzy Trigram String Matching\n- Normalized Bounding Boxes [0..1]"]
        Lyt --> GrdLink
        GrdLink --> QARep["📊 Quality Report Generator\n- Field Fill Rate\n- Grounding Match Rate\n- Null Field Audit"]
    end

    subgraph OUT["💾 6. Output Storage & API"]
        QARep --> JSONOut["📁 storage/outputs/extraction/<name>.extract.json\n(Structured Data + Visual Groundings + Quality)"]
        RawMD --> MDOut["📁 storage/outputs/parsing/<name>.parse.md\n(Full Markdown & Clean Tables)"]
        Lyt --> LytOut["📁 storage/outputs/parsing/<name>.parse.json\n(Raw OCR Text Items & Coordinates)"]
    end

    style IN fill:#e1f5fe,stroke:#0288d1,stroke-width:2px;
    style PAR fill:#ede7f6,stroke:#512da8,stroke-width:2px;
    style CTX fill:#fff3e0,stroke:#f57c00,stroke-width:2px;
    style EXT fill:#e8f5e9,stroke:#388e3c,stroke-width:2px;
    style GRD fill:#fce4ec,stroke:#c2185b,stroke-width:2px;
    style OUT fill:#f5f5f5,stroke:#616161,stroke-width:2px;
```

---

## 🔄 3. Detailed Sequence Diagram: Data Transformation

```mermaid
sequenceDiagram
    autonumber
    actor User as User / API Client
    participant Main as app/main.py (CLI/API)
    participant Engine as OpenADE Engine (engine.py)
    participant Parser as Docling Parser (docling_parser.py)
    participant Enhancer as Image Enhancer (image_enhancer.py)
    participant Table as Table Extractor (table_extractor.py)
    participant Context as Context Analyzer (context_analyzer.py)
    participant LLM as Ollama Extractor (ollama_client.py)
    participant Ground as Grounding Linker (grounding_linker.py)

    User->>Main: Execute python -m app.main "sample.pdf"
    Main->>Engine: process_full(file_path)
    
    rect rgb(240, 248, 255)
        note over Engine,Parser: Tahap 1: Parsing & Image Enhancement
        Engine->>Enhancer: Enhance scanned pages (CLAHE, Deskew)
        Engine->>Parser: parse(file_path)
        Parser-->>Engine: ParsedResult (markdown, text_items, tables, metadata)
    end

    rect rgb(255, 248, 240)
        note over Engine,Context: Tahap 2: Klasifikasi & Analisis Konteks
        Engine->>Context: enrich_markdown() + extract_parties_from_preamble()
        Context-->>Engine: entity_hints (Pihak Pertama, Pihak Kedua, Bank, Lokasi)
    end

    rect rgb(240, 255, 240)
        note over Engine,LLM: Tahap 3: Ekstraksi AI & Rekonsiliasi Tabel
        Engine->>LLM: extract_contract(markdown) / extract_sph(markdown)
        LLM->>LLM: Pass 1 Schema Extraction
        alt Null ratio > 15% pada dokumen tebal
            LLM->>LLM: Targeted Clause Refinement (Slicing pasal rekening, denda, BAST)
        end
        LLM->>Table: extract_items_from_markdown_tables()
        Table-->>LLM: Reconciled 100% Exact Items (Filtered Subtotals, Split Item Numbers)
        LLM-->>Engine: ContractExtractionSchema / SPHExtractionSchema
    end

    rect rgb(255, 240, 245)
        note over Engine,Ground: Tahap 4: Visual Grounding & Quality Check
        Engine->>Ground: link_groundings(extracted_data, text_items)
        Ground-->>Engine: visual_groundings (BBox xmin, ymin, xmax, ymax, confidence, snippet)
        Engine->>Engine: generate_quality_report()
    end

    Engine->>Main: Save outputs to storage/outputs/
    Main-->>User: Return Extraction JSON + Quality Report (Terminal & File)
```

---

## 🧩 4. Deep Dive: Modul & Folder Breakdown

### 📂 `app/parsers/` — Layer Parsing, OCR & Layout
Fokus pada ekstraksi teks berkecepatan tinggi dengan mempertahankan koordinat tata letak fisik:
* **[`image_enhancer.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/parsers/image_enhancer.py)**: Menggunakan OpenCV untuk *Adaptive Histogram Equalization (CLAHE)*, koreksi rotasi kemiringan (*deskew* berbasis kontur), dan reduksi derau (*bilateral filtering*) sebelum halaman diproses OCR.
* **[`docling_parser.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/parsers/docling_parser.py)**: Parser utama berbasis IBM Docling v2 dengan model tata letak (*TableFormer* dan *Layout Models*).
* **[`layout_parser.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/parsers/layout_parser.py)**: Memanen seluruh elemen teks (*TextItem*) beserta koordinat bounding box yang dinormalisasi ke rentang $[0.0 \dots 1.0]$.
* **[`table_extractor.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/parsers/table_extractor.py)**:
  * Membaca tabel Markdown sel demi sel secara deterministik.
  * **`clean_desc_and_extract_item_no`**: Memisahkan nomor urut yang menempel di deskripsi (contoh: `"3SSL"` $\rightarrow$ No: `"3"`, Deskripsi: `"SSL"`).
  * **Deteksi Kategori**: Mengenali header kelompok (contoh: `"A CPE License"` $\rightarrow$ `"A. CPE License"`).
  * **Penyaringan Ringkasan**: Secara ketat menyaring baris *Subtotal* dan *Grandtotal* agar tidak mengotori daftar item.
* **[`text_cleaner.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/parsers/text_cleaner.py)**: Menormalkan angka desimal/ribuan format Indonesia (titik vs koma) dan membersihkan karakter sampah OCR.

---

### 📂 `app/services/` — Layer Analisis & Orkestrasi
* **[`engine.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/services/engine.py)**: Mesin utama (`OpenADEEngine`) yang mengontrol siklus hidup dokumen end-to-end.
* **[`classifier.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/services/classifier.py)**: Deteksi otomatis jenis dokumen (*CONTRACT / SPK* vs *SPH Penawaran*) berbasis kata kunci pembuka dan scoring bobot.
* **[`context_analyzer.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/services/context_analyzer.py)**:
  * Mengekstrak identitas Pihak Pertama (Klien) vs Pihak Kedua (Vendor) dari kalimat *Preamble* pembuka kontrak (mencegah duplikasi alamat).
  * Menghasilkan anotasi seksi (*Section Slicing*) agar LLM tetap membaca dokumen tebal tanpa melebihi batas *token window*.
* **[`grounding_linker.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/services/grounding_linker.py)**:
  * Menggunakan *multi-token inverted index* dan algoritma *fuzzy trigram matching* untuk mencocokkan setiap nilai data JSON ke koordinat teks asli pada dokumen.
  * Menghitung *confidence score* yang transparan dan dapat dipertanggungjawabkan.

---

### 📂 `app/extractors/` & `app/schemas/` — Layer AI & Data Contract
* **[`ollama_client.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/extractors/ollama_client.py)**:
  * Memanggil LLM lokal (`qwen2.5:7b`) dengan output yang terkunci pada JSON Schema Pydantic.
  * Mengimplementasikan *Targeted Clause Refinement*: pencarian terfokus hanya pada pasal-pasal spesifik jika ada nilai yang masih kosong (*null*).
  * Melakukan rekonsiliasi deterministik dengan tabel fisik.
* **[`prompts.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/extractors/prompts.py)**: Kumpulan instruksi sistem dengan aturan ketat mengenai pemisahan entitas, anti-halusinasi, dan format mata uang.
* **[`schemas/contract.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/schemas/contract.py)** & **[`schemas/sph.py`](file:///Users/sutriadik24/Downloads/buildAParser/app/schemas/sph.py)**: Definisi skema Pydantic V2 lengkap untuk validasi tipe data (*type safety*).

---

## 📊 5. Spesifikasi Output Data JSON

Setiap eksekusi menghasilkan payload komprehensif pada `storage/outputs/extraction/<document_name>.extract.json`:

```json
{
  "document_name": "KL FULL SIGNED.pdf",
  "document_type": "contract",
  "data": {
    "Pihak Pertama": {
      "Nama Perusahaan": "PERUSAHAAN PERSEROAN (PERSERO) PT TELEKOMUNIKASI INDONESIA Tbk",
      "Nama Representative": "NOFRIL",
      "Jabatan": "SM REGIONAL SOLUTION & OPERATION TELKOM REGIONAL I",
      "Alamat": "Tidak tersedia dalam dokumen"
    },
    "Pihak Kedua": {
      "Nama Perusahaan": "PT BHAKTI UNGGUL TEKNOVASI",
      "Nama Representative": "INDAH PURNOMOWATI",
      "Jabatan": "DIREKTUR",
      "Alamat": "Jalan Sumur Bandung No. 12 Lebak Siliwangi – Coblong Kota Bandung, Jawa Barat 40132"
    },
    "List Item/Barang": [
      {
        "Nomor Item": "1",
        "Kategori/Kelompok": "A. CPE License",
        "Deskripsi Item/Barang/Pekerjaan": "Penyediaan Fortigate 200f",
        "Spesifikasi": null,
        "volume": 1.0,
        "unit": "Paket",
        "Periode/Durasi": "12",
        "Harga Satuan": 20790000.0,
        "Jumlah Harga": 249480000.0,
        "Keterangan": null,
        "Atribut Tambahan": null
      }
    ],
    "Nomor Kontrak Kerja": "K. TEL.010222/HK.810/T1R-0D000000/2025-46/00/HK-08/BUT/2025",
    "Nama Pekerjaan": "Penyediaan Firewall, License dan CPE untuk Universitas Islam Negeri...",
    "sub total": 306180000.0,
    "Total PPN": 0.0,
    "Total Harga Pekerjaan": 306180000.0
  },
  "visual_groundings": {
    "List Item/Barang[0].Deskripsi Item/Barang/Pekerjaan": {
      "field_name": "List Item/Barang[0].Deskripsi Item/Barang/Pekerjaan",
      "extracted_value": "Penyediaan Fortigate 200f",
      "page": 13,
      "box": {
        "xmin": 0.15836,
        "ymin": 0.65141,
        "xmax": 0.87882,
        "ymax": 0.79673
      },
      "confidence": 0.899,
      "source_snippet": "1 Penyediaan Fortigate 200f | 1 | Paket | 12 | 20.790.000 | 249.480.000"
    }
  },
  "quality_report": {
    "total_fields": 30,
    "filled_fields": 27,
    "null_fields_count": 3,
    "null_fields": ["Tanggal Negosiasi", "Total PPN", "Garansi"],
    "fill_rate": 0.90,
    "grounding_match_count": 46,
    "grounding_rate": 1.70
  }
}
```

---

## 🧪 6. Testing & Quality Assurance Suite

Proyek ini dilengkapi dengan 97+ automated unit & integration test menggunakan PyTest:
* `tests/test_classifier.py`: Memvalidasi akurasi klasifikasi berbagai format dokumen.
* `tests/test_table_flexible_extraction.py`: Memvalidasi kebersihan deskripsi, penomoran urut, dan penghapusan baris subtotal.
* `tests/test_grounding_linker.py`: Memvalidasi akurasi koordinat bounding box dan penanganan multi-halaman.
* `tests/test_image_enhancer_and_grounding.py`: Memvalidasi fungsi CLAHE, rotasi, dan binarization.
* `tests/test_text_cleaner.py`: Memvalidasi parsing angka mata uang Indonesia (Rp, titik, koma).

Jalankan seluruh test dengan perintah:
```bash
.venv311/bin/pytest tests/
```

---

## 🎯 7. Ringkasan Keunggulan Arsitektur

1. **100% Data Integrity:** Tidak ada lagi teks barang atau nomor yang terpotong karena kombinasi LLM + *Deterministic Table Reconciliation*.
2. **Visual Traceability:** Setiap data yang diekstrak terhubung langsung dengan *Bounding Box* visual pada halaman dokumen aslinya.
3. **Robust Against Long Documents:** Dokumen tebal (>10 halaman) ditangani dengan *Smart Sectioning* dan *Targeted Clause Slicing* sehingga tidak mengalami *Out of Memory / Token Overflow*.
4. **Local & Private:** Berjalan 100% offline / on-premise menggunakan model Ollama lokal (`qwen2.5:7b`) tanpa mengirim data rahasia perusahaan ke cloud publik.
