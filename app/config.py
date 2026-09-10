import os
from pathlib import Path
from pydantic import BaseModel

class AppConfig(BaseModel):
    # Base paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    STORAGE_DIR: Path = BASE_DIR / "storage"
    TEMP_UPLOADS: Path = STORAGE_DIR / "temp_uploads"
    OUTPUT_DIR: Path = STORAGE_DIR / "outputs"
    PARSING_OUTPUT_DIR: Path = OUTPUT_DIR / "parsing"
    EXTRACTION_OUTPUT_DIR: Path = OUTPUT_DIR / "extraction"
    
    # Ollama settings
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    OLLAMA_TEMPERATURE: float = 0.1
    
    # Parser settings
    DEFAULT_DPI: int = 150
    OCR_LANG: str = "en"
    SCANNED_CHAR_THRESHOLD: int = 40
    
    # OCR Spatial Clustering settings
    OCR_Y_TOLERANCE: float = 0.015
    OCR_X_GAP_TOLERANCE: float = 0.15
    
    # Extraction settings
    MAX_EXTRACTION_RETRIES: int = 2
    NULL_FIELD_THRESHOLD: float = 0.30  # Retry jika > 30% fields null
    
    # Grounding Linker settings
    GROUNDING_MIN_SCORE: float = 0.25  # Minimum score untuk match bounding box
    
    # PP-Structure Layout Analysis settings
    ENABLE_LAYOUT_ANALYSIS: bool = True   # Gunakan PPStructure untuk scanned docs
    ENABLE_TABLE_RECOGNITION: bool = True # Aktifkan SLANet table recognition
    LAYOUT_SCORE_THRESHOLD: float = 0.5  # Minimum confidence untuk layout region
    TABLE_MAX_CELLS: int = 500            # Skip tabel dengan sel lebih dari ini
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # NocoDB settings (Optional production integration)
    NOCODB_URL: str = os.getenv("NOCODB_URL", "http://localhost:8080")
    NOCODB_API_TOKEN: str = os.getenv("NOCODB_API_TOKEN", "")

config = AppConfig()

# Ensure directories exist
config.TEMP_UPLOADS.mkdir(parents=True, exist_ok=True)
config.PARSING_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
config.EXTRACTION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
