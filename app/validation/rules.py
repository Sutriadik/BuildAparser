"""
Open ADE — Rule & cross-field validation (Plan §15).

Pydantic hanya menjamin tipe/struktur. Modul ini memeriksa apakah nilainya masuk akal
secara bisnis: total = subtotal + PPN, terbilang = total, jumlah item = subtotal, dst.
Semua rule deterministik, tanpa LLM.
"""
import re
from typing import Any, Callable, Dict, List, Optional

from app.extractors.deterministic.dates import find_dates
from app.extractors.deterministic.numbers import amounts_equal, parse_id_number, terbilang_to_number
from app.schemas.evidence import Severity, ValidationIssue, ValidationReport

RULE_VERSION = "rules-2026.09.1"
# Toleransi absolut untuk selisih pembulatan PPN/harga satuan. Toleransi relatif (0.5%) sempat
# dicoba tapi meloloskan terbilang yang salah ~Rp824 ribu pada nilai kontrak Rp174 juta.
ROUNDING_TOLERANCE = 1000.0


def _num(value: Any) -> Optional[float]:
    parsed = parse_id_number(value)
    return parsed if parsed not in (None, 0.0) else None


def _close(a: Optional[float], b: Optional[float], tolerance: float = ROUNDING_TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    return amounts_equal(a, b, tolerance=tolerance)


def _percent(value: Any) -> Optional[float]:
    match = re.search(r'(\d+(?:[.,]\d+)?)\s*%', str(value or ""))
    return float(match.group(1).replace(',', '.')) / 100 if match else None


class _Checker:
    def __init__(self) -> None:
        self.issues: List[ValidationIssue] = []
        self.checked: List[str] = []

    def rule(self, name: str) -> None:
        self.checked.append(name)

    def add(self, rule: str, severity: Severity, fields: List[str], message: str,
            expected: Any = None, actual: Any = None) -> None:
        self.issues.append(ValidationIssue(rule=rule, severity=severity, fields=fields, message=message,
                                           expected=expected, actual=actual))

    def report(self) -> ValidationReport:
        if any(i.severity == Severity.ERROR for i in self.issues):
            status = "fail"
        elif self.issues:
            status = "warn"
        else:
            status = "pass"
        return ValidationReport(status=status, rule_version=RULE_VERSION, checked_rules=self.checked, issues=self.issues)


def _check_totals(c: _Checker, subtotal_key: str, ppn_key: str, total_key: str, rate_key: str, data: Dict[str, Any]) -> None:
    subtotal, ppn, total = _num(data.get(subtotal_key)), _num(data.get(ppn_key)), _num(data.get(total_key))

    c.rule("total_equals_subtotal_plus_ppn")
    if subtotal and total:
        expected_total = subtotal + (ppn or 0.0)
        if not _close(expected_total, total):
            c.add("total_equals_subtotal_plus_ppn", Severity.ERROR, [subtotal_key, ppn_key, total_key],
                  f"{subtotal_key} + {ppn_key} tidak sama dengan {total_key}", expected=expected_total, actual=total)
        if total < subtotal:
            c.add("total_not_below_subtotal", Severity.ERROR, [subtotal_key, total_key],
                  f"{total_key} lebih kecil dari {subtotal_key}", expected=f">= {subtotal}", actual=total)

    c.rule("ppn_matches_rate")
    rate = _percent(data.get(rate_key))
    if subtotal and ppn and rate:
        # PPN 12% dengan DPP nilai lain (11/12) menghasilkan tarif efektif 11%.
        candidates = [subtotal * rate, subtotal * rate * 11 / 12]
        if not any(_close(ppn, cand) for cand in candidates):
            c.add("ppn_matches_rate", Severity.WARNING, [ppn_key, rate_key],
                  f"{ppn_key} tidak sesuai tarif {data.get(rate_key)} dari {subtotal_key}",
                  expected=round(candidates[0], 2), actual=ppn)


def _check_items(c: _Checker, items_key: str, qty_key: str, price_key: str, line_total_key: str,
                 subtotal_key: str, total_key: str, data: Dict[str, Any]) -> None:
    items = data.get(items_key) or []
    c.rule("item_line_total")
    line_sum = 0.0
    all_lines_have_total = bool(items)
    for idx, item in enumerate(items):
        qty, price, line_total = _num(item.get(qty_key)), _num(item.get(price_key)), _num(item.get(line_total_key))
        if line_total is None:
            all_lines_have_total = False
            continue
        line_sum += line_total
        if qty and price:
            periode = _num(re.sub(r'[^\d.,]', '', str(item.get("Periode/Durasi") or ""))) if item.get("Periode/Durasi") else None
            candidates = [qty * price] + ([qty * price * periode] if periode else [])
            extra = item.get("Atribut Tambahan") or {}
            for extra_value in extra.values():
                multiplier = _num(extra_value)
                if multiplier and multiplier < 10_000:
                    candidates.append(qty * price * multiplier)
            if not any(_close(line_total, cand) for cand in candidates):
                base = f"{items_key}[{idx}]"
                c.add("item_line_total", Severity.WARNING,
                      [f"{base}.{qty_key}", f"{base}.{price_key}", f"{base}.{line_total_key}"],
                      f"Item {idx + 1}: volume x harga satuan tidak sama dengan jumlah harga",
                      expected=round(qty * price, 2), actual=line_total)

    c.rule("items_sum_matches_subtotal")
    subtotal, total = _num(data.get(subtotal_key)), _num(data.get(total_key))
    if all_lines_have_total and line_sum and (subtotal or total):
        if not (_close(line_sum, subtotal) or _close(line_sum, total)):
            c.add("items_sum_matches_subtotal", Severity.WARNING, [items_key, subtotal_key],
                  "Jumlah harga seluruh item tidak sama dengan subtotal maupun total",
                  expected=subtotal, actual=line_sum)


def _check_terbilang(c: _Checker, terbilang_key: str, amount_keys: List[str], data: Dict[str, Any]) -> None:
    c.rule("terbilang_matches_amount")
    words = data.get(terbilang_key)
    if not words:
        return
    spelled = terbilang_to_number(words)
    amounts = [(k, _num(data.get(k))) for k in amount_keys]
    amounts = [(k, v) for k, v in amounts if v]
    if spelled is None or not amounts:
        return
    if not any(_close(float(spelled), v, tolerance=1.0) for _, v in amounts):
        c.add("terbilang_matches_amount", Severity.ERROR, [terbilang_key],
              "Jumlah terbilang tidak sama dengan nominal", expected=amounts[0][1], actual=spelled)


def _check_required(c: _Checker, keys: List[str], data: Dict[str, Any]) -> None:
    c.rule("required_fields")
    for key in keys:
        node: Any = data
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is None or (isinstance(node, str) and not node.strip()):
            c.add("required_fields", Severity.WARNING, [key], f"Field wajib '{key}' kosong")


def _check_date_range(c: _Checker, key: str, data: Dict[str, Any]) -> None:
    c.rule("date_range_order")
    dates = find_dates(str(data.get(key) or ""))
    if len(dates) >= 2 and dates[1] < dates[0]:
        c.add("date_range_order", Severity.ERROR, [key], "Tanggal selesai lebih awal dari tanggal mulai",
              expected=f">= {dates[0]}", actual=str(dates[1]))


def validate_contract(data: Dict[str, Any]) -> ValidationReport:
    c = _Checker()
    _check_required(c, ["Nomor Kontrak Kerja", "Nama Pekerjaan", "Pihak Pertama.Nama Perusahaan", "Pihak Kedua.Nama Perusahaan"], data)
    _check_totals(c, "sub total", "Total PPN", "Total Harga Pekerjaan", "persentase ppn", data)
    _check_items(c, "List Item/Barang", "volume", "Harga Satuan", "Jumlah Harga", "sub total", "Total Harga Pekerjaan", data)
    _check_terbilang(c, "Jumlah Terbilang", ["Total Harga Pekerjaan", "sub total"], data)
    _check_date_range(c, "Jangka Waktu", data)

    c.rule("parties_distinct")
    p1, p2 = data.get("Pihak Pertama") or {}, data.get("Pihak Kedua") or {}
    for key in ("Nama Perusahaan", "Alamat"):
        v1, v2 = (p1.get(key) or "").strip().lower(), (p2.get(key) or "").strip().lower()
        if v1 and v1 == v2:
            c.add("parties_distinct", Severity.ERROR, [f"Pihak Pertama.{key}", f"Pihak Kedua.{key}"],
                  f"{key} Pihak Pertama dan Pihak Kedua identik (kemungkinan tertukar/tersalin)")
    return c.report()


def validate_sph(data: Dict[str, Any]) -> ValidationReport:
    c = _Checker()
    _check_required(c, ["Nomor SPH", "Vendor.Nama Vendor"], data)
    _check_totals(c, "Subtotal", "Nilai PPN", "Grand Total", "Persentase PPN", data)
    _check_items(c, "Daftar Penawaran Harga", "Volume / Qty", "Harga Satuan", "Total Harga", "Subtotal", "Grand Total", data)
    return c.report()


_VALIDATORS: Dict[str, Callable[[Dict[str, Any]], ValidationReport]] = {
    "contract": validate_contract,
    "sph": validate_sph,
}


def validate_extraction(doc_type: str, data: Dict[str, Any]) -> ValidationReport:
    validator = _VALIDATORS.get(doc_type, validate_contract)
    return validator(data)
