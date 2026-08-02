"""ล้าง HTML ที่หลุดเข้าไปในชื่อโฟลเดอร์/ไฟล์ของคลัง

    python -m src.scripts.clean_vault_html [--dry-run]

ต้นทาง HDC ใส่ `<font color=red>` ในชื่อรายงานเพื่อเน้นคำ ตัวนำเข้ารุ่นก่อน
ไม่ได้ล้างก่อนเอาไปทำ path ⇒ ได้โฟลเดอร์ชื่อ

    ประชากร<font color=red>ทะเบียนราษฏร์< font> ย้อนหลัง 3 ปี

ซึ่งคนอ่านไม่รู้เรื่อง และ AI จับคู่ชื่อโฟลเดอร์กับคำถามไม่ได้

ตัวนำเข้าแก้แล้ว (`clean_title` ใน hdc_opendata) — สคริปต์นี้ตามเก็บของเก่า
ใช้กลไกย้ายเดียวกับ `move_vault_folder` เพื่อให้ที่อยู่ครบทั้ง 3 แหล่งเสมอ
"""
from __future__ import annotations

import io
import json
import sys
import urllib.parse as up

from minio.commonconfig import REPLACE, CopySource

from src.db.pool import execute_db
from src.tools.hdc_opendata import clean_title
from src.tools.minio import _bucket, _get_client
from src.scripts.move_vault_folder import _quote_within, _NAME_BUDGET, _PATH_BUDGET


def _clean_path(path: str) -> str:
    """ล้างทีละชั้น — `/` เป็นตัวคั่นโฟลเดอร์ ห้ามให้ตัวล้างไปยุ่ง"""
    return "/".join(clean_title(seg) for seg in path.split("/"))


def main(dry_run: bool = False) -> int:
    client, bucket = _get_client(), _bucket()
    jobs: list[tuple[str, str, str, str, str]] = []   # (id, name, path, new_name, new_path)

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
        try:
            side = json.loads(client.get_object(bucket, f"__pathdata__/{fid}.json").read())
            path = side.get("path") or path
            name = side.get("name") or name
        except Exception:
            pass
        if not path:
            continue

        new_path, new_name = _clean_path(path), clean_title(name)
        if (new_path, new_name) != (path, name):
            jobs.append((fid, name, path, new_name, new_path))

    print(f"พบชื่อที่มี HTML ปน {len(jobs)} ไฟล์\n")
    for fid, _, old, _, new in jobs[:8]:
        print(f"  [{fid}]\n    เดิม {old}\n    ใหม่ {new}")
    if dry_run:
        print("\n(dry-run — ยังไม่ได้เขียนอะไร)")
        return 0
    if not jobs:
        return 0

    ok, failed = 0, []
    for fid, _, _, new_name, new_path in jobs:
        try:
            body = json.dumps({"name": new_name, "path": new_path},
                              ensure_ascii=False).encode("utf-8")
            client.put_object(bucket, f"__pathdata__/{fid}.json", io.BytesIO(body), len(body),
                              content_type="application/json")

            stat = client.stat_object(bucket, fid)
            old_meta = {k.lower(): v for k, v in (stat.metadata or {}).items()}
            client.copy_object(
                bucket, fid, CopySource(bucket, fid), metadata_directive=REPLACE,
                metadata={
                    "x-amz-meta-name": _quote_within(new_name, _NAME_BUDGET),
                    "x-amz-meta-path": _quote_within(new_path, _PATH_BUDGET),
                    "x-amz-meta-extension": old_meta.get("x-amz-meta-extension", ""),
                    "x-amz-meta-previewkind": old_meta.get("x-amz-meta-previewkind", "unsupported"),
                    "x-amz-meta-size": old_meta.get("x-amz-meta-size", str(stat.size)),
                    "x-amz-meta-uploadedat": old_meta.get("x-amz-meta-uploadedat", "0"),
                })

            execute_db("UPDATE csv_data_dict SET vault_path=%s, file_name=%s WHERE file_id=%s",
                       (new_path, new_name, fid))
            execute_db("UPDATE hdc_import SET vault_path=%s WHERE file_id=%s", (new_path, fid))
            ok += 1
        except Exception as exc:
            failed.append((fid, f"{type(exc).__name__}: {exc}"))

    print(f"\nล้างสำเร็จ {ok} · ล้มเหลว {len(failed)}")
    for fid, why in failed[:10]:
        print(f"  ❌ [{fid}] {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(dry_run="--dry-run" in sys.argv))
