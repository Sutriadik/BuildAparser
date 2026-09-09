"""
Open ADE — Ollama LLM Extraction Client
Dengan retry logic untuk mengisi field-field yang masih null.
"""
import ollama
from typing import Union, List, Tuple, Dict, Any, Optional
from pydantic import BaseModel

from app.config import config
from app.logger import logger
from app.schemas.contract import ContractExtractionSchema
from app.schemas.sph import SPHExtractionSchema
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

    def extract_contract(self, markdown_text: str) -> ContractExtractionSchema:
        """
        Mengekstrak informasi SPK / Kontrak menjadi ContractExtractionSchema terstruktur.
        Dengan retry logic untuk mengisi field-field null.
        """
        user_prompt = (
            f"Berikut teks dokumen kontrak/SPK hasil parsing:\n\n"
            f"{markdown_text}\n\n"
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
        
        # Retry if too many nulls
        if null_ratio > config.NULL_FIELD_THRESHOLD and config.MAX_EXTRACTION_RETRIES > 0:
            for retry_num in range(1, config.MAX_EXTRACTION_RETRIES + 1):
                logger.info(f"🔄 Retry extraction pass {retry_num + 1} (null ratio: {null_ratio:.0%})...")
                
                retry_prompt = CONTRACT_RETRY_PROMPT_TEMPLATE.format(
                    null_fields="\n".join(f"- {name}" for name in null_names),
                    markdown_text=markdown_text
                )
                
                try:
                    retry_result = self._extract_with_llm(
                        CONTRACT_EXTRACTION_SYSTEM_PROMPT,
                        retry_prompt,
                        ContractExtractionSchema
                    )
                    
                    retry_dict = retry_result.model_dump(by_alias=True)
                    merged_dict = self._merge_retry_result(data_dict, retry_dict)
                    
                    # Re-validate merged data
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
        Dengan retry logic untuk mengisi field-field null.
        """
        user_prompt = (
            f"Berikut teks dokumen SPH hasil parsing:\n\n"
            f"{markdown_text}\n\n"
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
        
        # Retry if too many nulls
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
