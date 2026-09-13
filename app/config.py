import os
from pathlib import Path
from pydantic import BaseModel


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


class AppConfig(BaseModel):
    # Base paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    STORAGE_DIR: Path = BASE_DIR / "storage"
    TEMP_UPLOADS: Path = STORAGE_DIR / "temp_uploads"
    OUTPUT_DIR: Path = STORAGE_DIR / "outputs"
    PARSING_OUTPUT_DIR: Path = OUTPUT_DIR / "parsing"
    EXTRACTION_OUTPUT_DIR: Path = OUTPUT_DIR / "extraction"

    # Ollama settings
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    OLLAMA_TEMPERATURE: float = 0.0
    OLLAMA_SEED: int = _env_int("OLLAMA_SEED", 42)
    # Wajib eksplisit: default server Ollama (umumnya 4096) memotong prompt kontrak (~8k token) diam-diam.
    OLLAMA_NUM_CTX: int = _env_int("OLLAMA_NUM_CTX", 12288)
    OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    OLLAMA_TIMEOUT: float = float(os.getenv("OLLAMA_TIMEOUT", "600"))
    # Batas token output: mencegah model "berputar" menghasilkan JSON tanpa akhir sampai timeout.
    OLLAMA_NUM_PREDICT: int = _env_int("OLLAMA_NUM_PREDICT", 4096)

    # Parser settings
    DEFAULT_DPI: int = 150
    OCR_LANG: str = os.getenv("OCR_LANG", "en")
    SCANNED_CHAR_THRESHOLD: int = 40          # rata-rata karakter/halaman di bawah ini = scan
    PAGE_NATIVE_MIN_CHARS: int = 80           # halaman dengan teks native >= ini tidak perlu OCR

    # OCR Spatial Clustering settings
    OCR_Y_TOLERANCE: float = 0.015
    OCR_X_GAP_TOLERANCE: float = 0.15

    # Extraction settings
    MAX_EXTRACTION_RETRIES: int = _env_int("MAX_EXTRACTION_RETRIES", 1)
    NULL_FIELD_THRESHOLD: float = 0.30  # Retry jika > 30% fields null
    EFFECTIVE_TEXT_MAX_CHARS: int = _env_int("EFFECTIVE_TEXT_MAX_CHARS", 12000)

    # Grounding Linker settings
    GROUNDING_MIN_SCORE: float = 0.65  # Minimum calibrated score untuk visual grounding bounding box

    # PP-Structure Layout Analysis settings
    ENABLE_LAYOUT_ANALYSIS: bool = True   # Gunakan PPStructure untuk scanned docs (fallback engine)
    LAYOUT_SCORE_THRESHOLD: float = 0.5  # Minimum confidence untuk layout region

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # NocoDB settings (Optional production integration)
    NOCODB_URL: str = os.getenv("NOCODB_URL", "http://localhost:8080")
    NOCODB_API_TOKEN: str = os.getenv("NOCODB_API_TOKEN", "")

    # Versioning (Plan §27) — naikkan saat parser/schema berubah
    PARSER_VERSION: str = "parse-2026.09.1"
    SCHEMA_VERSION: str = "schema-2026.09.1"


config = AppConfig()

# Ensure directories exist
config.TEMP_UPLOADS.mkdir(parents=True, exist_ok=True)
config.PARSING_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
config.EXTRACTION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
