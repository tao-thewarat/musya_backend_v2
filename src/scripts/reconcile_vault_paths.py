"""ปรับ `csv_data_dict` ให้ตรงกับไฟล์จริงใน MinIO — **MinIO คือความจริง**

ทำไม MinIO ถึงเป็นความจริง ไม่ใช่ DB:
หน้า `/fileapa` สร้าง folder tree จาก `x-amz-meta-path` ของ object ใน MinIO ล้วน ๆ
ส่วน `csv_data_dict.vault_path` เป็นสำเนาที่บันทึกไว้ตอนนำเข้า **ไม่ได้อัปเดตตาม
เมื่อไฟล์ถูกย้าย** ⇒ พอย้ายไฟล์ (เช่นด้วย `move_vault_folder.py`) สองฝั่งก็แยกกันทันที

ผลที่วัดได้ 2026-08-01 — DB บอกว่ามีโฟลเดอร์ `HDC/` 9 ไฟล์ และ `D5_Population` 0 ไฟล์
แต่ของจริงใน MinIO ไม่มี `HDC/` เลย และ `D5_Population` มี 5 ไฟล์
**รายงานสถานะที่อ่านจาก DB จึงผิดทั้งหมด**

ทำไมต้องรีบแก้ ไม่ใช่แค่เรื่องรายงาน:
`search_file_ids(terms, domain)` กรองไฟล์ตามโดเมนจาก `csv_data_dict` ⇒ ถ้า path ใน DB
ผิด File Finder จะกรองไฟล์ที่ควรเจอทิ้ง หรือหยิบไฟล์ที่ย้ายไปแล้วมาตอบ

    docker exec chatapp-python-ai python -m src.scripts.reconcile_vault_paths --dry-run
    docker exec chatapp-python-ai python -m src.scripts.reconcile_vault_paths
    docker exec chatapp-python-ai python -m src.scripts.reconcile_vault_paths --prune
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse

# โฟลเดอร์โดเมน → รหัสโดเมนที่ pipeline ใช้กรอง
_DOMAIN_CODE = {"D1_": "d1", "D2_": "d2", "D3_": "d3", "D4_": "d4", "D5_": "d5", "D6_": "d6"}


def _domain_of(path: str) -> str | None:
    head = path.split("/", 1)[0]
    for prefix, code in _DOMAIN_CODE.items():
        if head.startswith(prefix):
            return code
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true",
                    help="ลบแถวพจนานุกรมที่ไม่มีไฟล์จริงแล้ว (ต้องยืนยันด้วยตัวเลือกนี้)")
    ap.add_argument("--build-missing", action="store_true",
                    help="สร้างพจนานุกรมให้ไฟล์ที่มีอยู่จริงแต่ยังไม่มีแถวใน csv_data_dict")
    args = ap.parse_args()

    from src.db.pool import execute_db, query_db
    from src.tools.minio import _bucket, _get_client

    client, bucket = _get_client(), _bucket()
    live: dict[str, str] = {}
    for obj in client.list_objects(bucket, recursive=True):
        try:
            st = client.stat_object(bucket, obj.object_name)
        except Exception:
            continue
        md = {k.lower().replace("x-amz-meta-", ""): v for k, v in (st.metadata or {}).items()}
        path = urllib.parse.unquote(md.get("path", "") or "")
        if path:
            live[obj.object_name] = path

    rows = query_db("SELECT file_id, vault_path, file_name, domain FROM csv_data_dict")
    db = {r["file_id"]: r for r in rows}

    moved = [(fid, db[fid]["vault_path"], live[fid]) for fid in db
             if fid in live and db[fid]["vault_path"] != live[fid]]
    orphan = [fid for fid in db if fid not in live]
    missing = [fid for fid in live if fid not in db]

    print(f"MinIO {len(live)} ไฟล์ · พจนานุกรม {len(db)} แถว"
          + ("  (dry-run — ไม่เขียนอะไร)" if args.dry_run else ""))
    print(f"  path ไม่ตรง       : {len(moved)}")
    print(f"  แถวกำพร้า (ไม่มีไฟล์) : {len(orphan)}")
    print(f"  ไฟล์ไม่มีพจนานุกรม   : {len(missing)}")

    for fid, old, new in moved:
        name = new.rsplit("/", 1)[-1]
        if old.split("/", 1)[0] != new.split("/", 1)[0]:
            print(f"  ↪ {fid} ย้ายโดเมน {old.split('/', 1)[0]} → {new.split('/', 1)[0]}")
        if not args.dry_run:
            execute_db(
                "UPDATE csv_data_dict SET vault_path=%s, file_name=%s, domain=%s "
                "WHERE file_id=%s",
                (new, name, _domain_of(new), fid),
            )

    if orphan:
        print("\nแถวกำพร้า — พจนานุกรมชี้ไปไฟล์ที่ไม่มีอยู่แล้ว:")
        for fid in orphan[:20]:
            print(f"    {fid}  {(db[fid]['vault_path'] or '')[:72]}")
        if args.prune and not args.dry_run:
            for fid in orphan:
                execute_db("DELETE FROM csv_data_dict WHERE file_id=%s", (fid,))
            print(f"  ลบทิ้งแล้ว {len(orphan)} แถว")
        else:
            # ไม่ลบเองโดยไม่ถาม — อาจเป็นไฟล์ที่กำลังจะอัปโหลดกลับ หรือลบผิด
            print("  (ยังไม่ลบ — ใส่ --prune ถ้าต้องการลบจริง)")

    if missing:
        print("\nไฟล์ที่ยังไม่มีพจนานุกรม:")
        built = failed = 0
        for fid in missing:
            print(f"    {fid}  {live[fid][:72]}")
            if args.dry_run or not args.build_missing:
                continue
            try:
                import json

                from src.tools.data_dict import build_data_dict
                from src.tools.minio import read_file_bytes_impl

                path = live[fid]
                d = build_data_dict(fid, path, path.rsplit("/", 1)[-1],
                                    read_file_bytes_impl(fid))
                execute_db(
                    """INSERT INTO csv_data_dict (
                          file_id, vault_path, file_name, domain, indicator_th,
                          year_min, year_max, years, provinces, granularity,
                          row_count, col_count, key_province, key_district, key_year,
                          keywords, columns_json, unknown_cols, caveats,
                          counting_basis, confidence, source, built_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s::jsonb,%s,%s,%s,%s,%s,NOW())
                       ON CONFLICT (file_id) DO NOTHING""",
                    (fid, path, d["file_name"], _domain_of(path), d["indicator_th"],
                     d["year_min"], d["year_max"], d["years"], d["provinces"],
                     d["granularity"], d["row_count"], d["col_count"],
                     d["key_province"] or None, d["key_district"] or None,
                     d["key_year"] or None, d["keywords"],
                     json.dumps(d["columns"], ensure_ascii=False), d["unknown_cols"],
                     d.get("caveats") or [], d["counting_basis"] or None,
                     "auto", "reconcile"),
                )
                built += 1
            except Exception as exc:
                # ไฟล์ที่ไม่ใช่ CSV (เช่น PDF) สร้างพจนานุกรมไม่ได้เป็นเรื่องปกติ
                print(f"        ✗ {type(exc).__name__}: {str(exc)[:70]}")
                failed += 1
        if args.build_missing and not args.dry_run:
            print(f"  สร้างพจนานุกรมได้ {built} · ไม่ได้ {failed}")
        elif not args.dry_run:
            print("  (ยังไม่สร้าง — ใส่ --build-missing ถ้าต้องการสร้าง)")

    if not args.dry_run:
        print(f"\nอัปเดต path แล้ว {len(moved)} แถว")
    return 0


if __name__ == "__main__":
    sys.exit(main())
