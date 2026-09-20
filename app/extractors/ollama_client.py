"""
Open ADE — Ollama LLM Extraction Client.

Alur per dokumen: context enrichment → 1x schema extraction → targeted clause (hanya field
yang null) → retry (opsional) → rekonsiliasi deterministik (tabel, terbilang, hint regex).

Perubahan penting:
- `num_ctx` diset eksplisit. Tanpa itu Ollama memakai default server (umumnya 4096 token)
  dan memotong awal prompt (termasuk system prompt) secara diam-diam.
- `seed` + temperature 0 agar hasil dapat direproduksi.
- 6 blok pemanggilan LLM yang copy-paste disatukan ke `_ask_json`.
- Jumlah & durasi LLM call dicatat (`llm_calls`, `llm_seconds`) untuk observability.
"""
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import httpx
import ollama
from pydantic import BaseModel

from app.config import config
from app.extractors.deterministic.numbers import amounts_equal, terbilang_to_number
from app.extractors.prompts import (
    BAST_EXTRACTION_SYSTEM_PROMPT,
    BAST_RETRY_PROMPT_TEMPLATE,
    CONTRACT_EXTRACTION_SYSTEM_PROMPT,
    CONTRACT_RETRY_PROMPT_TEMPLATE,
    SPH_EXTRACTION_SYSTEM_PROMPT,
    SPH_RETRY_PROMPT_TEMPLATE,
)
from app.logger import logger
from app.parsers.table_extractor import (
    clean_desc_and_extract_item_no,
    extract_items_from_markdown_tables,
    extract_tables_from_markdown,
)
from app.schemas.bast import BASTExtractionSchema, BASTItemDetail
from app.schemas.contract import ContractExtractionSchema, ItemBarangPekerjaan
from app.schemas.sph import SPHExtractionSchema, SPHItemDetail
from app.services.context_analyzer import ContextAnalyzer

PLACEHOLDER_PATTERNS = (
    "informasi tidak", "tidak tersedia", "tidak disebutkan", "tidak disediakan", "tidak ada",
    "tidak ditemukan", "tidak diketahui", "belum tersedia", "information not", "not available",
    "not found", "n/a",
)
SUMMARY_ROW = re.compile(r"^(?:sub\s*total|grand\s*total|total\s*[a-z0-9+]*|jumlah\s*total|total\s*keseluruhan)\b", re.IGNORECASE)
# Field yang diisi deterministik dari tabel hasil parser (extract_items_from_markdown_tables /
# extract_tables_from_markdown + _reconcile_items). Field ini dibuang dari schema pass-1 supaya
# LLM tidak menghabiskan ribuan token output mengetik ulang tabel yang sudah kita miliki —
# pada dokumen uji, dua field ini saja menyumbang 69% token output.
DETERMINISTIC_FIELDS = (
    "List Item/Barang", "Daftar Penawaran Harga", "Daftar Barang/Pekerjaan Diserahkan",
    "Daftar Tabel Terstruktur",
)
# Field deterministik tidak boleh ikut menghitung rasio null — kalau ikut, `items` yang masih
# kosong saat pass-1 akan memicu retry yang tidak perlu.
NULL_COUNT_SKIP = set(DETERMINISTIC_FIELDS)
CHARS_PER_TOKEN = 3.0  # estimasi kasar untuk teks Indonesia pada tokenizer Qwen


