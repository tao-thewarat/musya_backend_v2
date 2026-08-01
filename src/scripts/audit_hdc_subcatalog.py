"""ตรวจทุกลิงก์ในหมวดหนึ่งว่าดึง JSON ได้จริงไหม — และพังตรงขั้นไหน

ที่มา: ผู้ใช้พบว่า "บางลิงก์ไม่สมบูรณ์ ดึง JSON ไม่ได้" ซึ่งไม่ใช่เรื่องแปลก
เพราะ opendata กับ HDC เป็นคนละระบบที่ sync กัน ตารางที่ HDC มีอาจยังไม่ถูก
เปิดออกทาง opendata API หรือเปิดแล้วแต่ไม่มีข้อมูลของเขต 10

ตรวจ 4 ขั้น แยกให้เห็นว่าพังตรงไหน (สำคัญ เพราะแก้คนละวิธี):
  1. schema   — opendata รู้จักตารางนี้ไหม
  2. years    — ประกาศปีอะไรบ้าง
  3. data     — ดึงข้อมูลปีล่าสุดได้ไหม
  4. zone10   — ในข้อมูลนั้นมีเขต 10 ไหม  ← ผ่าน 1-3 แต่ตกข้อนี้ได้

    docker exec chatapp-python-ai python -m src.scripts.audit_hdc_subcatalog <catId|url>
"""
from __future__ import annotations

import sys
import time


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    from src.tools import hdc_opendata as hdc

    cat_id = hdc.parse_subcatalog_id(sys.argv[1])
    if not cat_id:
        print("รหัสหมวดไม่ถูกต้อง")
        return 2

    items = hdc.list_subcatalog(cat_id)
    print(f"หมวด {cat_id} — {len(items)} ตัวชี้วัด (ยุบซ้ำแล้ว)\n")

    ok, broken = [], []
    for i, it in enumerate(items, 1):
        tbl = it["table"]
        row = {"table": tbl, "title": it["title_th"], "url": it["report_url"]}
        print(f"[{i}/{len(items)}] {tbl}")

        # 1. schema
        try:
            schema = hdc.get_schema(tbl)
            row["cols"] = len(schema)
        except Exception as exc:
            row["fail"] = f"schema: {type(exc).__name__} {str(exc)[:60]}"
            broken.append(row); print(f"      ✗ {row['fail']}"); continue

        # 2. years
        try:
            years = hdc.get_years(tbl)
            row["declared"] = len(years)
        except Exception as exc:
            row["fail"] = f"report_year: {type(exc).__name__} {str(exc)[:60]}"
            broken.append(row); print(f"      ✗ {row['fail']}"); continue
        if not years:
            row["fail"] = "report_year: ไม่ประกาศปีเลย"
            broken.append(row); print(f"      ✗ {row['fail']}"); continue

        # 3+4. ลองดึงจริงจากปีล่าสุดถอยหลัง — ปีเดียวพังไม่ได้แปลว่าตารางใช้ไม่ได้
        got = zone = 0
        err = ""
        for y in reversed(years):
            try:
                d = hdc._req(f"{hdc.BASE}/report_data",
                             {"tableName": tbl, "year": y, "type": "json"})
                got = len(d)
                zone = len([r for r in d if str(r.get("areacode", ""))[:2] in hdc.ZONE10])
                row["year_tried"] = y
                break
            except Exception as exc:
                err = f"{type(exc).__name__} {str(exc)[:50]}"
            time.sleep(0.3)

        row["rows"], row["zone10"] = got, zone
        if not got:
            row["fail"] = f"report_data: ดึงไม่ได้สักปี ({err})"
            broken.append(row); print(f"      ✗ {row['fail']}")
        elif not zone:
            # ผ่านทุกขั้นแต่ไม่มีข้อมูลเขต 10 — ใช้กับระบบเราไม่ได้ แต่ไม่ใช่ลิงก์เสีย
            row["fail"] = f"ไม่มีข้อมูลเขต 10 (ทั้งประเทศ {got:,} แถว)"
            broken.append(row); print(f"      ✗ {row['fail']}")
        else:
            ok.append(row)
            print(f"      ✓ {len(schema)} คอลัมน์ · {len(years)} ปี · "
                  f"เขต 10 {zone:,}/{got:,} แถว (ปี {row['year_tried']})")
        time.sleep(0.3)

    print(f"\n{'='*72}\nสรุป — ใช้ได้ {len(ok)} · มีปัญหา {len(broken)} จาก {len(items)}\n{'='*72}")
    for r in broken:
        print(f"\n  {r['table']}")
        print(f"    {r['title'][:70]}")
        print(f"    ปัญหา: {r['fail']}")
        if r.get("url"):
            print(f"    {r['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
