"""
Open ADE — Document classifier (Plan §10.1).

Rule berbasis skor dijalankan lebih dulu; LLM hanya dipanggil bila rule ambigu.
Versi lama memakai urutan if pertama-cocok dengan substring: SPH yang menyebut "SPK"
(atau kata yang memuat "SPK") langsung terklasifikasi kontrak.
"""
import re
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.config import config

CLASSIFY_WINDOW_CHARS = 3000
TITLE_WINDOW_CHARS = 600

_RULES: Dict[str, List[Tuple[str, float]]] = {
    "contract": [
        (r"\bSURAT\s+PERINTAH\s+KERJA\b", 3.0), (r"\bSPK\b", 2.0), (r"\bPERJANJIAN\b", 2.0),
        (r"\bKONTRAK\b", 2.0), (r"\bPIHAK\s+PERTAMA\b", 1.5), (r"\bPIHAK\s+KEDUA\b", 1.5),
        (r"\bKONTRAK\s+LAYANAN\b", 2.0),
    ],
    "sph": [
        (r"\bSURAT\s+PENAWARAN\s+HARGA\b", 3.0), (r"\bSPH\b", 2.0), (r"\bPENAWARAN\s+HARGA\b", 2.5),
        (r"\bPRICE\s+QUOTATION\b", 3.0), (r"\bQUOTATION\b", 2.0), (r"\bPENAWARAN\s+BIAYA\b", 2.5),
    ],
    "bast": [
        (r"\bBERITA\s+ACARA\s+SERAH\s+TERIMA\b", 3.5), (r"\bBAST\b", 2.5), (r"\bSERAH\s+TERIMA\s+PEKERJAAN\b", 2.5),
        (r"\bBERITA\s+ACARA\s+PENYELESAIAN\b", 2.5),
    ],
}
MIN_SCORE = 2.0


class DocumentClassificationResult(BaseModel):
    document_type: str = Field(description="Jenis dokumen: 'contract', 'sph', 'bast', atau 'general'")
    confidence: float = Field(description="Skor keyakinan (0.0 - 1.0)")
    reasoning: str = Field(description="Alasan penentuan tipe dokumen")
    title: Optional[str] = Field(None, description="Judul dokumen yang terdeteksi")


class DocumentClassifier:
    def __init__(self, model_name: str = None, base_url: str = None):
        self.model_name = model_name or config.OLLAMA_MODEL
        self.base_url = base_url or config.OLLAMA_BASE_URL
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import ollama
            self._client = ollama.Client(host=self.base_url)
        return self._client

    @staticmethod
    def score_types(text: str) -> Dict[str, float]:
        window = text[:CLASSIFY_WINDOW_CHARS].upper()
        title = window[:TITLE_WINDOW_CHARS]
        scores: Dict[str, float] = {}
        for doc_type, patterns in _RULES.items():
            score = 0.0
            for pattern, weight in patterns:
                if re.search(pattern, title):
                    score += weight * 1.5  # kata kunci di judul/kop jauh lebih bermakna
                elif re.search(pattern, window):
                    score += weight
            scores[doc_type] = score
        return scores

    def classify_fast_rule(self, text: str) -> Tuple[str, float]:
        scores = self.score_types(text)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        (best_type, best), (_, second) = ranked[0], ranked[1]
        if best >= MIN_SCORE and best >= second * 1.5:
            return best_type, 0.98
        if best >= MIN_SCORE:
            return best_type, 0.80
        return "contract", 0.70

    def classify(self, markdown_text: str) -> DocumentClassificationResult:
        rule_type, rule_conf = self.classify_fast_rule(markdown_text)
        title = re.sub(r"[#*]", "", markdown_text[:100]).strip()
        if rule_conf > 0.90:
            return DocumentClassificationResult(document_type=rule_type, confidence=rule_conf,
                                                reasoning=f"Rule score: {self.score_types(markdown_text)}", title=title)

        system_prompt = (
            "Anda adalah AI Classifier dokumen pengadaan, hukum, dan administrasi.\n"
            "Tentukan jenis dokumen berikut:\n"
            "- 'contract': Surat Perintah Kerja (SPK), Perjanjian, atau Kontrak.\n"
            "- 'sph': Surat Penawaran Harga dari vendor/penyedia.\n"
            "- 'bast': Berita Acara Serah Terima pekerjaan/barang.\n"
            "- 'general': dokumen lain."
        )
        try:
            response = self.client.chat(
                model=self.model_name,
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": f"Cuplikan halaman awal dokumen:\n\n{markdown_text[:2000]}\n\nTentukan tipe dokumen dalam JSON."}],
                format=DocumentClassificationResult.model_json_schema(),
                options={"temperature": 0.0, "seed": config.OLLAMA_SEED, "num_ctx": 4096},
            )
            return DocumentClassificationResult.model_validate_json(response["message"]["content"])
        except Exception:
            return DocumentClassificationResult(document_type=rule_type, confidence=rule_conf,
                                                reasoning="Rule-based classification fallback", title=title)
