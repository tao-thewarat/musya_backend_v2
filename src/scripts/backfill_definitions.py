"""ย้อนเติม "นิยามเชิงปฏิบัติการ" จาก HDC ให้ไฟล์ที่นำเข้าไปก่อนหน้า

    python -m src.scripts.backfill_definitions [--dry-run] [--only <table>]

ทำไมต้องมี: `_save_dict` เดิม **ไม่ได้เขียน** definition/numerator/denominator ลง DB
(ค่าถูกใส่ลง dict แล้วทิ้ง) ⇒ ไฟล์ที่นำเข้าไปแล้วทั้งหมดไม่มีนิยามเลยสักไฟล์

นิยามนี้คือสิ่งที่ตอบคำถามแรกที่คนทำนโยบายถามเสมอ — "ตัวเลขนี้นับใคร"
เช่นเกณฑ์ Risk ของการคัดกรองเบาหวาน (ปกติ <100 · เสี่ยง 100–125 · สงสัยป่วย >=126)
ซึ่ง `report_schema` ไม่มี มีแต่ในหน้ารายงาน HDC
"""
from __future__ import annotations

import re
import sys
import time

from src.db.pool import execute_db, query_db
from src.tools import hdc_opendata as hdc

_RID = re.compile(r"/standard-report-detail/([a-f0-9]{16,40})")


def _report_code(url: str) -> str:
    m = _RID.search(url or "")
    return m.group(1) if m else ""


def _table_to_report_id() -> dict[str, str]:
    """แผนที่ ชื่อตาราง → report_id จากสารบัญทุกหมวด

    จำเป็นเพราะไฟล์ที่นำเข้าด้วย "ชื่อตาราง" ล้วน ๆ ไม่มี `report_url` เก็บไว้
    ⇒ แกะรหัสรายงานไม่ได้ ⇒ ดึงนิยามไม่ได้ (35 จาก 83 ไฟล์เป็นแบบนี้)

    ต้นทางไม่มี endpoint ค้นด้วยชื่อตารางโดยตรง (ลอง by-source-table / by-table /
    search แล้วได้ 404 ทั้งหมด) จึงต้องไล่สารบัญทุกหมวดมาทำ index เอง
    ครั้งเดียวได้ ~900 ตาราง ใช้เวลาไม่กี่สิบวินาที
    """
    out: dict[str, str] = {}
    try:
        cats = hdc.list_categories()
    except Exception as exc:
        print(f"⚠️  ดึงสารบัญหมวดไม่ได้ ({type(exc).__name__}) — ข้ามการเดารหัสรายงาน")
        return out

    for c in cats:
        cid = c.get("id") or c.get("cat_id") or c.get("category_id") or ""
        if not cid:
            continue
        try:
            for it in hdc.list_subcatalog(cid):
                if it.get("report_id"):
                    out.setdefault(it["table"], it["report_id"])
        except Exception:
            continue
        time.sleep(0.2)
    return out


def main(dry_run: bool = False, only: str = "") -> None:
    sql = """
        SELECT h.file_id, h.table_name, h.title_th, h.report_url,
               d.definition, d.numerator_th, d.denominator_th, d.kpi_target
        FROM hdc_import h
        LEFT JOIN csv_data_dict d ON d.file_id = h.file_id
        ORDER BY h.table_name
    """
    rows = query_db(sql)
    if only:
        rows = [r for r in rows if r["table_name"] == only]

    todo = [r for r in rows if not (r.get("definition") or "").strip()]
    print(f"ไฟล์จาก HDC ทั้งหมด {len(rows)} · ยังไม่มีนิยาม {len(todo)}\n")

    need_map = any(not _report_code(r["report_url"] or "") for r in todo)
    tbl_map = _table_to_report_id() if need_map else {}
    if tbl_map:
        print(f"ทำ index ชื่อตาราง → รหัสรายงาน ได้ {len(tbl_map)} ตาราง\n")

    ok = skip = fail = 0
    for r in todo:
        rid = _report_code(r["report_url"] or "") or tbl_map.get(r["table_name"], "")
        if not rid:
            print(f"⏭️  [{r['file_id']}] {r['table_name']:24} ไม่พบรหัสรายงาน")
            skip += 1
            continue
        try:
            n = hdc.get_report_notice(rid)
        except Exception as exc:
            print(f"❌ [{r['file_id']}] {r['table_name']:24} {type(exc).__name__}")
            fail += 1
            continue

        if not any(n.get(k) for k in ("notice", "a_name", "b_name", "target")):
            print(f"⏭️  [{r['file_id']}] {r['table_name']:24} ต้นทางไม่มีนิยาม")
            skip += 1
            continue

        got = []
        if n.get("notice"):
            got.append(f"หมายเหตุ {len(n['notice'])} ตัวอักษร")
        if n.get("a_name"):
            got.append("ตัวตั้ง")
        if n.get("b_name"):
            got.append("ตัวหาร")
        if n.get("target"):
            got.append(f"เป้า {n['target']}")
        print(f"✅ [{r['file_id']}] {r['table_name']:24} {' · '.join(got)}")

        if not dry_run:
            execute_db(
                """UPDATE csv_data_dict SET
                     definition     = coalesce(nullif(%s,''), definition),
                     numerator_th   = coalesce(nullif(%s,''), numerator_th),
                     denominator_th = coalesce(nullif(%s,''), denominator_th),
                     kpi_target     = coalesce(nullif(%s,''), kpi_target)
                   WHERE file_id = %s""",
                (n.get("notice", ""), n.get("a_name", ""),
                 n.get("b_name", ""), n.get("target", ""), r["file_id"]),
            )
        ok += 1
        time.sleep(0.3)     # ต้นทางเป็นบริการสาธารณะ — ไม่รุมยิง

    print(f"\nเติมนิยามได้ {ok} · ข้าม {skip} · ล้มเหลว {fail}")
    if dry_run:
        print("(dry-run — ยังไม่ได้เขียนลงฐานข้อมูล)")


if __name__ == "__main__":
    args = sys.argv[1:]
    only = ""
    if "--only" in args:
        only = args[args.index("--only") + 1]
    main(dry_run="--dry-run" in args, only=only)
