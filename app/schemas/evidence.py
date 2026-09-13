"""
Open ADE — Evidence & validation schemas (Plan §14, §15, §25).
"""
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from app.schemas.common import BoundingBox


class FieldStatus(str, Enum):
    AUTO_VERIFIED = "AUTO_VERIFIED"      # nilai ditemukan persis di dokumen + lolos semua rule
    AUTO_ACCEPTED = "AUTO_ACCEPTED"      # evidence cukup kuat, tapi tidak persis
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # evidence lemah / tidak ada / rule warning
    CONFLICT = "CONFLICT"                # melanggar rule konsistensi (error)
    MISSING = "MISSING"                  # tidak terisi


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    rule: str
    severity: Severity
    fields: List[str]
    message: str
    expected: Optional[Any] = None
    actual: Optional[Any] = None


class ValidationReport(BaseModel):
    status: str = Field(description="pass | warn | fail")
    rule_version: str
    checked_rules: List[str] = Field(default_factory=list)
    issues: List[ValidationIssue] = Field(default_factory=list)

    def issues_for(self, field: str) -> List[ValidationIssue]:
        return [i for i in self.issues if field in i.fields]


class FieldEvidence(BaseModel):
    field: str
    value: Any
    source_document: str
    page: Optional[int] = None
    bbox: Optional[BoundingBox] = None
    evidence_text: Optional[str] = None
    block_ids: List[str] = Field(default_factory=list)
    match_type: Optional[str] = None
    evidence_score: float = 0.0
    confidence: float = 0.0
    status: FieldStatus
    issues: List[str] = Field(default_factory=list)
