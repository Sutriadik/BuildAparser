"""
Open ADE — Pembangkit DDL PostgreSQL dari app/companion/model.py.

Kenapa DDL di PostgreSQL, bukan membuat tabel lewat UI NocoDB: belum terkonfirmasi bahwa
relasi di UI NocoDB menjadi foreign key sungguhan, dan belum terkonfirmasi adanya constraint
UNIQUE di UI. Integritas data tidak boleh bergantung pada hal yang belum terbukti. Briefing
hlm. 13 sudah memakai PostgreSQL, jadi NocoDB cukup dihubungkan sebagai sumber data eksternal
dan tetap memberi UI untuk PM.
"""
from __future__ import annotations

from typing import List

from app.companion.model import ALL_TABLES, SCHEMA_VERSION, Table, insert_order

_PG_TYPE = {
    "text": "text",
    "int": "integer",
    "numeric": "numeric(18,2)",
    "date": "date",
    "timestamptz": "timestamptz",
    "char3": "char(3)",
}

_UPDATED_AT_FN = """-- Menjaga updated_at tetap benar tanpa bergantung pada aplikasi yang menulis.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;"""


def _column_sql(t: Table, c) -> str:
    parts = [f"    {c.name:26} {_PG_TYPE[c.type]}"]
    if c.name in ("created_at", "updated_at"):
        parts.append("NOT NULL DEFAULT now()")
    else:
        if not c.null:
            parts.append("NOT NULL")
        if c.name == "currency":
            parts.append("DEFAULT 'IDR'")
    if c.unique:
        parts.append("UNIQUE")
    if c.fk:
        ref_table, ref_col = c.fk.split(".")
        parts.append(f"REFERENCES {ref_table}({ref_col}) ON DELETE {c.fk_on_delete}")
    return " ".join(parts)


def _table_sql(t: Table) -> str:
    lines: List[str] = []
    if t.note:
        for ln in t.note.split(". "):
            if ln.strip():
                lines.append(f"-- {ln.strip().rstrip('.')}.")
    lines.append(f"-- Ditulis oleh: {'MANUSIA (PM)' if t.written_by == 'human' else 'mesin (pipeline)'}")
    lines.append(f"CREATE TABLE IF NOT EXISTS {t.name} (")

    body = ["    id                         bigserial PRIMARY KEY"]
    for c in t.columns:
        body.append(_column_sql(t, c))
    for cols in t.unique_together:
        body.append(f"    UNIQUE ({', '.join(cols)})")
    for c in t.columns:
        if c.enum:
            allowed = ", ".join(f"'{v}'" for v in c.enum)
            body.append(f"    CONSTRAINT {t.name}_{c.name}_valid CHECK ({c.name} IN ({allowed}))")
        if c.check:
            body.append(f"    CONSTRAINT {t.name}_{c.name}_check CHECK ({c.check})")
    lines.append(",\n".join(body))
    lines.append(");")

    # Index untuk kolom FK: NocoDB & n8n sering memfilter baris anak berdasarkan induknya.
    for c in t.columns:
        if c.fk and not c.unique:
            lines.append(f"CREATE INDEX IF NOT EXISTS idx_{t.name}_{c.name} ON {t.name}({c.name});")
    lines.append(
        f"DROP TRIGGER IF EXISTS trg_{t.name}_updated_at ON {t.name};\n"
        f"CREATE TRIGGER trg_{t.name}_updated_at BEFORE UPDATE ON {t.name}\n"
        f"    FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
    )
    return "\n".join(lines)


_STATUS_VIEW = """-- Status verifikasi TIDAK disimpan sebagai kolom: ia turunan dari field_review.
-- Menyimpannya berarti ada dua sumber kebenaran yang bisa berbeda.
CREATE OR REPLACE VIEW document_review_status AS
SELECT
    d.id                                            AS document_id,
    d.source_filename,
    d.doc_type,
    count(ef.id)                                    AS field_count,
    count(fr.id) FILTER (WHERE fr.decision = 'confirmed') AS confirmed_count,
    count(fr.id) FILTER (WHERE fr.decision = 'corrected') AS corrected_count,
    CASE
        WHEN count(ef.id) = 0 THEN 'no_fields'
        WHEN count(fr.id) = 0 THEN 'draft_ai'
        WHEN count(fr.id) < count(ef.id) THEN 'in_review'
        ELSE 'verified_by_pm'
    END                                             AS review_status
FROM document d
LEFT JOIN extracted_field ef ON ef.document_id = d.id
LEFT JOIN field_review   fr ON fr.document_id = d.id AND fr.field_path = ef.field_path
GROUP BY d.id, d.source_filename, d.doc_type;

-- Field yang konfirmasinya basi: nilai AI berubah setelah PM memutuskan.
CREATE OR REPLACE VIEW field_review_stale AS
SELECT fr.document_id, fr.field_path,
       fr.reviewed_ai_value_text AS value_saat_dikonfirmasi,
       ef.ai_value_text          AS value_sekarang,
       fr.reviewed_by, fr.reviewed_at
FROM field_review fr
JOIN extracted_field ef
  ON ef.document_id = fr.document_id AND ef.field_path = fr.field_path
WHERE ef.ai_value_text IS DISTINCT FROM fr.reviewed_ai_value_text;"""


def generate_ddl() -> str:
    order = insert_order()
    tables = sorted(ALL_TABLES, key=lambda t: order.index(t.name))
    out = [
        "-- Open ADE — Companion data model untuk NocoDB",
        f"-- Dibangkitkan dari app/companion/model.py (versi {SCHEMA_VERSION}).",
        "-- JANGAN diedit tangan: ubah model.py lalu bangkitkan ulang.",
        "",
        _UPDATED_AT_FN,
        "",
    ]
    for t in tables:
        out.append(_table_sql(t))
        out.append("")
    out.append(_STATUS_VIEW)
    return "\n".join(out) + "\n"
