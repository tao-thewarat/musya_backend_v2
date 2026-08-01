"""เติม `csv_data_dict.caveats` ให้ไฟล์ที่อยู่ในคลังอยู่แล้ว

ทำไมต้องมี: `describe_for_prompt()` แนบ caveats เข้าพรอมต์ให้ทุกครั้งอยู่แล้ว
แต่ตอนสำรวจ 2026-07-31 พบว่า **0 จาก 49 ไฟล์มีข้อมูลเลย** — ท่อครบแต่ว่างเปล่า
ไฟล์ที่ index ไปก่อนหน้าจึงต้องย้อนมาเติม ไม่งั้นต้องรออัปโหลดใหม่ทั้งคลัง

อ่านไฟล์จาก MinIO มาตรวจใหม่ **แตะเฉพาะคอลัมน์ caveats** ไม่ยุ่งกับคอลัมน์อื่น
เพราะ dictionary ที่มีอยู่อาจถูกคนแก้มือไว้แล้ว

    docker exec chatapp-python-ai python -m src.scripts.backfill_caveats --dry-run
    docker exec chatapp-python-ai python -m src.scripts.backfill_caveats
"""
from __future__ import annotations

import argparse
import io
import logging
import sys

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="แสดงผลอย่างเดียว ไม่เขียน DB")
    args = ap.parse_args()

    import pandas as pd

    from src.db.pool import execute_db, query_db
    from src.tools.data_dict import detect_frame_caveats
    from src.tools.minio import read_file_bytes_impl

    rows = query_db(
        """SELECT file_id, file_name, indicator_th, key_district, key_province,
                  caveats, source
           FROM csv_data_dict ORDER BY file_id"""
    )
    print(f"ตรวจ {len(rows)} ไฟล์" + (" (dry-run — ไม่เขียนอะไร)" if args.dry_run else ""))

    changed = skipped = failed = 0
    for r in rows:
        fid = r["file_id"]
        try:
            raw = read_file_bytes_impl(fid)
            df = pd.read_csv(io.BytesIO(raw), dtype=str)
        except Exception as exc:
            # ไฟล์หายจาก MinIO แต่ยังมีแถวใน dict ได้ — ข้ามไป อย่าให้ล้มทั้งงาน
            print(f"  ✗ {fid} อ่านไม่ได้: {type(exc).__name__}")
            failed += 1
            continue

        found = detect_frame_caveats(
            df, r["key_district"] or "", r["key_province"] or "", r["indicator_th"] or ""
        )
        old = list(r["caveats"] or [])
        # เก็บของเดิมไว้ก่อนเสมอ — อาจมีคนเขียนมือไว้ซึ่งตรวจอัตโนมัติหาไม่เจอ
        merged = list(dict.fromkeys(old + found))
        if merged == old:
            skipped += 1
            continue

        print(f"  + {fid} {(r['file_name'] or '')[:46]}")
        for c in found:
            if c not in old:
                print(f"      {c[:110]}")
        if not args.dry_run:
            execute_db("UPDATE csv_data_dict SET caveats=%s WHERE file_id=%s", (merged, fid))
        changed += 1

    print(f"\nเพิ่มคำเตือน {changed} ไฟล์ · ไม่มีอะไรเปลี่ยน {skipped} · อ่านไม่ได้ {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
