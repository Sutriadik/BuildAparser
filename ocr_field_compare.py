#!/usr/bin/env python3
"""
Bandingkan field hasil ekstraksi antar engine untuk SATU dokumen yang TIDAK punya
golden/ground-truth (mis. KL FULL SIGNED). Karena tidak ada ground truth independen,
Apple Vision dipakai sebagai REFERENSI -- bukan karena pasti benar, tapi karena
terbukti lenient=1.0/critical=1.0 pada dokumen golden yang punya ground truth
(SPK_ATS_ORACLE_FULL_SIGNED, lihat eval/reports/).

Field dianggap "cocok" kalau token_f1 terhadap nilai referensi >= 0.6 (nilai numerik
dibandingkan sebagai angka, toleran beda format ribuan).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval.run_eval import flatten, token_f1  # noqa: E402

NUM_RE = re.compile(r"[-\d.,]+")


def values_match(ref, got) -> bool:
    if ref is None and got is None:
        return True
    if ref is None or got is None:
        return False
    if isinstance(ref, (int, float)) or isinstance(got, (int, float)):
        try:
            return abs(float(ref) - float(got)) < 0.01
        except (TypeError, ValueError):
            pass
    return token_f1(str(ref), str(got)) >= 0.6


# Dump tabel mentah tanpa urutan/nama kolom yang stabil antar engine -- membandingkannya
# per-sel akan menghukum perbedaan urutan kolom yang sebenarnya tidak masalah. Field
# skema sebenarnya (termasuk "List Item/Barang" yang terstruktur) tetap dibandingkan.
_EXCLUDE_PREFIX = "Daftar Tabel Terstruktur"


def compare(ref_path: Path, other_path: Path) -> dict:
    ref_data = json.loads(ref_path.read_text())["data"]
    got_data = json.loads(other_path.read_text())["data"]
    ref_data.pop(_EXCLUDE_PREFIX, None)
    got_data.pop(_EXCLUDE_PREFIX, None)
    ref = flatten(ref_data)
    got = flatten(got_data)
    keys = set(ref) | set(got)
    total = len(keys)
    filled = sum(1 for k in keys if got.get(k) not in (None, "", [], {}))
    matched = sum(1 for k in keys if values_match(ref.get(k), got.get(k)))
    mismatches = [
        (k, ref.get(k), got.get(k)) for k in sorted(keys)
        if not values_match(ref.get(k), got.get(k)) and ref.get(k) not in (None, "", [], {})
    ]
    return {"total": total, "filled": filled, "matched_vs_ref": matched, "mismatches": mismatches}


if __name__ == "__main__":
    ref_path, other_path = Path(sys.argv[1]), Path(sys.argv[2])
    r = compare(ref_path, other_path)
    print(f"total={r['total']} filled={r['filled']} matched_vs_ref={r['matched_vs_ref']}")
    print(f"completeness={100*r['filled']/r['total']:.1f}%  match_rate={100*r['matched_vs_ref']/r['total']:.1f}%")
    if r["mismatches"]:
        print(f"\n{len(r['mismatches'])} field beda dari referensi (field, ref, got):")
        for k, a, b in r["mismatches"][:20]:
            print(f"  {k:45} ref={str(a)[:40]!r:42} got={str(b)[:40]!r}")
