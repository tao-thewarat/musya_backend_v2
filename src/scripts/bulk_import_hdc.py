"""นำเข้าตัวชี้วัดที่จับคู่ได้ "ตรงเป๊ะ" เป็นชุด — เฟส 4

ใช้คู่กับ `match_hdc_tables.py` ซึ่งแยกชั้นความมั่นใจไว้แล้ว **สคริปต์นี้แตะเฉพาะ
ชั้น exact เท่านั้น** ชั้น choose/none ต้องให้คนตัดสินเอง (ดูเหตุผลใน match_hdc_tables)

ไฟล์เดิมไม่ถูกลบหรือทับ — สร้างเป็นไฟล์ใหม่วางไว้โฟลเดอร์เดียวกัน เพื่อให้บทสนทนาเก่า
ที่อ้าง file_id เดิมยังใช้ได้อยู่

    docker exec chatapp-python-ai python -m src.scripts.bulk_import_hdc --dry-run
    docker exec chatapp-python-ai python -m src.scripts.bulk_import_hdc
"""
from __future__ import annotations

import argparse
import sys

# x-amz-meta-path ตัดที่ 150 ตัวอักษรเงียบ ๆ — เกินแล้ว folder tree พัง ไฟล์หาไม่เจอ
PATH_LIMIT = 150
SUFFIX = " (HDC)"


def _plan(vault_path: str, indicator: str) -> tuple[str, str]:
    """คืน (โฟลเดอร์, ชื่อไฟล์) ที่รับประกันว่าความยาวรวมไม่เกินลิมิต"""
    folder = vault_path.rsplit("/", 1)[0] if "/" in vault_path else vault_path
    title = (indicator or "ตัวชี้วัด").strip()
    # เผื่อที่ให้ "/" + ".csv" + SUFFIX
    room = PATH_LIMIT - len(folder) - len("/") - len(".csv") - len(SUFFIX)
    if room < 12:
        # โฟลเดอร์ยาวเกินจนตั้งชื่อไม่ได้ — ถอยขึ้นไปหนึ่งชั้น ดีกว่าปล่อยให้ path โดนตัด
        folder = folder.rsplit("/", 1)[0] if "/" in folder else folder
        room = PATH_LIMIT - len(folder) - len("/") - len(".csv") - len(SUFFIX)
    return folder, title[:max(room, 1)] + SUFFIX


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="ทำแค่ N ตัวแรก (ไว้ลองก่อน)")
    args = ap.parse_args()

    from src.db.pool import query_db
    from src.routers.hdc_import import _do_import
    from src.scripts.match_hdc_tables import match_one

    rows = query_db(
        "SELECT file_id, indicator_th, file_name, vault_path FROM csv_data_dict "
        "WHERE source='upload' ORDER BY file_id"
    )
    done = {r["table_name"] for r in query_db("SELECT table_name FROM hdc_import")}

    jobs = []
    print(f"จับคู่ {len(rows)} ตัวชี้วัด…")
    for r in rows:
        name = (r["indicator_th"] or r["file_name"] or "").strip()
        res = match_one(name)
        if res["level"] != "exact":
            continue
        if res["table"] in done:
            print(f"  ข้าม {res['table']} — นำเข้าไปแล้ว")
            continue
        folder, title = _plan(r["vault_path"] or "HDC", res["title"] or name)
        jobs.append((res["table"], folder, title))

    print(f"\nจะนำเข้า {len(jobs)} ตัวชี้วัด")
    if args.limit:
        jobs = jobs[: args.limit]
        print(f"(จำกัดไว้ {len(jobs)} ตัวตาม --limit)")
    for t, f, ti in jobs:
        print(f"  {t:26} → {f}/{ti}.csv  [{len(f) + len(ti) + 5} ตัว]")
    if args.dry_run:
        print("\ndry-run — ไม่ได้เขียนอะไร")
        return 0

    ok = fail = 0
    for i, (table, folder, title) in enumerate(jobs, 1):
        print(f"\n[{i}/{len(jobs)}] {table}")
        try:
            d = _do_import(table, folder, title, "", None, "bulk_import")
            print(f"    ✓ {d['rows']:,} แถว · {len(d['usableYears'])} ปี · "
                  f"{len(d['provinces'])} จังหวัด · id={d['file_id']}")
            ok += 1
        except Exception as exc:
            # ตัวเดียวพังต้องไม่ล้มทั้งชุด — ที่เหลืออีก 30 กว่าตัวยังต้องได้ไปต่อ
            print(f"    ✗ {type(exc).__name__}: {str(exc)[:160]}")
            fail += 1

    print(f"\nสำเร็จ {ok} · ล้มเหลว {fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
