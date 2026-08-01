"""จับคู่ตัวชี้วัดในคลัง กับตาราง HDC — เตรียมงานเฟส 4 (เลิกพึ่งไฟล์ Excel จาก สสจ.)

ทำไมต้องแยกชั้นความมั่นใจ แทนที่จะนำเข้าอัตโนมัติทั้งหมด:
API ค้นหาให้คะแนนตามคำที่ตรงกัน จึงจับผิดได้ง่ายมากเมื่อชื่อมีคำร่วมโดยบังเอิญ
เจอจริง 2026-07-31 — "ผู้ป่วยเบาหวานชนิดที่ 2 เข้าสู่ระยะสงบ (Remission)" ถูกจับคู่กับ
"ผู้ป่วยโรคซึมเศร้าหายทุเลา (Remission)" เพราะคำว่า Remission เหมือนกันคำเดียว
⇒ นำเข้าทับโดยไม่ตรวจ = เอาข้อมูลซึมเศร้าไปใส่แฟ้มเบาหวาน โดยไม่มีใครรู้

จึงคืนผลเป็น 3 ชั้น: ตรงเป๊ะ (นำเข้าได้เลย) · ต้องเลือก (คนตัดสิน) · ไม่พบ

    docker exec chatapp-python-ai python -m src.scripts.match_hdc_tables
    docker exec chatapp-python-ai python -m src.scripts.match_hdc_tables --exact-only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

HDRS = {"User-Agent": "MUSYA/1.0 (+health-region-10)", "Accept": "application/json"}
BASE = "https://opendata.moph.go.th/api/report_name"

# คำที่ปรากฏแทบทุกตัวชี้วัด ใช้ค้นแล้วได้ผลลัพธ์มั่วเต็มไปหมด
STOP = {"ร้อยละ", "ของ", "ที่", "และ", "ใน", "จำนวน", "อัตรา", "การ",
        "ได้รับ", "มี", "ต่อ", "เป็น", "ปีงบประมาณ", "กลุ่ม"}


def _norm(s: str) -> str:
    """ตัดวงเล็บ ช่องว่าง เครื่องหมาย เพื่อเทียบชื่อแบบไม่ติดรูปแบบการพิมพ์"""
    s = re.sub(r"\((?:work\s*load|coverage)\)", " ", s or "", flags=re.I)
    return re.sub(r"[\s\-–—()\[\],./]+", "", s).lower()


def _terms(name: str) -> list[str]:
    t = re.sub(r"[()\[\]/,\-–—0-9]+", " ", name or "")
    toks = [w for w in t.split() if len(w) >= 4 and w not in STOP]
    return sorted(set(toks), key=len, reverse=True)[:4]


def _search(q: str, limit: int = 25) -> list[dict]:
    url = f"{BASE}/{urllib.parse.quote(q)}/1/{limit}"
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers=HDRS)
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode()).get("data", [])
        except Exception:
            if i == 2:
                return []
            time.sleep(2 * (i + 1))
    return []


def match_one(indicator: str) -> dict:
    """คืน {level, table, title, candidates}

    level: exact = ชื่อตรงกันหลัง normalize ⇒ ปลอดภัยพอที่จะนำเข้าอัตโนมัติ
           choose = มีผู้สมัครแต่ไม่มีตัวไหนตรงเป๊ะ ⇒ ต้องให้คนเลือก
           none   = ไม่พบอะไรเลย
    """
    cands: dict[str, str] = {}
    for q in _terms(indicator):
        for d in _search(q):
            if d.get("source_table"):
                cands.setdefault(d["source_table"], (d.get("report_name") or "").strip())
        time.sleep(0.25)

    target = _norm(indicator)
    for tbl, title in cands.items():
        if _norm(title) == target:
            return {"level": "exact", "table": tbl, "title": title, "candidates": cands}
    if cands:
        return {"level": "choose", "table": "", "title": "", "candidates": cands}
    return {"level": "none", "table": "", "title": "", "candidates": {}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exact-only", action="store_true", help="แสดงเฉพาะที่ตรงเป๊ะ")
    args = ap.parse_args()

    from src.db.pool import query_db

    rows = query_db(
        "SELECT file_id, indicator_th, file_name FROM csv_data_dict "
        "WHERE source='upload' ORDER BY file_id"
    )
    buckets: dict[str, list] = {"exact": [], "choose": [], "none": []}
    for r in rows:
        name = (r["indicator_th"] or r["file_name"] or "").strip()
        res = match_one(name)
        buckets[res["level"]].append((r["file_id"], name, res))

    print(f"ตรวจ {len(rows)} ตัวชี้วัด — "
          f"ตรงเป๊ะ {len(buckets['exact'])} · ต้องเลือก {len(buckets['choose'])} · "
          f"ไม่พบ {len(buckets['none'])}\n")

    print("=" * 72)
    print("ตรงเป๊ะ — นำเข้าได้เลย")
    print("=" * 72)
    for fid, name, res in buckets["exact"]:
        print(f"  {fid}  {res['table']:26} {name[:52]}")

    if args.exact_only:
        return 0

    print("\n" + "=" * 72)
    print("ต้องให้คนเลือก — ชื่อไม่ตรงเป๊ะ อย่านำเข้าโดยไม่ดู")
    print("=" * 72)
    for fid, name, res in buckets["choose"]:
        print(f"  {fid}  {name[:64]}")
        for tbl, title in list(res["candidates"].items())[:4]:
            print(f"        {tbl:26} {title[:58]}")

    print("\n" + "=" * 72)
    print("ไม่พบ — ชื่อในคลังไม่ตรงกับศัพท์ของ HDC ต้องหาเอง")
    print("=" * 72)
    for fid, name, _ in buckets["none"]:
        print(f"  {fid}  {name[:64]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
