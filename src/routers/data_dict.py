"""พจนานุกรมข้อมูลของไฟล์ในคลัง — ให้หน้าเว็บแสดงข้าง ๆ ตัวอย่างไฟล์

ข้อมูลชุดนี้ AI ได้อ่านทุกครั้งที่วิเคราะห์อยู่แล้ว (ผ่าน `describe_for_prompt`)
แต่ **คนที่เปิดดูไฟล์กลับไม่เห็นอะไรเลย** — เห็นแค่ตาราง `F3 / F5 / a_name`
ที่ตีความไม่ได้ ทั้งที่ระบบรู้คำตอบอยู่แล้ว จึงเปิดให้หน้าเว็บดึงไปแสดงด้วย
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException

from src.db.pool import query_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/datadict", tags=["data-dict"])


@router.get("/{file_id}")
async def get_file_dict(file_id: str):
    rows = query_db(
        """SELECT d.file_id, d.file_name, d.vault_path, d.indicator_th, d.domain,
                  d.years, d.year_min, d.year_max, d.provinces, d.granularity,
                  d.row_count, d.col_count, d.key_province, d.key_district, d.key_year,
                  d.columns_json, d.unknown_cols, d.caveats, d.counting_basis,
                  d.confidence, d.source, d.built_at,
                  -- นิยามเชิงปฏิบัติการจากหน้ารายงาน HDC — ตอบคำถาม "ตัวเลขนี้นับใคร"
                  -- เดิมเก็บลง DB แล้วแต่ไม่ได้ส่งออกมา คนจึงไม่เคยเห็น
                  d.definition, d.numerator_th, d.denominator_th, d.kpi_target,
                  h.table_name, h.report_url, h.usable_years, h.last_sync_at,
                  -- วันที่ต้นทางประมวลผล แยกรายจังหวัด (แต่ละจังหวัดคนละเวลา)
                  h.date_com
           FROM csv_data_dict d
           LEFT JOIN hdc_import h USING (file_id)
           WHERE d.file_id = %s""",
        (file_id,),
    )
    if not rows:
        # ไฟล์ที่ยังไม่ได้ทำพจนานุกรม (เช่น PDF) ไม่ใช่ error — หน้าเว็บแค่ไม่ต้องแสดงแผง
        raise HTTPException(404, "ไฟล์นี้ยังไม่มีพจนานุกรมข้อมูล")

    d = dict(rows[0])
    cols = d.get("columns_json") or []
    if isinstance(cols, str):
        try:
            cols = json.loads(cols)
        except Exception:
            cols = []
    d["columns_json"] = cols
    return d
