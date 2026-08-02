"""ย้าย/เปลี่ยนชื่อโฟลเดอร์ในคลังไฟล์ — แก้ที่อยู่ให้ครบทั้ง 3 แหล่ง

    python -m src.scripts.move_vault_folder <ต้นทาง> <ปลายทาง> [--dry-run]
    python -m src.scripts.move_vault_folder "D5_Other" "D6_Other"

โฟลเดอร์ในระบบนี้ไม่มีอยู่จริง — เป็นแค่ prefix ของ `path` บนไฟล์แต่ละตัว
(object ใน MinIO ชื่อเป็นตัวเลข ส่วนโครงสร้างอยู่ใน `x-amz-meta-path`)
⇒ "ย้ายโฟลเดอร์" = แก้ prefix ของทุกไฟล์ข้างใน

**ที่อยู่ของไฟล์หนึ่งถูกเก็บไว้ 3 ที่** ที่หลุดจากกันได้ ต้องเขียนให้ครบทั้งหมด:

  1. `x-amz-meta-path` บน object      ← AI อ่านจากตรงนี้ (`_load_path_index`)
  2. `__pathdata__/{id}.json` sidecar ← หน้าเว็บอ่านจากตรงนี้
  3. `csv_data_dict.vault_path` + `hdc_import.vault_path` ใน Postgres

ตัวตัดชื่อคิดตาม **ความยาวหลังเข้ารหัส** ไม่ใช่จำนวนอักษร เพราะอักษรไทย 1 ตัว
กลายเป็น 9 ไบต์เมื่อ percent-encode ⇒ 150 อักษรได้ 1,350 ไบต์ ซึ่งชนโควตา
metadata ของ MinIO (เจอจริง 8 ไฟล์ที่ metadata สั้นกว่า sidecar อยู่แล้ว)
"""
from __future__ import annotations

import json
import sys
import urllib.parse as up

from src.db.pool import execute_db
from src.tools.minio import _bucket, _get_client

_PATH_BUDGET = 1000
_NAME_BUDGET = 500


def _quote_within(text: str, max_encoded: int) -> str:
    """percent-encode แล้วตัดที่ขอบตัวอักษร ไม่ตัดกลาง escape sequence"""
    full = up.quote(text, safe="")
    if len(full) <= max_encoded:
        return full
    out = ""
    for ch in text:
        piece = up.quote(ch, safe="")
        if len(out) + len(piece) > max_encoded:
            break
        out += piece
    return out


def _replace_prefix(path: str, old: str, new: str) -> str | None:
    """เทียบแบบมีขอบเขต — `D3_NCD` ต้องไม่ไปโดน `D3_NCDs` ที่คนละโฟลเดอร์"""
    if path == old:
        return new
    if path.startswith(old + "/"):
        return new + path[len(old):]
    return None


def main(source: str, target: str, dry_run: bool = False) -> int:
    client, bucket = _get_client(), _bucket()

    jobs: list[tuple[str, str, str, str]] = []   # (file_id, name, from, to)
    for obj in client.list_objects(bucket, recursive=True):
        fid = obj.object_name
        if fid.startswith("__"):
            continue

        try:
            stat = client.stat_object(bucket, fid)
        except Exception:
            continue
        meta = {k.lower(): v for k, v in (stat.metadata or {}).items()}
        path = up.unquote(meta.get("x-amz-meta-path", ""))
        name = up.unquote(meta.get("x-amz-meta-name", "")) or fid

        # sidecar เก็บ path เต็มไม่ตัด — เชื่อถือได้มากกว่า metadata ที่อาจถูกตัด
        try:
            side = json.loads(client.get_object(bucket, f"__pathdata__/{fid}.json").read())
            path = side.get("path") or path
            name = side.get("name") or name
        except Exception:
            pass

        if not path:
            continue
        nxt = _replace_prefix(path, source, target)
        if nxt:
            jobs.append((fid, name, path, nxt))

    print(f"พบไฟล์ที่ต้องย้าย {len(jobs)} ไฟล์\n  {source}  →  {target}\n")
    if not jobs:
        return 0
    for fid, _, frm, to in jobs[:5]:
        print(f"  [{fid}] {frm}\n        → {to}")
    if len(jobs) > 5:
        print(f"  … และอีก {len(jobs) - 5} ไฟล์")

    if dry_run:
        print("\n(dry-run — ยังไม่ได้เขียนอะไร)")
        return 0

    ok = 0
    failed: list[tuple[str, str]] = []
    for fid, name, _, to in jobs:
        try:
            # 1) sidecar ก่อน — เป็นตัวจริงเมื่อ metadata ถูกตัด
            body = json.dumps({"name": name, "path": to}, ensure_ascii=False).encode("utf-8")
            import io as _io
            client.put_object(bucket, f"__pathdata__/{fid}.json", _io.BytesIO(body), len(body),
                              content_type="application/json")

            # 2) metadata บน object — MinIO แก้ในที่ไม่ได้ ต้อง copy ทับตัวเองพร้อม REPLACE
            stat = client.stat_object(bucket, fid)
            old = {k.lower(): v for k, v in (stat.metadata or {}).items()}
            new_meta = {
                "x-amz-meta-name": _quote_within(name, _NAME_BUDGET),
                "x-amz-meta-path": _quote_within(to, _PATH_BUDGET),
                "x-amz-meta-extension": old.get("x-amz-meta-extension", ""),
                "x-amz-meta-previewkind": old.get("x-amz-meta-previewkind", "unsupported"),
                "x-amz-meta-size": old.get("x-amz-meta-size", str(stat.size)),
                "x-amz-meta-uploadedat": old.get("x-amz-meta-uploadedat", "0"),
            }
            from minio.commonconfig import REPLACE, CopySource
            client.copy_object(bucket, fid, CopySource(bucket, fid),
                               metadata=new_meta, metadata_directive=REPLACE)

            # 3) ฐานข้อมูล — ไม่มีแถวไม่ใช่ error แค่ไม่มีอะไรให้อัปเดต
            execute_db("UPDATE csv_data_dict SET vault_path=%s WHERE file_id=%s", (to, fid))
            execute_db("UPDATE hdc_import SET vault_path=%s WHERE file_id=%s", (to, fid))
            ok += 1
        except Exception as exc:
            failed.append((fid, f"{type(exc).__name__}: {exc}"))

    print(f"\nย้ายสำเร็จ {ok} · ล้มเหลว {len(failed)}")
    for fid, why in failed[:10]:
        print(f"  ❌ [{fid}] {why}")
    # ย้ายได้บางส่วนไม่ใช่ความสำเร็จ — ไฟล์ที่ตกค้างจะอยู่คนละที่กับพวกเดียวกัน
    return 1 if failed else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], args[1], dry_run="--dry-run" in sys.argv))
