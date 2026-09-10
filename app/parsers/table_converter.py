"""
Open ADE — Table Converter Module

Mengonversi tabel HTML (dari PPStructure SLANet) menjadi Markdown table yang bersih.
Juga mendukung konversi dari raw cell data ke markdown table.

Supported inputs:
  1. HTML <table> string → Markdown table
  2. List of rows (list of cell strings) → Markdown table
"""
import re
from typing import List, Optional
from html.parser import HTMLParser


class _TableHTMLParser(HTMLParser):
    """
    Parser HTML sederhana yang mengekstrak isi sel tabel 
    menjadi List[List[str]] (rows × cols).
    """
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self._current_row: List[str] = []
        self._current_cell: str = ""
        self._in_cell = False
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = ""
            # Handle colspan
            self._colspan = 1
            for attr_name, attr_value in attrs:
                if attr_name == "colspan" and attr_value:
                    try:
                        self._colspan = int(attr_value)
                    except ValueError:
                        self._colspan = 1
        elif tag == "br" and self._in_cell:
            self._current_cell += " "

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in ("td", "th"):
            self._in_cell = False
            cell_text = self._current_cell.strip()
            # Collapse whitespace
            cell_text = re.sub(r'\s+', ' ', cell_text)
            self._current_row.append(cell_text)
            # Fill colspan with empty cells
            for _ in range(self._colspan - 1):
                self._current_row.append("")
        elif tag == "tr":
            if self._current_row:
                self.rows.append(self._current_row)

    def handle_data(self, data: str):
        if self._in_cell:
            self._current_cell += data


def html_table_to_rows(html_str: str) -> List[List[str]]:
    """
    Parse HTML <table> string menjadi list of rows.
    
    Args:
        html_str: HTML string berisi tag <table>
        
    Returns:
        List of rows, setiap row adalah list of cell strings
    """
    parser = _TableHTMLParser()
    parser.feed(html_str)
    return parser.rows


def rows_to_markdown_table(rows: List[List[str]], has_header: bool = True) -> str:
    """
    Konversi list of rows menjadi Markdown table string.
    
    Args:
        rows: List[List[str]] — setiap inner list adalah satu row
        has_header: Apakah row pertama adalah header
        
    Returns:
        Markdown table string
    """
    if not rows:
        return ""
    
    # Normalisasi jumlah kolom (pad shorter rows)
    max_cols = max(len(row) for row in rows)
    normalized_rows = []
    for row in rows:
        padded = row + [""] * (max_cols - len(row))
        normalized_rows.append(padded)
    
    # Hitung lebar kolom optimal
    col_widths = []
    for col_idx in range(max_cols):
        max_width = 3  # minimum 3 chars
        for row in normalized_rows:
            cell_len = len(row[col_idx]) if col_idx < len(row) else 0
            max_width = max(max_width, cell_len)
        col_widths.append(min(max_width, 50))  # cap at 50 chars
    
    lines = []
    
    for row_idx, row in enumerate(normalized_rows):
        # Format cells dengan padding
        cells = []
        for col_idx, cell in enumerate(row):
            width = col_widths[col_idx]
            cells.append(f" {cell:<{width}} ")
        line = "|" + "|".join(cells) + "|"
        lines.append(line)
        
        # Tambah separator line setelah header row
        if row_idx == 0 and has_header:
            sep_cells = ["-" * (w + 2) for w in col_widths]
            sep_line = "|" + "|".join(sep_cells) + "|"
            lines.append(sep_line)
    
    return "\n".join(lines)


def html_table_to_markdown(html_str: str) -> str:
    """
    Konversi HTML <table> string langsung ke Markdown table.
    
    Args:
        html_str: HTML string dengan tag <table>
        
    Returns:
        Markdown table string, atau string kosong jika parsing gagal
    """
    if not html_str or "<table" not in html_str.lower():
        return ""
    
    rows = html_table_to_rows(html_str)
    
    if not rows:
        return ""
    
    # Skip tabel dengan 1 row atau 1 col (kemungkinan noise)
    if len(rows) < 2:
        return ""
    
    return rows_to_markdown_table(rows, has_header=True)


def clean_table_cells(rows: List[List[str]]) -> List[List[str]]:
    """
    Membersihkan isi sel tabel:
    - Trim whitespace
    - Hapus newline di dalam sel
    - Collapse multi-spaces
    """
    cleaned = []
    for row in rows:
        cleaned_row = []
        for cell in row:
            cell = cell.strip()
            cell = re.sub(r'\s+', ' ', cell)
            cleaned_row.append(cell)
        cleaned.append(cleaned_row)
    return cleaned
