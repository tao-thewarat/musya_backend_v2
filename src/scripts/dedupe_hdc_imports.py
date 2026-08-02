"""ทำเครื่องหมายไฟล์ HDC ที่ซ้ำตารางเดียวกัน ให้เหลือตัวที่ดีที่สุดตารางละ 1 ไฟล์

ที่มา — รันจริง 2026-08-03: ถาม "ผู้ป่วยความดันควบคุมได้ดี ที่อำเภอคำชะอี"
มี 3 ไฟล์ที่ชื่อตัวชี้วัดแทบเหมือนกัน ให้คำตอบ **20.54% / 6.40% / 54.75%**
ต่างกัน 8 เท่า และไม่มีอะไรบอกว่าไฟล์ไหนถูก

สาเหตุ: ตาราง HDC เดียวถูกนำเข้าซ้ำหลายครั้งระหว่างพัฒนา (30 ตาราง → 71 ไฟล์)
แต่ละครั้งได้ `file_id` ใหม่ ⇒ File Finder เห็นหลายเวอร์ชันแล้วเลือกแบบสุ่ม

**เกณฑ์เลือกตัวที่เก็บไว้** (เรียงตามลำดับ):
  1. จำนวนปีที่ใช้ได้มากที่สุด — ครอบคลุมกว่าคือดีกว่า
  2. จำนวนแถวมากที่สุด — ละเอียดกว่า
  3. ซิงก์ล่าสุด — ข้อมูลสดกว่า

**ไม่ลบไฟล์ทิ้ง** เพราะบทสนทนาเก่าอ้าง `file_id` เดิมไว้ · แค่ตั้ง `superseded_by`
ให้ File Finder ข้าม ส่วนคนที่เปิดลิงก์เก่ายังเห็นไฟล์อยู่

    docker exec chatapp-python-ai python -m src.scripts.dedupe_hdc_imports --dry-run
    docker exec chatapp-python-ai python -m src.scripts.dedupe_hdc_imports
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from src.db.pool import execute_db, query_db

    # ⚠️ JOIN แบบ INNER ไม่ใช่ LEFT — `hdc_import` มีแถวค้างของไฟล์ที่ถูกลบจาก MinIO
    # ไปแล้ว (วัดจริง 2026-08-03: ค้าง 14 แถว) ถ้าเอามานับด้วยจะรายงานเกินจริง
    # เช่นบอกว่า "ซ่อน 41 ไฟล์" ทั้งที่ UPDATE โดนจริงแค่ 29
    rows = query_db(
        """SELECT h.file_id, h.table_name, h.row_count, h.last_sync_at,
                  COALESCE(array_length(h.usable_years, 1), 0) AS yrs,
                  d.vault_path, d.superseded_by
           FROM hdc_import h
           JOIN csv_data_dict d USING (file_id)
           ORDER BY h.table_name"""
    )
    stale = query_db(
        """SELECT count(*) AS n FROM hdc_import h
           LEFT JOIN csv_data_dict d USING (file_id) WHERE d.file_id IS NULL"""
    )[0]["n"]
    if stale:
        # ไม่ลบให้เอง — เป็นทะเบียนการนำเข้า อาจมีคุณค่าเชิงประวัติ
        print(f"หมายเหตุ: `hdc_import` มีแถวค้าง {stale} แถวที่ไฟล์ถูกลบไปแล้ว (ข้ามไป)")

    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["table_name"], []).append(r)

    dup = {t: g for t, g in groups.items() if len(g) > 1}
    print(f"ตาราง HDC ทั้งหมด {len(groups)} · ซ้ำ {len(dup)} ตาราง "
          f"({sum(len(g) for g in dup.values())} ไฟล์)"
          + ("  (dry-run — ไม่เขียนอะไร)" if args.dry_run else ""))

    marked = cleared = 0
    for table, g in sorted(dup.items()):
        # มากปี → มากแถว → ซิงก์ล่าสุด
        g_sorted = sorted(
            g,
            key=lambda r: (r["yrs"], r["row_count"] or 0, r["last_sync_at"]),
            reverse=True,
        )
        keep, drop = g_sorted[0], g_sorted[1:]
        print(f"\n  {table}")
        print(f"    ✔ เก็บ  {keep['file_id']}  {keep['yrs']} ปี · "
              f"{(keep['row_count'] or 0):,} แถว")
        for r in drop:
            print(f"    ✕ ซ่อน {r['file_id']}  {r['yrs']} ปี · "
                  f"{(r['row_count'] or 0):,} แถว")
            if not args.dry_run:
                execute_db(
                    "UPDATE csv_data_dict SET superseded_by=%s WHERE file_id=%s",
                    (keep["file_id"], r["file_id"]),
                )
            marked += 1
        # ตัวที่เลือกเก็บอาจเคยถูกทำเครื่องหมายไว้ในรอบก่อน — ต้องปลดธง
        if keep.get("superseded_by") and not args.dry_run:
            execute_db(
                "UPDATE csv_data_dict SET superseded_by=NULL WHERE file_id=%s",
                (keep["file_id"],),
            )
            cleared += 1

    print(f"\nทำเครื่องหมายซ่อน {marked} ไฟล์" + (f" · ปลดธง {cleared}" if cleared else ""))
    if not args.dry_run:
        left = query_db(
            "SELECT count(*) AS n FROM csv_data_dict WHERE superseded_by IS NULL"
        )[0]["n"]
        print(f"ไฟล์ที่ File Finder จะเห็น: {left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
