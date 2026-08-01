"""อ่านพจนานุกรมข้อมูล (csv_data_dict) มาใช้ใน CSV pipeline

เฟส 2 — ป้อนคำอธิบายคอลัมน์ + ข้อควรระวังให้ Schema Analyst / Insight Analyst
เฟส 3 — ป้อนคำค้น (keywords) ให้ File Finder หาไฟล์เจอมากขึ้น

ทุกฟังก์ชันต้อง **ไม่ throw** — พจนานุกรมเป็นของเสริม ถ้าอ่านไม่ได้ pipeline
ต้องทำงานต่อได้เหมือนเดิม ไม่ใช่ล้มทั้งคำถาม
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def get_dict(file_id: str) -> dict:
    """พจนานุกรมของไฟล์เดียว — คืน {} ถ้าไม่มีหรืออ่านไม่ได้"""
    if not file_id:
        return {}
    try:
        from src.db.pool import query_db
        rows = query_db(
            """SELECT file_id, indicator_th, year_min, year_max, years, provinces,
                      granularity, row_count, key_province, key_district, key_year,
                      columns_json, caveats, counting_basis, confidence, unknown_cols,
                      definition, numerator_th, denominator_th, kpi_target
               FROM csv_data_dict WHERE file_id = %s""",
            (file_id,),
        )
        return rows[0] if rows else {}
    except Exception as exc:
        logger.warning("อ่าน csv_data_dict ไม่ได้: %s", exc)
        return {}


def search_file_ids(terms: list[str], domain: str = "", limit: int = 8) -> list[str]:
    """หา file_id จาก keywords — ใช้เสริม File Finder ที่ค้นได้เฉพาะชื่อโฟลเดอร์

    แก้ปัญหาที่วัดได้: ถาม "BMI" ไม่เจอไฟล์ `ค่าดัชนีมวลกาย` เพราะชื่อโฟลเดอร์
    ไม่มีคำนั้น · ตารางนี้เก็บคำพ้องไว้แล้วจึงค้นเจอ
    """
    if not terms:
        return []
    try:
        from src.db.pool import query_db
        sql = """
            SELECT file_id,
                   (SELECT count(*) FROM unnest(keywords) k
                     WHERE EXISTS (SELECT 1 FROM unnest(%s::text[]) t
                                    WHERE lower(k) LIKE '%%' || lower(t) || '%%'
                                       OR lower(t) LIKE '%%' || lower(k) || '%%')) AS hits
            FROM csv_data_dict
            WHERE TRUE
        """
        params: list = [terms]
        if domain:
            sql += " AND domain = %s"
            params.append(domain)
        sql += " ORDER BY hits DESC LIMIT %s"
        params.append(limit)
        rows = query_db(sql, tuple(params))
        return [r["file_id"] for r in rows if (r.get("hits") or 0) > 0]
    except Exception as exc:
        logger.warning("ค้น csv_data_dict ไม่ได้: %s", exc)
        return []


def describe_for_prompt(file_id: str) -> str:
    """สรุปพจนานุกรมเป็นข้อความสำหรับแนบเข้าพรอมป์

    คืน "" เมื่อไม่มีข้อมูล — ผู้เรียกต่อสตริงได้เลยโดยไม่ต้องเช็ค
    """
    d = get_dict(file_id)
    if not d:
        return ""

    lines = [f"=== คำอธิบายชุดข้อมูล [{file_id}] ==="]
    if d.get("indicator_th"):
        lines.append(f"ตัวชี้วัด: {d['indicator_th']}")

    scope = []
    if d.get("year_min"):
        scope.append(f"ปี {d['year_min']}–{d['year_max']}")
    if d.get("provinces"):
        scope.append(f"{len(d['provinces'])} จังหวัด ({', '.join(d['provinces'])})")
    if d.get("granularity"):
        scope.append(f"ระดับ{d['granularity']}")
    if d.get("row_count"):
        scope.append(f"{d['row_count']:,} แถว")
    if scope:
        # ⚠️ สำคัญ: ขอบเขตนี้อ่านจากเนื้อไฟล์จริง ไม่ใช่จากชื่อไฟล์ซึ่งเชื่อไม่ได้
        # (เจอจริง: ไฟล์ชื่อ "2569-2569" แต่ข้างในมีตั้งแต่ 2565)
        lines.append("ขอบเขตข้อมูลจริง: " + " · ".join(scope))
        lines.append("⚠️ ยึดขอบเขตนี้ ไม่ต้องเชื่อปีที่ปรากฏในชื่อไฟล์")

    keys = [f"{k}='{d[f'key_{k2}']}'" for k, k2 in
            (("จังหวัด", "province"), ("อำเภอ", "district"), ("ปี", "year"))
            if d.get(f"key_{k2}")]
    if keys:
        lines.append("คอลัมน์แกน: " + " · ".join(keys))

    # ── นิยามเชิงปฏิบัติการจากต้นทาง ─────────────────────────────────────────
    # ตอบคำถามที่คนทำนโยบายถามเป็นอย่างแรกเสมอ: "ตัวเลขนี้นับใคร"
    # `report_schema` ให้แค่ชื่อคอลัมน์สั้น ๆ ส่วนนี้มาจากหน้า HDC ซึ่งมีเกณฑ์จริง
    if d.get("numerator_th"):
        lines.append(f"ตัวตั้ง (A): {d['numerator_th']}")
    if d.get("denominator_th"):
        lines.append(f"ตัวหาร (B): {d['denominator_th']}")
    if d.get("kpi_target"):
        # รู้เกณฑ์แล้วจึงตอบคำถาม "ผ่านเกณฑ์ไหม" ได้โดยไม่ต้องให้คนบอก
        lines.append(f"เป้าหมายตัวชี้วัด: ร้อยละ {d['kpi_target']}")
    if d.get("definition"):
        lines.append("นิยาม/หมายเหตุจากต้นทาง (HDC):")
        lines.extend(f"  {ln}" for ln in str(d["definition"]).splitlines() if ln.strip())

    cols = d.get("columns_json") or []
    if isinstance(cols, str):
        try:
            cols = json.loads(cols)
        except Exception:
            cols = []
    ROLE_TH = {"denominator": "ตัวหาร (ฐานประชากรเป้าหมาย)",
               "numerator": "ตัวตั้ง (ผู้ผ่านเกณฑ์)",
               "percentage": "ร้อยละ", "key": "แกน", "measure": "ค่าวัด"}
    described = [c for c in cols if c.get("role") in ("denominator", "numerator", "percentage")]
    if described:
        lines.append("บทบาทของคอลัมน์:")
        for c in described[:16]:
            desc = f" — {c['desc']}" if c.get("desc") else ""
            lines.append(f"  · {c['name']} = {ROLE_TH.get(c['role'], c['role'])}{desc}")

    basis = d.get("counting_basis")
    if basis:
        # ยืนยันจาก HDC schema: typearea = "ในเขตรับผิดชอบ" ตัดซ้ำด้วยเลขบัตร ปชช.
        # chronicfu = ผู้มารับบริการจริง 1 คนนับได้หลายครั้ง — ต่างกันได้ถึง 18%
        note = {
            "typearea": "ข้อมูลนับตามเขตรับผิดชอบ (Typearea) — ตัดความซ้ำซ้อนแล้ว 1 คน 1 record",
            "chronicfu": "ข้อมูลนับจากผู้มารับบริการจริง (CHRONICFU) — 1 คนนับได้หลายครั้ง",
            "both": ("ไฟล์นี้มี 2 ชุดตัวเลข: Typearea = ในเขตรับผิดชอบ (ตัดซ้ำแล้ว) "
                     "และ CHRONICFU = ผู้มารับบริการจริง (นับซ้ำได้) "
                     "**ต้องระบุให้ชัดว่าใช้ชุดไหน** ตัวเลขต่างกันมาก"),
        }.get(basis)
        if note:
            lines.append(f"⚠️ {note}")

    for cv in (d.get("caveats") or []):
        lines.append(f"⚠️ {cv}")

    unknown = d.get("unknown_cols") or []
    if unknown:
        # ห้ามให้ AI เดาความหมายเองแล้วนำเสนอเหมือนรู้จริง
        lines.append(f"⚠️ คอลัมน์ที่ยังไม่มีใครยืนยันความหมาย: {', '.join(unknown)}")
        lines.append("   ถ้าจำเป็นต้องใช้ ให้บอกผู้ใช้ตรง ๆ ว่าตีความเอง")

    return "\n".join(lines)
