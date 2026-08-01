"""สร้างพจนานุกรมข้อมูลให้ CSV ทุกไฟล์ในคลัง แล้วเขียนลงตาราง csv_data_dict

    python -m src.scripts.build_data_dict [--dry-run]

ปลอดภัยต่อการรันซ้ำ (upsert) — รันใหม่ได้ทุกเมื่อหลังอัปไฟล์เพิ่ม
"""
from __future__ import annotations

import sys
import urllib.parse

from src.db.pool import execute_db
from src.tools.data_dict import build_data_dict
from src.tools.minio import _bucket, _get_client, _load_path_index

UPSERT = """
INSERT INTO csv_data_dict (
    file_id, vault_path, file_name, domain, indicator_th,
    year_min, year_max, years, provinces, granularity, row_count, col_count,
    key_province, key_district, key_year, keywords,
    columns_json, unknown_cols, counting_basis, confidence, source, built_at
) VALUES (
    %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,
    %s::jsonb,%s,%s,%s,%s, NOW()
)
ON CONFLICT (file_id) DO UPDATE SET
    vault_path=EXCLUDED.vault_path, file_name=EXCLUDED.file_name,
    domain=EXCLUDED.domain, indicator_th=EXCLUDED.indicator_th,
    year_min=EXCLUDED.year_min, year_max=EXCLUDED.year_max, years=EXCLUDED.years,
    provinces=EXCLUDED.provinces, granularity=EXCLUDED.granularity,
    row_count=EXCLUDED.row_count, col_count=EXCLUDED.col_count,
    key_province=EXCLUDED.key_province, key_district=EXCLUDED.key_district,
    key_year=EXCLUDED.key_year, keywords=EXCLUDED.keywords,
    columns_json=EXCLUDED.columns_json, unknown_cols=EXCLUDED.unknown_cols,
    counting_basis=EXCLUDED.counting_basis, built_at=NOW()
    -- ⚠️ ไม่แตะ confidence / verified_by / caveats — เป็นของที่คนกรอกไว้
    --    รันซ้ำแล้วต้องไม่ล้างงานที่คนตรวจไปแล้วทิ้ง
"""


def main(dry_run: bool = False) -> None:
    import json

    idx = _load_path_index(force=True)
    client, bucket = _get_client(), _bucket()
    print(f"พบไฟล์ที่มี path metadata {len(idx)} รายการ\n")

    ok = skipped = failed = 0
    for fid, path in sorted(idx.items(), key=lambda kv: kv[1]):
        try:
            stat = client.stat_object(bucket, fid)
            meta = {k.lower(): v for k, v in (stat.metadata or {}).items()}
            name = urllib.parse.unquote(meta.get("x-amz-meta-name", "") or "")
            if not name.lower().endswith(".csv"):
                skipped += 1
                continue

            raw = client.get_object(bucket, fid).read()
            d = build_data_dict(fid, path, name, raw)

            flag = "⚠️" if d["unknown_cols"] else "✅"
            span = f"{d['year_min']}–{d['year_max']}" if d["years"] else "ไม่มีปี"
            print(f"{flag} [{fid}] {d['indicator_th'][:52]}")
            print(f"      {d['row_count']:>5} แถว × {d['col_count']:>2} คอล · ปี {span} · "
                  f"{len(d['provinces'])} จังหวัด · {d['granularity']}")
            if d["unknown_cols"]:
                print(f"      คอลัมน์ที่ระบุไม่ได้: {d['unknown_cols']}")

            if not dry_run:
                execute_db(UPSERT, (
                    d["file_id"], d["vault_path"], d["file_name"], d["domain"] or None,
                    d["indicator_th"], d["year_min"], d["year_max"], d["years"],
                    d["provinces"], d["granularity"], d["row_count"], d["col_count"],
                    d["key_province"] or None, d["key_district"] or None, d["key_year"] or None,
                    d["keywords"], json.dumps(d["columns"], ensure_ascii=False),
                    d["unknown_cols"], d["counting_basis"] or None, d["confidence"], "upload",
                ))
            ok += 1
        except Exception as exc:
            failed += 1
            print(f"❌ [{fid}] {path[:56]}\n      {type(exc).__name__}: {exc}")

    print(f"\nสำเร็จ {ok} · ข้าม (ไม่ใช่ CSV) {skipped} · ล้มเหลว {failed}")
    if dry_run:
        print("(dry-run — ยังไม่ได้เขียนลงฐานข้อมูล)")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
