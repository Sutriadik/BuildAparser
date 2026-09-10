"""
Open ADE — Ollama LLM Extraction Client
Dengan retry logic, context enrichment, dan targeted clause extraction.
"""
import json
import ollama
from typing import Union, List, Tuple, Dict, Any, Optional
from pydantic import BaseModel

from app.config import config
from app.logger import logger
from app.schemas.contract import ContractExtractionSchema
from app.schemas.sph import SPHExtractionSchema
from app.services.context_analyzer import ContextAnalyzer
from app.extractors.prompts import (
    CONTRACT_EXTRACTION_SYSTEM_PROMPT, 
    SPH_EXTRACTION_SYSTEM_PROMPT,
    CONTRACT_RETRY_PROMPT_TEMPLATE,
    SPH_RETRY_PROMPT_TEMPLATE,
)


class OllamaExtractor:
    def __init__(self, model_name: str = None, base_url: str = None):
        self.model_name = model_name or config.OLLAMA_MODEL
        self.base_url = base_url or config.OLLAMA_BASE_URL
        self.client = ollama.Client(host=self.base_url)
        self.context_analyzer = ContextAnalyzer()

    def _count_null_fields(self, data: Dict[str, Any], prefix: str = "") -> Tuple[int, int, List[str]]:
        """
        Menghitung jumlah field null vs total fields, dan mengembalikan daftar field null.
        
        Returns:
            (total_fields, null_count, list_of_null_field_names)
        """
        total = 0
        nulls = 0
        null_names: List[str] = []
        
        for key, value in data.items():
            current_key = f"{prefix}.{key}" if prefix else key
            
            if isinstance(value, dict):
                sub_total, sub_nulls, sub_names = self._count_null_fields(value, current_key)
                total += sub_total
                nulls += sub_nulls
                null_names.extend(sub_names)
            elif isinstance(value, list):
                # Skip list fields from null counting (items bisa kosong)
                total += 1
                if not value:
                    nulls += 1
                    null_names.append(current_key)
            else:
                total += 1
                if value is None or value == "" or value == 0.0:
                    # 0.0 dianggap null untuk field finansial yang seharusnya terisi
                    # Exception: beberapa field memang bisa 0
                    if value == 0.0 and key.lower() in ["volume"]:
                        continue
                    nulls += 1
                    null_names.append(current_key)
        
        return total, nulls, null_names

    def _merge_retry_result(
        self, 
        original: Dict[str, Any], 
        retry_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Menggabungkan hasil retry ke data original. 
        Hanya mengisi field yang sebelumnya null/kosong.
        """
        merged = original.copy()
        
        for key, value in retry_result.items():
            if key not in merged:
                continue
                
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_retry_result(merged[key], value)
            elif merged.get(key) is None or merged.get(key) == "" or merged.get(key) == 0.0:
                if value is not None and value != "" and value != 0.0:
                    merged[key] = value
                    logger.info(f"  🔄 Retry filled: {key} = {str(value)[:80]}")
        
        return merged

    def _extract_with_llm(
        self, 
        system_prompt: str, 
        user_prompt: str,
        schema_class: type
    ) -> BaseModel:
        """
        Memanggil Ollama LLM untuk extraction dengan schema JSON.
        """
        response = self.client.chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            format=schema_class.model_json_schema(),
            options={"temperature": config.OLLAMA_TEMPERATURE}
        )
        
        json_str = response["message"]["content"]
        return schema_class.model_validate_json(json_str)

    def _extract_targeted_clauses(self, markdown_text: str, null_fields: List[str]) -> Dict[str, Any]:
        """
        Melakukan pencarian bagian teks yang relevan secara spesifik (Focused Clause Slicing)
        untuk field-field yang masih null pada dokumen panjang.
        """
        found_data = {}
        lines = markdown_text.splitlines()
        
        # 1. Targeted search for Bank & Payment clause
        bank_keywords = ["bank", "rekening", "cabang", "a.n", "atas nama", "transfer", "pembayaran"]
        if any(f in null_fields for f in ["Nama Bank", "Lokasi Cabang Bank", "Nomor Rekening Bank", "Nama Rekening Bank", "Mekanisme Skema Pembayaran"]):
            relevant_lines = []
            for i, line in enumerate(lines):
                if any(kw in line.lower() for kw in bank_keywords):
                    start = max(0, i - 2)
                    end = min(len(lines), i + 4)
                    relevant_lines.extend(lines[start:end])
            
            if relevant_lines:
                snippet = "\n".join(list(dict.fromkeys(relevant_lines))[:30])
                prompt = (
                    f"Berikut cuplikan pasal CARA PEMBAYARAN dari dokumen kontrak:\n\n"
                    f"{snippet}\n\n"
                    f"Tugas: Ekstrak Nama Bank, Lokasi Cabang Bank, Nomor Rekening Bank, Nama Rekening Bank, dan Mekanisme Skema Pembayaran.\n"
                    f"Keluarkan dalam format JSON:\n"
                    f'{{"Nama Bank": "...", "Lokasi Cabang Bank": "...", "Nomor Rekening Bank": "...", "Nama Rekening Bank": "...", "Mekanisme Skema Pembayaran": "..."}}'
                )
                try:
                    res = self.client.chat(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        options={"temperature": 0.0}
                    )
                    import json
                    content = res["message"]["content"]
                    # parse json from response
                    start_j = content.find("{")
                    end_j = content.rfind("}")
                    if start_j != -1 and end_j != -1:
                        clause_json = json.loads(content[start_j:end_j+1])
                        for k, v in clause_json.items():
                            if v and str(v).lower() not in ["null", "none", "tidak tersedia", "-"]:
                                found_data[k] = v
                except Exception as e:
                    logger.debug(f"Targeted bank extraction failed: {e}")

        # 2. Targeted search for Duration, Jangka Waktu, Sanksi, BAST
        duration_keywords = ["jangka waktu", "delivery", "waktu pelaksanaan", "denda", "sanksi", "hari kalender", "bast"]
        if any(f in null_fields for f in ["Jangka Waktu", "Durasi Kerja", "Persentase Sanksi/Penalti", "Garansi", "Syarat Lampiran Wajib BAST"]):
            relevant_lines = []
            for i, line in enumerate(lines):
                if any(kw in line.lower() for kw in duration_keywords):
                    start = max(0, i - 2)
                    end = min(len(lines), i + 4)
                    relevant_lines.extend(lines[start:end])
            
            if relevant_lines:
                snippet = "\n".join(list(dict.fromkeys(relevant_lines))[:30])
                prompt = (
                    f"Berikut cuplikan pasal WAKTU, DENDA/SANKSI, dan BAST dari dokumen kontrak:\n\n"
                    f"{snippet}\n\n"
                    f"Tugas: Ekstrak Jangka Waktu, Durasi Kerja, Persentase Sanksi/Penalti, Garansi, dan Syarat Lampiran Wajib BAST.\n"
                    f"Keluarkan dalam format JSON:\n"
                    f'{{"Jangka Waktu": "...", "Durasi Kerja": "...", "Persentase Sanksi/Penalti": "...", "Garansi": "...", "Syarat Lampiran Wajib BAST": ["..."]}}'
                )
                try:
                    res = self.client.chat(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        options={"temperature": 0.0}
                    )
                    import json
                    content = res["message"]["content"]
                    start_j = content.find("{")
                    end_j = content.rfind("}")
                    if start_j != -1 and end_j != -1:
                        clause_json = json.loads(content[start_j:end_j+1])
                        for k, v in clause_json.items():
                            if v and str(v).lower() not in ["null", "none", "tidak tersedia", "-", "[]"]:
                                found_data[k] = v
                except Exception as e:
                    logger.debug(f"Targeted duration extraction failed: {e}")

        return found_data

    def _extract_targeted_sph_clauses(self, markdown_text: str, null_fields: List[str]) -> Dict[str, Any]:
        """
        Targeted extraction untuk SPH: vendor info, pricing, terms.
        """
        found_data = {}
        lines = markdown_text.splitlines()
        
        # 1. Vendor & Contact info
        vendor_keywords = ["pt.", "cv.", "npwp", "telp", "email", "alamat", "hormat kami"]
        if any(f in null_fields for f in ["Vendor.Nama Vendor", "Vendor.Alamat Vendor", "Vendor.Kontak / Email", "Vendor.NPWP"]):
            relevant_lines = []
            for i, line in enumerate(lines):
                if any(kw in line.lower() for kw in vendor_keywords):
                    start = max(0, i - 2)
                    end = min(len(lines), i + 3)
                    relevant_lines.extend(lines[start:end])
            
            if relevant_lines:
                snippet = "\n".join(list(dict.fromkeys(relevant_lines))[:20])
                prompt = (
                    f"Berikut cuplikan informasi vendor dari SPH:\n\n{snippet}\n\n"
                    f"Ekstrak: Nama Vendor, Alamat Vendor, Kontak/Email, NPWP.\n"
                    f'JSON: {{"Nama Vendor": "...", "Alamat Vendor": "...", "Kontak / Email": "...", "NPWP": "..."}}'
                )
                try:
                    res = self.client.chat(model=self.model_name, messages=[{"role": "user", "content": prompt}], options={"temperature": 0.0})
                    content = res["message"]["content"]
                    start_j, end_j = content.find("{"), content.rfind("}")
                    if start_j != -1 and end_j != -1:
                        clause_json = json.loads(content[start_j:end_j+1])
                        # Wrap in Vendor key
                        found_data["Vendor"] = {k: v for k, v in clause_json.items() if v and str(v).lower() not in ["null", "none"]}
                except Exception as e:
                    logger.debug(f"Targeted vendor extraction failed: {e}")
        
        # 2. Terms, garansi, delivery
        terms_keywords = ["garansi", "warranty", "sla", "syarat", "ketentuan", "berlaku", "delivery", "pengiriman"]
        if any(f in null_fields for f in ["Garansi / SLA", "Syarat dan Ketentuan", "Masa Berlaku Penawaran", "Jangka Waktu Pengiriman"]):
            relevant_lines = []
            for i, line in enumerate(lines):
                if any(kw in line.lower() for kw in terms_keywords):
                    start = max(0, i - 1)
                    end = min(len(lines), i + 3)
                    relevant_lines.extend(lines[start:end])
            
            if relevant_lines:
                snippet = "\n".join(list(dict.fromkeys(relevant_lines))[:25])
                prompt = (
                    f"Berikut cuplikan syarat dan ketentuan dari SPH:\n\n{snippet}\n\n"
                    f"Ekstrak: Garansi/SLA, Syarat dan Ketentuan (array), Masa Berlaku Penawaran, Jangka Waktu Pengiriman.\n"
                    f'JSON: {{"Garansi / SLA": "...", "Syarat dan Ketentuan": ["..."], "Masa Berlaku Penawaran": "...", "Jangka Waktu Pengiriman": "..."}}'
                )
                try:
                    res = self.client.chat(model=self.model_name, messages=[{"role": "user", "content": prompt}], options={"temperature": 0.0})
                    content = res["message"]["content"]
                    start_j, end_j = content.find("{"), content.rfind("}")
                    if start_j != -1 and end_j != -1:
                        clause_json = json.loads(content[start_j:end_j+1])
                        for k, v in clause_json.items():
                            if v and str(v).lower() not in ["null", "none", "-", "[]"]:
                                found_data[k] = v
                except Exception as e:
                    logger.debug(f"Targeted terms extraction failed: {e}")
        
        return found_data

    def extract_contract(self, markdown_text: str) -> ContractExtractionSchema:
        """
        Mengekstrak informasi SPK / Kontrak menjadi ContractExtractionSchema terstruktur.
        Dilengkapi Context Enrichment, Section Slicing, dan Targeted Refinement.
        """
        # Context enrichment — add section/entity annotations
        enriched_text = self.context_analyzer.enrich_markdown(markdown_text)
        
        # Untuk dokumen sangat panjang (>5000 karakter), lakukan sampling cerdas
        if len(enriched_text) > 5000:
            logger.info(f"📑 Dokumen tebal terdeteksi ({len(enriched_text)} karakter). Menerapkan Smart Section Sampling...")
            preamble = enriched_text[:3500]
            ending = enriched_text[-3000:]
            middle_lines = []
            for line in enriched_text[3500:-3000].splitlines():
                if any(kw in line.lower() for kw in ["pasal", "cara pembayaran", "rekening", "bank", "jangka waktu", "denda", "total", "sub total", "ppn", "[section", "[key"]):
                    middle_lines.append(line)
            middle_sample = "\n".join(middle_lines[:50])
            effective_text = f"{preamble}\n\n[...BAGIAN PASAL-PASAL KONTRAK...]\n{middle_sample}\n\n[...BAGIAN AKHIR & LAMPIRAN...]\n{ending}"
        else:
            effective_text = enriched_text

        user_prompt = (
            f"Berikut teks dokumen kontrak/SPK hasil parsing:\n\n"
            f"{effective_text}\n\n"
            f"Ekstrak seluruh informasi sesuai schema JSON."
        )

        # First extraction
        logger.info(f"🤖 Extraction pass 1 [{self.model_name}]...")
        extracted = self._extract_with_llm(
            CONTRACT_EXTRACTION_SYSTEM_PROMPT,
            user_prompt,
            ContractExtractionSchema
        )
        
        # Check null fields
        data_dict = extracted.model_dump(by_alias=True)
        total, nulls, null_names = self._count_null_fields(data_dict)
        null_ratio = nulls / max(total, 1)
        
        logger.info(f"📊 Extraction pass 1: {total - nulls}/{total} fields terisi ({(1 - null_ratio) * 100:.0f}%)")
        
        if null_names:
            logger.warning(f"⚠️  Field null ({nulls}): {', '.join(null_names[:10])}")
        
        # Targeted Clause Refinement jika ada null pada dokumen panjang
        if null_names and len(markdown_text) > 3000:
            logger.info("🎯 Menjalankan Targeted Clause Refinement untuk pasal-pasal spesifik...")
            targeted_data = self._extract_targeted_clauses(markdown_text, null_names)
            if targeted_data:
                logger.info(f"✨ Berhasil menemukan {len(targeted_data)} field dari targeted scan: {list(targeted_data.keys())}")
                merged_dict = self._merge_retry_result(data_dict, targeted_data)
                try:
                    extracted = ContractExtractionSchema.model_validate(merged_dict)
                    data_dict = extracted.model_dump(by_alias=True)
                    total, nulls, null_names = self._count_null_fields(data_dict)
                    null_ratio = nulls / max(total, 1)
                    logger.info(f"📊 After targeted scan: {total - nulls}/{total} fields terisi ({(1 - null_ratio) * 100:.0f}%)")
                except Exception as e:
                    logger.warning(f"Validation after targeted scan: {e}")

        # Standard Retry if still too many nulls
        if null_ratio > config.NULL_FIELD_THRESHOLD and config.MAX_EXTRACTION_RETRIES > 0:
            for retry_num in range(1, config.MAX_EXTRACTION_RETRIES + 1):
                logger.info(f"🔄 Retry extraction pass {retry_num + 1} (null ratio: {null_ratio:.0%})...")
                
                retry_prompt = CONTRACT_RETRY_PROMPT_TEMPLATE.format(
                    null_fields="\n".join(f"- {name}" for name in null_names),
                    markdown_text=effective_text
                )
                
                try:
                    retry_result = self._extract_with_llm(
                        CONTRACT_EXTRACTION_SYSTEM_PROMPT,
                        retry_prompt,
                        ContractExtractionSchema
                    )
                    
                    retry_dict = retry_result.model_dump(by_alias=True)
                    merged_dict = self._merge_retry_result(data_dict, retry_dict)
                    
                    extracted = ContractExtractionSchema.model_validate(merged_dict)
                    data_dict = extracted.model_dump(by_alias=True)
                    total, nulls, null_names = self._count_null_fields(data_dict)
                    null_ratio = nulls / max(total, 1)
                    
                    logger.info(f"📊 After retry {retry_num + 1}: {total - nulls}/{total} fields terisi ({(1 - null_ratio) * 100:.0f}%)")
                    
                    if null_ratio <= config.NULL_FIELD_THRESHOLD:
                        logger.info(f"✅ Retry berhasil menurunkan null ratio ke {null_ratio:.0%}")
                        break
                        
                except Exception as e:
                    logger.warning(f"⚠️  Retry {retry_num + 1} gagal: {e}")
                    break
        
        return extracted

    def extract_sph(self, markdown_text: str) -> SPHExtractionSchema:
        """
        Mengekstrak informasi SPH Vendor menjadi SPHExtractionSchema terstruktur.
        Dengan context enrichment, targeted refinement, dan retry logic.
        """
        # Context enrichment
        enriched_text = self.context_analyzer.enrich_markdown(markdown_text)
        
        user_prompt = (
            f"Berikut teks dokumen SPH hasil parsing:\n\n"
            f"{enriched_text}\n\n"
            f"Ekstrak seluruh informasi sesuai schema JSON."
        )

        # First extraction
        logger.info(f"🤖 Extraction pass 1 [{self.model_name}]...")
        extracted = self._extract_with_llm(
            SPH_EXTRACTION_SYSTEM_PROMPT,
            user_prompt,
            SPHExtractionSchema
        )
        
        # Check null fields
        data_dict = extracted.model_dump(by_alias=True)
        total, nulls, null_names = self._count_null_fields(data_dict)
        null_ratio = nulls / max(total, 1)
        
        logger.info(f"📊 Extraction pass 1: {total - nulls}/{total} fields terisi ({(1 - null_ratio) * 100:.0f}%)")
        
        if null_names:
            logger.warning(f"⚠️  Field null ({nulls}): {', '.join(null_names[:10])}")
        
        # Targeted Clause Refinement for SPH
        if null_names and len(markdown_text) > 1000:
            logger.info("🎯 Menjalankan Targeted SPH Clause Refinement...")
            targeted_data = self._extract_targeted_sph_clauses(markdown_text, null_names)
            if targeted_data:
                logger.info(f"✨ SPH targeted scan: {len(targeted_data)} field ditemukan")
                merged_dict = self._merge_retry_result(data_dict, targeted_data)
                try:
                    extracted = SPHExtractionSchema.model_validate(merged_dict)
                    data_dict = extracted.model_dump(by_alias=True)
                    total, nulls, null_names = self._count_null_fields(data_dict)
                    null_ratio = nulls / max(total, 1)
                    logger.info(f"📊 After SPH targeted scan: {total - nulls}/{total} fields terisi ({(1 - null_ratio) * 100:.0f}%)")
                except Exception as e:
                    logger.warning(f"Validation after SPH targeted scan: {e}")
        
        # Standard Retry if still too many nulls
        if null_ratio > config.NULL_FIELD_THRESHOLD and config.MAX_EXTRACTION_RETRIES > 0:
            for retry_num in range(1, config.MAX_EXTRACTION_RETRIES + 1):
                logger.info(f"🔄 Retry extraction pass {retry_num + 1}...")
                
                retry_prompt = SPH_RETRY_PROMPT_TEMPLATE.format(
                    null_fields="\n".join(f"- {name}" for name in null_names),
                    markdown_text=markdown_text
                )
                
                try:
                    retry_result = self._extract_with_llm(
                        SPH_EXTRACTION_SYSTEM_PROMPT,
                        retry_prompt,
                        SPHExtractionSchema
                    )
                    
                    retry_dict = retry_result.model_dump(by_alias=True)
                    merged_dict = self._merge_retry_result(data_dict, retry_dict)
                    
                    extracted = SPHExtractionSchema.model_validate(merged_dict)
                    data_dict = extracted.model_dump(by_alias=True)
                    total, nulls, null_names = self._count_null_fields(data_dict)
                    null_ratio = nulls / max(total, 1)
                    
                    logger.info(f"📊 After retry {retry_num + 1}: {total - nulls}/{total} fields terisi ({(1 - null_ratio) * 100:.0f}%)")
                    
                    if null_ratio <= config.NULL_FIELD_THRESHOLD:
                        break
                        
                except Exception as e:
                    logger.warning(f"⚠️  Retry {retry_num + 1} gagal: {e}")
                    break
        
        return extracted
