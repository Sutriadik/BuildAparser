import fitz  # PyMuPDF
from PIL import Image
import io
from typing import List, Tuple
from app.config import config

def is_scanned_pdf(pdf_path: str, threshold: int = None) -> bool:
    """
    Mendeteksi apakah PDF berupa dokumen scan (image-only) atau digital text layer.
    """
    if threshold is None:
        threshold = config.SCANNED_CHAR_THRESHOLD
        
    doc = fitz.open(pdf_path)
    total_chars = 0
    total_pages = len(doc)
    
    for page in doc:
        text = page.get_text().strip()
        total_chars += len(text)
        
    doc.close()
    
    avg_chars = total_chars / max(total_pages, 1)
    return avg_chars < threshold

def render_pdf_to_images(pdf_path: str, dpi: int = None) -> List[Tuple[Image.Image, int, int]]:
    """
    Merender seluruh halaman PDF menjadi gambar PIL Image beserta resolusi width & height aslinya.
    """
    if dpi is None:
        dpi = config.DEFAULT_DPI
        
    doc = fitz.open(pdf_path)
    rendered = []
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    
    for page in doc:
        rect = page.rect
        orig_width = rect.width
        orig_height = rect.height
        
        pix = page.get_pixmap(matrix=matrix)
        img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
        rendered.append((img, orig_width, orig_height))
        
    doc.close()
    return rendered
