"""คำนวณ `role` ของคอลัมน์ใหม่ โดยใช้คำอธิบายจากต้นทาง

ที่มา — วัดจริง 2026-08-03: ถาม "ผู้ป่วยซึมเศร้าเข้าถึงบริการ เขต 10 ปี 2569"
AI เอาคอลัมน์ `pop` (ประชากรอายุ 15 ปีขึ้นไป 1,219,915 คน) มาเป็นตัวตั้ง
หารด้วย `target` (ผู้ป่วยคาดประมาณ 32,938) ⇒ ตอบ **3703.67%**
คำตอบที่ถูกคือ `result1`/`target` = 32,054/32,938 = **97.32%**

สาเหตุ: `build_data_dict()` เดา `role` จาก **ชื่อคอลัมน์** ตอนที่ยังไม่มีคำอธิบาย
ชื่อแบบ HDC (`pop` / `target` / `result1`) เดาไม่ออก ⇒ กลายเป็น `measure` ทั้งหมด
พอกฎ "ห้ามใช้ measure เป็นตัวตั้ง" ห้ามทุกคอลัมน์พร้อมกัน **ก็เท่ากับไม่ได้ห้ามอะไรเลย**

แต่ `desc` จากต้นทางบอกไว้ชัด — สคริปต์นี้อ่าน `desc` ที่เก็บไว้แล้วคำนวณ role ใหม่
ไม่ต้องยิง API ใหม่ ไม่ต้องดึงข้อมูลใหม่

    docker exec chatapp-python-ai python -m src.scripts.rebuild_column_roles --dry-run
    docker exec chatapp-python-ai python -m src.scripts.rebuild_column_roles
"""
from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from src.db.pool import execute_db, query_db
    from src.tools.data_dict import _role_of

    rows = query_db(
        "SELECT file_id, indicator_th, columns_json FROM csv_data_dict ORDER BY file_id")
    print(f"ตรวจ {len(rows)} ไฟล์" + ("  (dry-run — ไม่เขียนอะไร)" if args.dry_run else ""))

    changed = same = 0
    tally: dict[str, int] = {}
    for r in rows:
        cols = r["columns_json"] or []
        if isinstance(cols, str):
            try:
                cols = json.loads(cols)
            except Exception:
                continue
        if not cols:
            continue

        moved = []
        for c in cols:
            old = c.get("role")
            new = _role_of(c.get("name", ""), c.get("desc", ""))
            if new != old:
                c["role"] = new
                moved.append(f"{c['name']}: {old}→{new}")
                tally[f"{old}→{new}"] = tally.get(f"{old}→{new}", 0) + 1

        if not moved:
            same += 1
            continue
        changed += 1
        print(f"  {r['file_id']} {(r['indicator_th'] or '')[:44]}")
        for m in moved[:6]:
            print(f"      {m}")
        if not args.dry_run:
            execute_db("UPDATE csv_data_dict SET columns_json=%s::jsonb WHERE file_id=%s",
                       (json.dumps(cols, ensure_ascii=False), r["file_id"]))

    print(f"\nเปลี่ยน {changed} ไฟล์ · เหมือนเดิม {same}")
    if tally:
        print("สรุปการย้ายบทบาท:")
        for k, v in sorted(tally.items(), key=lambda x: -x[1])[:10]:
            print(f"  {k:28} {v:5} คอลัมน์")
    return 0


if __name__ == "__main__":
    sys.exit(main())
