"""ซ่อม `csv_data_dict.indicator_th` ที่เพี้ยนเป็นชื่อโฟลเดอร์กลุ่ม

ที่มาของบั๊ก: `build_data_dict()` เดาชื่อตัวชี้วัดจาก path (`parts[-2]`)
แต่ `build_vault_path()` จะ **ตัดชั้น "ชื่อตัวชี้วัด" ทิ้ง** เมื่อความยาว path ใกล้ลิมิต 150
พอเหลือ `<โดเมน>/<กลุ่ม>/<ไฟล์>.csv` ⇒ `parts[-2]` กลายเป็นชื่อกลุ่ม ไม่ใช่ชื่อตัวชี้วัด

ผลจริงที่วัดได้ (2026-08-01): ไฟล์สุขภาพจิต **28 ไฟล์** ได้ `indicator_th` เป็น
"ผู้ป่วยสุขภาพจิต" เหมือนกันหมด · สาขายาเสพติด 18 ไฟล์ได้ชื่อหมวดที่ถูกตัดครึ่ง

ทำไมต้องรีบซ่อม: `indicator_th` ถูกใช้ใน 3 ที่ที่สำคัญ
  - `search_file_ids()` — File Finder หาไฟล์ · ชื่อซ้ำกัน 28 ไฟล์แปลว่าเลือกถูกโดยบังเอิญ
  - `describe_for_prompt()` — บอก AI ว่า "ตัวชี้วัดคือ..." · ผิดชื่อ = ตอบผิดเรื่อง
  - `detect_frame_caveats()` — ตรวจ Work Load จากชื่อ · ชื่อผิดก็ตรวจไม่เจอ

ดึงชื่อจริงจากต้นทางผ่าน `report_url` (มี 243/274 แถว) ที่เหลือใช้ค้นจากชื่อตาราง

    docker exec chatapp-python-ai python -m src.scripts.repair_indicator_names --dry-run
    docker exec chatapp-python-ai python -m src.scripts.repair_indicator_names
"""
from __future__ import annotations

import argparse
import re
import sys
import time

_RID = re.compile(r"/standard-report-detail/([a-f0-9]{16,40})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from src.db.pool import execute_db, query_db
    from src.tools import hdc_opendata as hdc

    rows = query_db(
        """SELECT d.file_id, d.indicator_th, d.vault_path,
                  h.table_name, h.report_url
           FROM csv_data_dict d JOIN hdc_import h USING (file_id)
           ORDER BY d.file_id"""
    )
    print(f"ตรวจ {len(rows)} ไฟล์ที่มาจาก HDC"
          + (" (dry-run — ไม่เขียนอะไร)" if args.dry_run else ""))

    fixed = same = nosrc = 0
    for r in rows:
        true_name = ""
        m = _RID.search(r["report_url"] or "")
        if m:
            try:
                true_name = (hdc.lookup_by_report_id(m.group(1)) or {}).get("title_th", "")
            except Exception:
                true_name = ""
            time.sleep(0.25)

        if not true_name:
            # ไม่มีลิงก์ต้นทาง — ค้นจากชื่อตารางแทน แล้วรับเฉพาะที่ตรงตารางเป๊ะ
            try:
                for c in hdc._search_by_table(r["table_name"]) if hasattr(hdc, "_search_by_table") else []:
                    if c.get("source_table") == r["table_name"]:
                        true_name = (c.get("report_name") or "").strip()
                        break
            except Exception:
                pass

        if not true_name:
            nosrc += 1
            print(f"  ? {r['file_id']} หาชื่อจริงไม่ได้ ({r['table_name']}) — ปล่อยไว้เหมือนเดิม")
            continue
        if true_name == r["indicator_th"]:
            same += 1
            continue

        print(f"  ✎ {r['file_id']} [{r['table_name']}]")
        print(f"      เดิม: {r['indicator_th'][:70]}")
        print(f"      ใหม่: {true_name[:70]}")
        if not args.dry_run:
            execute_db("UPDATE csv_data_dict SET indicator_th=%s WHERE file_id=%s",
                       (true_name, r["file_id"]))
        fixed += 1

    print(f"\nแก้ {fixed} · ถูกอยู่แล้ว {same} · หาต้นทางไม่ได้ {nosrc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
