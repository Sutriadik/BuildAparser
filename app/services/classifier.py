import ollama
import json
import re
from typing import Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field
from app.config import config

class DocumentClassificationResult(BaseModel):
    document_type: str = Field(description="Jenis dokumen: 'contract', 'sph', 'bast', atau 'general'")
    confidence: float = Field(description="Skor keyakinan (0.0 - 1.0)")
    reasoning: str = Field(description="Alasan penentuan tipe dokumen")
    title: Optional[str] = Field(None, description="Judul dokumen yang terdeteksi")

class DocumentClassifier:
    def __init__(self, model_name: str = None, base_url: str = None):
        self.model_name = model_name or config.OLLAMA_MODEL
        self.base_url = base_url or config.OLLAMA_BASE_URL
        self.client = ollama.Client(host=self.base_url)

    def classify_fast_rule(self, text: str) -> Tuple[str, float]:
        """
        Deteksi cepat berbasis kata kunci / regex sebelum fallback ke AI.
        """
        text_upper = text[:3000].upper()
        
        # Kontrak / SPK
        if any(k in text_upper for k in ["SURAT PERINTAH KERJA", "SPK", "PERJANJIAN KERJASAMA", "KONTRAK PENGADAAN", "PIHAK PERTAMA", "PIHAK KEDUA"]):
            return "contract", 0.98
            
        # SPH / Penawaran
        if any(k in text_upper for k in ["SURAT PENAWARAN HARGA", "SPH", "PENAWARAN HARGA", "PRICE QUOTATION", "PENAWARAN BIAYA"]):
            return "sph", 0.98
            
        # BAST
        if any(k in text_upper for k in ["BERITA ACARA SERAH TERIMA", "BAST", "SERAH TERIMA PEKERJAAN", "BERITA ACARA PENYELESAIAN"]):
            return "bast", 0.98
            
        return "contract", 0.70

    def classify(self, markdown_text: str) -> DocumentClassificationResult:
        """
        Klasifikasi Tipe Dokumen Otomatis Menggunakan Ollama Qwen 2.5 + Rule Fallback.
        """
        # Coba rule cepat terlebih dahulu
        rule_type, rule_conf = self.classify_fast_rule(markdown_text)
        if rule_conf > 0.90:
            return DocumentClassificationResult(
                document_type=rule_type,
                confidence=rule_conf,
                reasoning="Terdeteksi secara akurat melalui kata kunci judul dan entitas dokumen.",
                title=markdown_text[:100].strip().replace("#", "")
            )

        # Jika ambigu, gunakan LLM Qwen 2.5
        system_prompt = (
            "Anda adalah AI Classifier dokumen pengadaan, hukum, dan administrasi.\n"
            "Tentukan jenis dokumen berikut:\n"
            "- 'contract': Jika Surat Perintah Kerja (SPK), Perjanjian, atau Kontrak.\n"
            "- 'sph': Jika Surat Penawaran Harga dari vendor/penyedia.\n"
            "- 'bast': Jika Berita Acara Serah Terima pekerjaan/barang.\n"
            "- 'general': Jika dokumen lain."
        )
        
        user_prompt = f"Berikut cuplikan halaman awal dokumen:\n\n{markdown_text[:2000]}\n\nTentukan tipe dokumen ke dalam JSON."

        try:
            response = self.client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                format=DocumentClassificationResult.model_json_schema(),
                options={"temperature": 0.0}
            )
            return DocumentClassificationResult.model_validate_json(response["message"]["content"])
        except Exception:
            return DocumentClassificationResult(
                document_type=rule_type,
                confidence=rule_conf,
                reasoning="Rule-based classification fallback"
            )