def is_placeholder(value: Any) -> bool:
    """Nilai kosong atau kalimat penolakan LLM ("tidak disebutkan") dianggap null."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    v = value.strip().lower()
    return v in ("", "null", "none", "-", "[]") or any(p in v for p in PLACEHOLDER_PATTERNS)


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == 0.0 or value == [] or is_placeholder(value)


class OllamaExtractor:
    def __init__(self, model_name: str = None, base_url: str = None):
        self.model_name = model_name or config.OLLAMA_MODEL
        self.base_url = base_url or config.OLLAMA_BASE_URL
        self.client = ollama.Client(host=self.base_url, timeout=config.OLLAMA_TIMEOUT)
        self.context_analyzer = ContextAnalyzer()
        self.llm_calls = 0
        self.llm_seconds = 0.0
        # Prefix dokumen terakhir, dipakai ulang oleh extract_* agar panggilan susulan
        # (pengisian item / kolom opsional) tetap kena KV-cache Ollama.
        self._doc_text: str = ""
        self._system_prompt: str = ""

    # ------------------------------------------------------------------ LLM plumbing
    def reset_stats(self) -> None:
        self.llm_calls = 0
        self.llm_seconds = 0.0

    def _options(self) -> Dict[str, Any]:
        return {"temperature": config.OLLAMA_TEMPERATURE, "seed": config.OLLAMA_SEED,
                "num_ctx": config.OLLAMA_NUM_CTX, "num_predict": config.OLLAMA_NUM_PREDICT}

    def _chat(self, messages: List[Dict[str, str]], fmt: Any = None) -> str:
        prompt_chars = sum(len(m["content"]) for m in messages)
        est_tokens = int(prompt_chars / CHARS_PER_TOKEN)
        if est_tokens > config.OLLAMA_NUM_CTX * 0.8:
            logger.warning(f"⚠️  Prompt ~{est_tokens} token mendekati num_ctx={config.OLLAMA_NUM_CTX}; bagian awal bisa terpotong.")
        started = time.time()
        try:
            response = self.client.chat(model=self.model_name, messages=messages, format=fmt,
                                        options=self._options(), keep_alive=config.OLLAMA_KEEP_ALIVE)
        except httpx.TimeoutException as e:
            raise TimeoutError(
                f"Ollama tidak merespons dalam {config.OLLAMA_TIMEOUT:.0f}s ({est_tokens} token prompt). "
                f"Cek: aplikasi Ollama berjalan, RAM tidak penuh (tutup aplikasi berat), "
                f"atau naikkan OLLAMA_TIMEOUT."
            ) from e
        except httpx.ConnectError as e:
            raise ConnectionError(f"Tidak bisa terhubung ke Ollama di {self.base_url}. Buka aplikasi Ollama atau jalankan `ollama serve`.") from e
        elapsed = time.time() - started
        self.llm_calls += 1
        self.llm_seconds += elapsed
        # Rincian prefill vs decode: satu-satunya cara melihat apakah KV-cache Ollama kena.
        # prefill besar berulang = prefix prompt berubah (lihat _doc_first_messages).
        p_tok, p_ns = response.get("prompt_eval_count", 0), response.get("prompt_eval_duration", 0) or 1
        e_tok, e_ns = response.get("eval_count", 0), response.get("eval_duration", 0) or 1
        logger.info(
            f"   ⏱  prefill {p_tok} tok/{p_ns / 1e9:.1f}s ({p_tok / (p_ns / 1e9):.0f} tok/s) · "
            f"decode {e_tok} tok/{e_ns / 1e9:.1f}s ({e_tok / (e_ns / 1e9):.1f} tok/s) · total {elapsed:.1f}s"
        )
        return response["message"]["content"]

    # --- prefix caching -------------------------------------------------
    # Ollama me-reuse KV-cache selama prefix token sebuah prompt identik dengan panggilan
    # sebelumnya. Pada mesin M4 ini, dokumen 6.400 token butuh ~41 detik untuk di-prefill;
    # kalau prefix-nya sama persis, panggilan berikutnya hanya ~0,3 detik.
    #
    # Karena itu SEMUA panggilan untuk satu dokumen memakai susunan yang sama:
    #     system = system_prompt   (tetap)
    #     user   = <dokumen>  +  "--- TUGAS: ..."   (dokumen dulu, tugas DI AKHIR)
    #
    # Menaruh tugas/daftar field di depan dokumen akan mengubah prefix dan membuang
    # ~41 detik per panggilan. Jangan dibalik.
    @staticmethod
    def _doc_first_messages(system_prompt: str, doc_text: str, task: str) -> List[Dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{doc_text}\n\n---\nTUGAS:\n{task}"},
        ]

    @staticmethod
    def _slim_schema(schema_class: type, drop_fields: Tuple[str, ...]) -> Dict[str, Any]:
        """JSON-schema tanpa field yang diisi deterministik, agar LLM tidak mengetik ulang tabel."""
        schema = schema_class.model_json_schema()
        props = schema.get("properties", {})
        for alias in drop_fields:
            props.pop(alias, None)
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r not in drop_fields]
        return schema

    def _extract_with_llm(self, system_prompt: str, doc_text: str, task: str, schema_class: type,
                          drop_fields: Tuple[str, ...] = ()) -> BaseModel:
        fmt = self._slim_schema(schema_class, drop_fields) if drop_fields else schema_class.model_json_schema()
        content = self._chat(self._doc_first_messages(system_prompt, doc_text, task), fmt=fmt)
        return schema_class.model_validate_json(content)

    def _ask_json(self, system_prompt: str, doc_text: str, task: str) -> Dict[str, Any]:
        """Panggilan JSON bebas-skema yang tetap memakai prefix dokumen yang sama (cache hit)."""
        try:
            content = self._chat(self._doc_first_messages(system_prompt, doc_text, task), fmt="json")
            start, end = content.find("{"), content.rfind("}")
            return json.loads(content[start:end + 1]) if start != -1 and end != -1 else {}
        except Exception as e:  # targeted scan bersifat best-effort
            logger.debug(f"Targeted LLM call gagal: {e}")
            return {}

    # ------------------------------------------------------------------ null tracking
    def _count_null_fields(self, data: Dict[str, Any], prefix: str = "") -> Tuple[int, int, List[str]]:
        total, nulls, null_names = 0, 0, []
        for key, value in data.items():
            if key in NULL_COUNT_SKIP:
                continue
            current_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                sub_total, sub_nulls, sub_names = self._count_null_fields(value, current_key)
                total, nulls = total + sub_total, nulls + sub_nulls
                null_names.extend(sub_names)
                continue
            total += 1
            if _is_empty(value) and not (value == 0.0 and key.lower() == "volume"):
                nulls += 1
                null_names.append(current_key)
        return total, nulls, null_names

    def _merge_retry_result(self, original: Dict[str, Any], retry_result: Dict[str, Any]) -> Dict[str, Any]:
        """Isi hanya field yang sebelumnya kosong; nilai yang sudah ada tidak ditimpa."""
        merged = dict(original)
        for key, value in retry_result.items():
            if key not in merged:
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_retry_result(merged[key], value)
            elif _is_empty(merged.get(key)) and not _is_empty(value):
                merged[key] = value
                logger.info(f"  🔄 Filled: {key} = {str(value)[:80]}")
        return merged

    _is_placeholder_value = staticmethod(is_placeholder)

    # ------------------------------------------------------------------ targeted clause
    @staticmethod
    def _only_null_keys(templates: Dict[str, Any], null_fields: List[str]) -> Dict[str, Any]:
        """
        Buang kunci template yang sudah terisi, tapi HANYA di level teratas.

        Objek bersarang (mis. "Pihak Pertama") dikirim utuh walau cuma satu sub-field yang
        null: memotongnya jadi satu sub-field saja membuat model kehilangan penanda untuk
        membedakan Pihak Pertama dan Pihak Kedua, dan "Nama Representative" kedua pihak
        jadi gagal terisi. Hematnya sedikit, ruginya besar.
        """
        return {
            key: value for key, value in templates.items()
            if key in null_fields or any(f.startswith(f"{key}.") for f in null_fields)
        }

    def _run_targeted_groups(self, system_prompt: str, doc_text: str,
                             null_fields: List[str], groups: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Panggilan perbaikan untuk field yang masih null.

        Dokumen penuh (`doc_text`) dipakai sebagai prefix supaya KV-cache Ollama kena
        (prefill ~0,5 detik, bukan ~80 detik). Cuplikan klausul TIDAK ikut dikirim: dulu
        cuplikan itu menggantikan dokumen, sekarang dokumennya sudah utuh di prefix, jadi
        menempelkannya lagi hanya menduplikasi isi — sempat membengkakkan prompt targeted
        dari 10.300 jadi 16.300 token dan memperlambat decode hampir dua kali lipat.
        Arahan lokasi cukup lewat kalimat `task` tiap grup.
        """
        triggered = [g for g in groups if g["trigger"](null_fields)]
        if not triggered:
            return {}

        # Jika hanya 1 grup yang ter-trigger, jalankan langsung
        if len(triggered) == 1:
            g = triggered[0]
            task = (f"{g['intro']}\n{g['task']}\n"
                    f"Jika tidak ada di dokumen, isi null. Keluarkan JSON:\n{g['template']}")
            result = self._ask_json(system_prompt, doc_text, task)
            wrap = g.get("wrap")
            cleaned = {k: v for k, v in result.items() if not _is_empty(v) and not is_placeholder(v)}
            return {wrap: cleaned} if wrap and cleaned else cleaned

        # Jika banyak grup, gabungkan jadi SATU panggilan LLM terpadu (menghemat waktu secara drastis!)
        intros, templates = [], {}
        for g in triggered:
            intros.append(f"- {g['intro']} {g['task']}")
            try:
                t_dict = json.loads(g["template"])
                if g.get("wrap"):
                    templates[g["wrap"]] = t_dict
                else:
                    templates.update(t_dict)
            except Exception:
                pass

        if not templates:
            return {}

        # Hanya minta field yang memang masih null. Tanpa filter ini, satu grup yang terpicu
        # menyeret seluruh field-nya (termasuk yang sudah terisi di pass 1) ke dalam output —
        # decode adalah biaya terbesar yang tersisa, jadi tiap token output harus dibayar.
        templates = self._only_null_keys(templates, null_fields)
        if not templates:
            return {}

        combined_template = json.dumps(templates, indent=2)
        task = (
            "Beberapa field belum terisi. Cari langsung di dokumen di atas:\n"
            + "\n".join(intros)
            + "\n\nJika tidak ada di dokumen, isi null. "
            f"Keluarkan HANYA JSON sesuai format berikut:\n{combined_template}"
        )
        result = self._ask_json(system_prompt, doc_text, task)
        cleaned = {}
        for k, v in result.items():
            if isinstance(v, dict):
                v_clean = {sub_k: sub_v for sub_k, sub_v in v.items() if not is_placeholder(sub_v) and not _is_empty(sub_v)}
                if v_clean:
                    cleaned[k] = v_clean
            elif not _is_empty(v) and not is_placeholder(v):
                cleaned[k] = v
        return cleaned

    def _extract_targeted_clauses(self, system_prompt: str, doc_text: str,
                                  null_fields: List[str]) -> Dict[str, Any]:
        groups = [
            {
                "trigger": lambda nf: any(f.startswith("Pihak") for f in nf),
                "intro": "Berikut pembuka/preamble dokumen kontrak:",
                "task": "Tugas: Ekstrak Pihak Pertama (Pemberi Perintah / Klien) dan Pihak Kedua (Penyedia / Pelaksana). "
                        "Masing-masing pihak punya alamat sendiri; jangan menduplikasi alamat. Sertakan NPWP jika tertera.",
                "template": '{"Pihak Pertama": {"Nama Perusahaan": "...", "NPWP": "...", "Nama Representative": "...", "Jabatan": "...", "Alamat": "..."}, '
                            '"Pihak Kedua": {"Nama Perusahaan": "...", "NPWP": "...", "Nama Representative": "...", "Jabatan": "...", "Alamat": "..."}}',
            },
            {
                "trigger": lambda nf: any(f in nf for f in ["Nama Bank", "Lokasi Cabang Bank", "Nomor Rekening Bank", "Nama Rekening Bank", "Mekanisme Skema Pembayaran", "Ketentuan Pembayaran"]),
                "intro": "Berikut cuplikan pasal CARA PEMBAYARAN dari dokumen kontrak:",
                "task": "Tugas: Ekstrak Nama Bank, Lokasi Cabang Bank, Nomor Rekening Bank, Nama Rekening Bank, Mekanisme Skema Pembayaran, dan butir-butir Ketentuan Pembayaran (array).",
                "template": '{"Nama Bank": "...", "Lokasi Cabang Bank": "...", "Nomor Rekening Bank": "...", "Nama Rekening Bank": "...", "Mekanisme Skema Pembayaran": "...", "Ketentuan Pembayaran": ["..."]}',
            },
            {
                "trigger": lambda nf: any(f in nf for f in ["Jangka Waktu", "Durasi Kerja", "Persentase Sanksi/Penalti", "Garansi", "Syarat Lampiran Wajib BAST", "Klausul Jaminan"]),
                "intro": "Berikut cuplikan pasal WAKTU, DENDA/SANKSI, GARANSI, JAMINAN, dan BAST dari dokumen kontrak:",
                "task": "Tugas: Ekstrak Jangka Waktu, Durasi Kerja, Persentase Sanksi/Penalti, Garansi, Klausul Jaminan, dan Syarat Lampiran Wajib BAST.",
                "template": '{"Jangka Waktu": "...", "Durasi Kerja": "...", "Persentase Sanksi/Penalti": "...", "Garansi": "...", "Klausul Jaminan": {"Nomor Pasal": "...", "Uraian Jaminan": "..."}, "Syarat Lampiran Wajib BAST": ["..."]}',
            },
            {
                "trigger": lambda nf: any(f in nf for f in ["Nomor Kontrak Kerja", "Nomor Kontrak Internal", "Daftar Nomor Kontrak", "Nama Pekerjaan", "Tanggal Negosiasi", "Dokumen Pendukung"]),
                "intro": "Berikut cuplikan judul, nomor dokumen, pembuka (preamble), konsiderans surat pendukung, dan lampiran dari dokumen kontrak:",
                "task": "Tugas: Ekstrak Nomor Kontrak Kerja, Nomor Kontrak Internal (jika ada nomor kedua), Daftar Nomor Kontrak (array), Nama Pekerjaan, Tanggal Negosiasi, dan Dokumen Pendukung (array of {Nama Dokumen, Nomor Dokumen, Tanggal Dokumen}).",
                "template": '{"Nomor Kontrak Kerja": "...", "Nomor Kontrak Internal": "...", "Daftar Nomor Kontrak": ["..."], "Nama Pekerjaan": "...", "Tanggal Negosiasi": "...", "Dokumen Pendukung": [{"Nama Dokumen": "...", "Nomor Dokumen": "...", "Tanggal Dokumen": "..."}]}',
            },
            {
                "trigger": lambda nf: any(f in nf for f in ["Lokasi", "Tanggal Pembuatan Dokumen", "Daftar Penandatangan", "Informasi Bea Meterai"]),
                "intro": "Berikut bagian akhir dokumen kontrak (tanda tangan, meterai, & penutup):",
                "task": "Tugas: Ekstrak Lokasi, Tanggal Pembuatan Dokumen, Daftar Penandatangan (array of {Nama, Jabatan}), dan Informasi Bea Meterai.",
                "template": '{"Lokasi": "...", "Tanggal Pembuatan Dokumen": "...", "Daftar Penandatangan": [{"Nama": "...", "Jabatan": "..."}], "Informasi Bea Meterai": "..."}',
            },
        ]
        return self._run_targeted_groups(system_prompt, doc_text, null_fields, groups)

    def _extract_targeted_sph_clauses(self, system_prompt: str, doc_text: str,
                                      null_fields: List[str]) -> Dict[str, Any]:
        groups = [
            {
                "trigger": lambda nf: any(f in nf for f in ["Perihal / Nama Pekerjaan", "Nomor SPH", "Tanggal SPH"]),
                "intro": "Berikut pembuka dan header dari Surat Penawaran Harga (SPH):",
                "task": "Ekstrak: Perihal / Nama Pekerjaan, Nomor SPH, Tanggal SPH.",
                "template": '{"Perihal / Nama Pekerjaan": "...", "Nomor SPH": "...", "Tanggal SPH": "..."}',
            },
            {
                "trigger": lambda nf: any(f.startswith("Vendor.") for f in nf),
                "intro": "Berikut cuplikan informasi vendor dari SPH:",
                "task": "Ekstrak: Nama Vendor, Alamat Vendor, Kontak / Email, NPWP.",
                "template": '{"Nama Vendor": "...", "Alamat Vendor": "...", "Kontak / Email": "...", "NPWP": "..."}',
                "wrap": "Vendor",
            },
            {
                "trigger": lambda nf: any(f in nf for f in ["Garansi / SLA", "Syarat dan Ketentuan", "Masa Berlaku Penawaran", "Jangka Waktu Pengiriman"]),
                "intro": "Berikut cuplikan syarat dan ketentuan dari SPH:",
                "task": "Ekstrak: Garansi / SLA, Syarat dan Ketentuan (array), Masa Berlaku Penawaran, Jangka Waktu Pengiriman.",
                "template": '{"Garansi / SLA": "...", "Syarat dan Ketentuan": ["..."], "Masa Berlaku Penawaran": "...", "Jangka Waktu Pengiriman": "..."}',
            },
        ]
        return self._run_targeted_groups(system_prompt, doc_text, null_fields, groups)

    # ------------------------------------------------------------------ context budget
    def _prepare_effective_text(self, enriched_text: str, max_chars: int = None) -> str:
        """Dokumen panjang: pertahankan preamble, pasal kunci, seluruh tabel, dan penutup."""
        max_chars = max_chars or config.EFFECTIVE_TEXT_MAX_CHARS
        if len(enriched_text) <= max_chars:
            return enriched_text

        logger.info(f"📑 Dokumen tebal ({len(enriched_text)} karakter) → section & table preservation sampling")
        lines = enriched_text.splitlines()
        table_lines = [l for l in lines if l.strip().startswith("|") and l.strip().endswith("|")]
        non_table = [l for l in lines if not (l.strip().startswith("|") and l.strip().endswith("|"))]

        preamble = "\n".join(non_table[:60])
        ending = "\n".join(non_table[-40:]) if len(non_table) > 40 else ""
        middle_keywords = ["pasal", "cara pembayaran", "rekening", "bank", "jangka waktu", "denda", "sanksi", "total",
                           "sub total", "ppn", "garansi", "sla", "lampiran", "bast", "serah terima", "[section", "[key"]
        middle = [l for l in (non_table[60:-40] if len(non_table) > 100 else []) if any(kw in l.lower() for kw in middle_keywords)]
        parts = [
            preamble,
            "\n[...PASAL-PASAL DAN KLAUSUL KONTRAK...]\n" + "\n".join(middle[:40]) if middle else "",
            "\n[...RINCIAN LENGKAP TABEL PEKERJAAN / BILL OF QUANTITIES (BoQ)...]\n" + "\n".join(table_lines) if table_lines else "",
            "\n[...BAGIAN AKHIR DOKUMEN & TANDA TANGAN...]\n" + ending if ending else "",
        ]
        effective = "\n\n".join(p for p in parts if p.strip())
        if len(effective) > max_chars * 2:
            logger.warning(f"⚠️  Teks efektif masih {len(effective)} karakter (tabel sangat besar); pertimbangkan ekstraksi tabel per halaman.")
        return effective

    # ------------------------------------------------------------------ shared pipeline
    def _run_extraction(
        self,
        markdown_text: str,
        doc_label: str,
        system_prompt: str,
        schema_class: type,
        retry_template: str,
        targeted: Callable[[str, str, List[str]], Dict[str, Any]],
        targeted_min_chars: int,
    ) -> BaseModel:
        effective_text = self._prepare_effective_text(self.context_analyzer.enrich_markdown(markdown_text))
        # Prefix bersama untuk SEMUA panggilan dokumen ini (lihat _doc_first_messages).
        doc_text = f"Berikut teks dokumen {doc_label} hasil parsing:\n\n{effective_text}"
        self._doc_text, self._system_prompt = doc_text, system_prompt

        logger.info(f"🤖 Extraction pass 1 [{self.model_name}] (num_ctx={config.OLLAMA_NUM_CTX})...")
        extracted = self._extract_with_llm(
            system_prompt, doc_text,
            "Ekstrak seluruh informasi sesuai schema JSON.",
            schema_class, drop_fields=DETERMINISTIC_FIELDS,
        )
        data = extracted.model_dump(by_alias=True)
        total, nulls, null_names = self._count_null_fields(data)
        logger.info(f"📊 Pass 1: {total - nulls}/{total} field terisi")

        def _apply(update: Dict[str, Any], label: str) -> None:
            nonlocal extracted, data, total, nulls, null_names
            if not update:
                return
            try:
                extracted = schema_class.model_validate(self._merge_retry_result(data, update))
            except Exception as e:
                logger.warning(f"Validasi setelah {label} gagal: {e}")
                return
            data = extracted.model_dump(by_alias=True)
            total, nulls, null_names = self._count_null_fields(data)
            logger.info(f"📊 Setelah {label}: {total - nulls}/{total} field terisi")

        if null_names and len(markdown_text) > targeted_min_chars:
            logger.info(f"🎯 Targeted clause refinement untuk {len(null_names)} field null...")
            _apply(targeted(system_prompt, doc_text, null_names), "targeted scan")

        for attempt in range(config.MAX_EXTRACTION_RETRIES):
            if nulls / max(total, 1) <= config.NULL_FIELD_THRESHOLD:
                break
            logger.info(f"🔄 Retry {attempt + 1} (null ratio {nulls / max(total, 1):.0%})...")
            # Retry hanya meminta field yang masih kosong, bukan membangkitkan ulang seluruh
            # schema. Sebelumnya satu retry mengetik ulang ~2.300 token (termasuk tabel) hanya
            # untuk mengisi beberapa field.
            retry_task = retry_template.format(null_fields="\n".join(f"- {n}" for n in null_names))
            _apply(self._ask_json(system_prompt, doc_text, retry_task), f"retry {attempt + 1}")

        # Fallback deterministic dari context analyzer untuk field yang masih null
        hints = self.context_analyzer.get_entity_hints(markdown_text)
        fallback_updates = {}
        for k, v in hints.items():
            if isinstance(v, str) and v and _is_empty(data.get(k)):
                fallback_updates[k] = v
        if fallback_updates:
            _apply(fallback_updates, "context hints fallback")

        return extracted

    # ------------------------------------------------------------------ deterministic reconciliation
    @staticmethod
    def _reconcile_items(llm_items: List[BaseModel], table_items: List[Dict[str, Any]], item_model: type,
                         desc_attr: str, total_attr: str, total_alias: str,
                         reference_amounts: List[float]) -> List[BaseModel]:
        """
        Pilih daftar item dari tabel (deterministik) atau LLM berdasarkan kecocokan jumlah dengan
        subtotal/total, bukan sekadar jumlah baris. Field opsional dari LLM (spesifikasi, merek)
        disalin ke item tabel jika jumlah baris sama.
        """
        if not table_items:
            return llm_items

        def matches_reference(amount: float) -> bool:
            return any(ref and amounts_equal(amount, ref, 1000.0) for ref in reference_amounts)

        table_ok = matches_reference(sum(float(t.get(total_alias) or 0) for t in table_items))
        llm_ok = matches_reference(sum(float(getattr(i, total_attr) or 0) for i in llm_items))
        llm_has_summary = any(SUMMARY_ROW.match(getattr(i, desc_attr).strip()) for i in llm_items)

        use_table = (table_ok and not llm_ok) or (table_ok == llm_ok and (len(table_items) >= len(llm_items) or llm_has_summary))
        if not use_table:
            return llm_items

        try:
            validated = [item_model.model_validate(t) for t in table_items]
        except Exception as e:
            logger.warning(f"Item tabel tidak valid, tetap memakai item LLM: {e}")
            return llm_items
        if len(validated) == len(llm_items):
            for table_item, llm_item in zip(validated, llm_items):
                for attr in ("spesifikasi", "brand_merek", "nomor_part", "keterangan", "periode"):
                    if hasattr(table_item, attr) and getattr(table_item, attr) is None:
                        setattr(table_item, attr, getattr(llm_item, attr, None))
        logger.info(f"📊 Item dari tabel dipakai ({len(llm_items)} LLM → {len(validated)} tabel, cocok subtotal={table_ok})")
        return validated

    @staticmethod
    def _clean_and_number_items(items: List[BaseModel], desc_attr: str, no_attr: str) -> List[BaseModel]:
        cleaned = []
        for item in items:
            desc = (getattr(item, desc_attr) or "").strip()
            if SUMMARY_ROW.match(desc):
                continue
            clean_no, clean_text = clean_desc_and_extract_item_no(desc, getattr(item, no_attr) or "")
            setattr(item, desc_attr, clean_text)
            if clean_no and clean_no.isdigit():
                setattr(item, no_attr, clean_no)
            cleaned.append(item)
        for idx, item in enumerate(cleaned):
            if (getattr(item, no_attr) or "").strip() in ("", "0", "None", "null", "-"):
                setattr(item, no_attr, str(idx + 1))
        if len(cleaned) > 1 and len({getattr(i, no_attr) for i in cleaned}) == 1:
            for idx, item in enumerate(cleaned):
                setattr(item, no_attr, str(idx + 1))
        return cleaned

    @staticmethod
    def _find_terbilang_in_text(markdown_text: str, amount: float) -> Optional[str]:
        """Cari kalimat terbilang di dokumen yang nilainya sama dengan nominal (deterministik)."""
        for match in re.finditer(r'\(\s*([A-Za-z][A-Za-z\s]{10,200}?(?:Rupiah|rupiah))\s*\)', markdown_text):
            candidate = re.sub(r'\s+', ' ', match.group(1)).strip()
            if terbilang_to_number(candidate) == int(round(amount)):
                return candidate
        return None

    # ------------------------------------------------------------------ item fallback
    def _fill_items_via_llm(self, item_model: type, alias: str, key_attrs: Tuple[str, ...]) -> List[BaseModel]:
        """
        Jaring pengaman: dipakai HANYA kalau parser tidak menemukan baris tabel sama sekali
        (mis. daftar item ditulis sebagai paragraf, bukan tabel). Memakai prefix dokumen yang
        sama dengan pass-1, jadi prefill-nya nyaris gratis.
        """
        if not self._doc_text:
            return []
        logger.info(f"📋 Tabel tidak terdeteksi parser → minta LLM menyusun '{alias}'...")
        result = self._ask_json(
            self._system_prompt, self._doc_text,
            f"Kumpulkan seluruh baris barang/jasa/pekerjaan dari dokumen di atas.\n"
            f"Keluarkan HANYA JSON: {{\"{alias}\": [{{...}}, ...]}} "
            f"dengan tiap baris memuat minimal: {', '.join(key_attrs)}.\n"
            f"Jika dokumen memang tidak memuat rincian item, keluarkan {{\"{alias}\": []}}.",
        )
        rows = result.get(alias) or []
        if not isinstance(rows, list):
            return []
        items: List[BaseModel] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                items.append(item_model.model_validate(row))
            except Exception as e:
                logger.debug(f"Baris item dari LLM tidak valid, dilewati: {e}")
        if items:
            logger.info(f"📋 {len(items)} item disusun oleh LLM (fallback)")
        return items

    OPTIONAL_ITEM_ATTRS = ("spesifikasi", "brand_merek", "nomor_part", "periode", "keterangan")

    def _enrich_item_columns(self, items: List[BaseModel], desc_attr: str) -> None:
        """
        Baris & angka sudah deterministik dari tabel parser. Kolom deskriptif (spesifikasi,
        merek, part number) sering tersebar di paragraf lain, jadi kolom itu — dan HANYA kolom
        itu — diisi LLM lewat satu panggilan kecil di atas prefix dokumen yang sudah ter-cache.
        """
        if not items or not self._doc_text:
            return
        attrs = [a for a in self.OPTIONAL_ITEM_ATTRS if hasattr(items[0], a)]
        missing = [a for a in attrs if all(getattr(i, a, None) in (None, "") for i in items)]
        if not missing:
            return

        listing = "\n".join(f"{n}. {(getattr(i, desc_attr) or '').strip()}" for n, i in enumerate(items, 1))
        alias_of = {a: items[0].model_fields[a].alias or a for a in missing}
        task = (
            "Baris tabel di bawah ini sudah benar dan TIDAK boleh diubah. Tugasmu hanya melengkapi "
            f"kolom {', '.join(alias_of.values())} untuk tiap baris, berdasarkan dokumen di atas.\n\n"
            f"{listing}\n\n"
            "Keluarkan HANYA JSON: {\"rows\": [{\"no\": 1, "
            + ", ".join(f'"{v}": "..."' for v in alias_of.values())
            + "}, ...]}\nKosongkan (null) kolom yang tidak disebutkan di dokumen. Jangan mengarang."
        )
        rows = (self._ask_json(self._system_prompt, self._doc_text, task) or {}).get("rows") or []
        filled = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row.get("no", 0)) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(items):
                continue
            for attr, alias in alias_of.items():
                value = row.get(alias)
                if not _is_empty(value) and getattr(items[idx], attr, None) in (None, ""):
                    setattr(items[idx], attr, value)
                    filled += 1
        if filled:
            logger.info(f"🧩 {filled} kolom opsional item dilengkapi LLM ({', '.join(alias_of.values())})")

    def _apply_hint_fallbacks(self, extracted: BaseModel, markdown_text: str, mapping: Dict[str, str]) -> None:
        hints = self.context_analyzer.get_entity_hints(markdown_text)
        for attr, hint_key in mapping.items():
            if is_placeholder(getattr(extracted, attr, None)) and hints.get(hint_key):
                setattr(extracted, attr, hints[hint_key])
                logger.info(f"📍 Fallback regex: {hint_key} = {hints[hint_key]}")

    # ------------------------------------------------------------------ public API
    def extract_contract(self, markdown_text: str) -> ContractExtractionSchema:
        extracted: ContractExtractionSchema = self._run_extraction(
            markdown_text, "kontrak/SPK", CONTRACT_EXTRACTION_SYSTEM_PROMPT, ContractExtractionSchema,
            CONTRACT_RETRY_PROMPT_TEMPLATE, self._extract_targeted_clauses, targeted_min_chars=3000,
        )

        extracted.items = self._reconcile_items(
            extracted.items, extract_items_from_markdown_tables(markdown_text, doc_type="contract"),
            ItemBarangPekerjaan, "deskripsi", "jumlah_harga", "Jumlah Harga",
            [extracted.sub_total, extracted.total_harga_pekerjaan],
        )
        if not extracted.items:
            extracted.items = self._fill_items_via_llm(
                ItemBarangPekerjaan, "List Item/Barang", ("Deskripsi", "Volume", "Harga Satuan", "Jumlah Harga"))
        extracted.items = self._clean_and_number_items(extracted.items, "deskripsi", "nomor_item")
        self._enrich_item_columns(extracted.items, "deskripsi")

        self._apply_hint_fallbacks(extracted, markdown_text, {
            "lokasi": "Lokasi", "tanggal_pembuatan_dokumen": "Tanggal Pembuatan Dokumen",
            "tanggal_negosiasi": "Tanggal Negosiasi", "jangka_waktu": "Jangka Waktu", "durasi_kerja": "Durasi Kerja",
            "persentase_penalti": "Persentase Sanksi/Penalti", "nama_bank": "Nama Bank", "cabang_bank": "Lokasi Cabang Bank",
            "nomor_rekening": "Nomor Rekening Bank", "nama_rekening": "Nama Rekening Bank",
        })

        # sub_total/total_harga_pekerjaan sekarang Optional (dokumen ringkas seperti Nota Pesanan
        # kadang tidak memisahkan keduanya) -> None > None akan TypeError tanpa guard ini.
        if ((extracted.total_ppn is None or extracted.total_ppn == 0.0)
                and extracted.total_harga_pekerjaan and extracted.sub_total
                and extracted.total_harga_pekerjaan > extracted.sub_total > 0):
            extracted.total_ppn = round(extracted.total_harga_pekerjaan - extracted.sub_total, 2)
            logger.info(f"💰 Total PPN dihitung dari Total - Sub Total: {extracted.total_ppn}")

        for amount in (extracted.total_harga_pekerjaan, extracted.sub_total):
            if amount and terbilang_to_number(extracted.jumlah_terbilang or "") != int(round(amount)):
                found = self._find_terbilang_in_text(markdown_text, amount)
                if found:
                    logger.info(f"🔤 Terbilang dikoreksi dari teks dokumen: {found}")
                    extracted.jumlah_terbilang = found
                    break

        self._reconcile_parties(extracted, markdown_text)

        if not extracted.daftar_tabel_terstruktur:
            extracted.daftar_tabel_terstruktur = extract_tables_from_markdown(markdown_text) or None
        return extracted

    @staticmethod
    def _reconcile_bast_items(llm_items: List[BASTItemDetail], table_items: List[Dict[str, Any]]) -> List[BASTItemDetail]:
        """
        BAST umumnya tidak punya kolom harga, jadi tidak ada nilai numerik untuk memvalidasi
        silang seperti pada kontrak/SPH (_reconcile_items). Pilih tabel hasil parsing
        deterministik jika baris tabelnya sama banyak/lebih banyak dari LLM, atau LLM sama
        sekali tidak menghasilkan item.
        """
        if not table_items or (llm_items and len(table_items) < len(llm_items)):
            return llm_items
        try:
            validated = [BASTItemDetail.model_validate(t) for t in table_items]
        except Exception as e:
            logger.warning(f"Item tabel BAST tidak valid, tetap memakai item LLM: {e}")
            return llm_items
        logger.info(f"📊 Item BAST dari tabel dipakai ({len(llm_items)} LLM → {len(validated)} tabel)")
        return validated

    def _reconcile_parties(self, extracted: Union[ContractExtractionSchema, BASTExtractionSchema], markdown_text: str) -> None:
        hints = self.context_analyzer.get_entity_hints(markdown_text)
        p1_hint, p2_hint = hints.get("Pihak Pertama") or {}, hints.get("Pihak Kedua") or {}
        if not (p1_hint and p2_hint):
            return
        p1, p2 = extracted.pihak_pertama, extracted.pihak_kedua

        if p1.alamat and p1.alamat == p2.alamat and p1_hint.get("alamat") and p2_hint.get("alamat") and p1_hint["alamat"] != p2_hint["alamat"]:
            logger.info("🔧 Alamat pihak pertama & kedua identik → dikoreksi dari preamble")
            p1.alamat, p2.alamat = p1_hint["alamat"], p2_hint["alamat"]

        c1, c2 = (p1_hint.get("nama_perusahaan") or "").strip().lower(), (p2_hint.get("nama_perusahaan") or "").strip().lower()
        if c1 and c2:
            e1, e2 = p1.nama_perusahaan.strip().lower(), p2.nama_perusahaan.strip().lower()
            if (e1 == c2 and e2 == c1) or e1 == e2:
                logger.info("🔄 Nama perusahaan pihak tertukar/identik → dikoreksi dari preamble")
                p1.nama_perusahaan, p2.nama_perusahaan = p1_hint["nama_perusahaan"], p2_hint["nama_perusahaan"]

        for party, hint in ((p1, p1_hint), (p2, p2_hint)):
            for attr in ("nama_representative", "jabatan"):
                if is_placeholder(getattr(party, attr)) and hint.get(attr):
                    setattr(party, attr, hint[attr])

    def extract_sph(self, markdown_text: str) -> SPHExtractionSchema:
        extracted: SPHExtractionSchema = self._run_extraction(
            markdown_text, "SPH", SPH_EXTRACTION_SYSTEM_PROMPT, SPHExtractionSchema,
            SPH_RETRY_PROMPT_TEMPLATE, self._extract_targeted_sph_clauses, targeted_min_chars=1000,
        )
        extracted.items = self._reconcile_items(
            extracted.items, extract_items_from_markdown_tables(markdown_text, doc_type="sph"),
            SPHItemDetail, "nama_item", "total_harga", "Total Harga",
            [extracted.subtotal, extracted.grand_total],
        )
        if not extracted.items:
            extracted.items = self._fill_items_via_llm(
                SPHItemDetail, "Daftar Penawaran Harga", ("Nama Item", "Qty", "Harga Satuan", "Total Harga"))
        extracted.items = self._clean_and_number_items(extracted.items, "nama_item", "nomor")
        self._enrich_item_columns(extracted.items, "nama_item")
        if not extracted.daftar_tabel_terstruktur:
            extracted.daftar_tabel_terstruktur = extract_tables_from_markdown(markdown_text) or None
        return extracted

    def extract_bast(self, markdown_text: str) -> BASTExtractionSchema:
        """
        BAST (Berita Acara Serah Terima) -- dokumen yang memicu penagihan (lihat Delivery Ops
        Layer briefing: prioritas utama otomasi). Reuse targeted-clause dari kontrak karena
        keduanya sama-sama memuat blok "PIHAK PERTAMA/KEDUA"; group lain (bank, tanggal
        pembuatan dsb) otomatis tidak terpicu karena nama field BAST berbeda.
        """
        extracted: BASTExtractionSchema = self._run_extraction(
            markdown_text, "BAST (Berita Acara Serah Terima)", BAST_EXTRACTION_SYSTEM_PROMPT, BASTExtractionSchema,
            BAST_RETRY_PROMPT_TEMPLATE, self._extract_targeted_clauses, targeted_min_chars=800,
        )
        extracted.items = self._reconcile_bast_items(
            extracted.items, extract_items_from_markdown_tables(markdown_text, doc_type="bast"),
        )
        if not extracted.items:
            extracted.items = self._fill_items_via_llm(
                BASTItemDetail, "Daftar Barang/Pekerjaan Diserahkan", ("Deskripsi", "Volume", "Satuan"))
        extracted.items = self._clean_and_number_items(extracted.items, "deskripsi", "nomor")
        self._reconcile_parties(extracted, markdown_text)
        if not extracted.daftar_tabel_terstruktur:
            extracted.daftar_tabel_terstruktur = extract_tables_from_markdown(markdown_text) or None
        return extracted
