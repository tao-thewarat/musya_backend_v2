"""API นำเข้าข้อมูลสถิติจาก MoPH Open Data เข้าคลัง แล้วรีเฟรชได้

เส้นทางการใช้งานจากหน้าเว็บ:
  1. ผู้ใช้วาง URL หรือชื่อตาราง → POST /preview  (ดูก่อนว่าได้อะไร ยังไม่เขียนอะไร)
  2. กด "นำเข้า"                → POST /import   (ดึงจริง → CSV → MinIO → พจนานุกรม)
  3. กด "รีเฟรช" ภายหลัง        → POST /refresh/{file_id} (ดึงใหม่ทับไฟล์เดิม)

ทำไมต้องเขียนเป็น CSV เข้า MinIO แทนที่จะเก็บใน DB: CSV pipeline ที่ใช้อยู่
(File Finder → Schema → Code Gen → Executor) อ่านจาก MinIO เท่านั้น
การเขียนเป็น CSV จึงใช้งานได้ทันทีโดยไม่ต้องแก้ pipeline เลย
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.db.pool import execute_db, query_db
from src.tools import hdc_opendata as hdc
from src.tools.minio import _bucket, _get_client, _load_path_index
from src.tools.vault_placement import build_vault_path, safe_segment

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/hdc", tags=["hdc-import"])


class PreviewReq(BaseModel):
    source: str                      # URL หรือชื่อตาราง


class ImportReq(BaseModel):
    source: str
    vault_path: str                  # โฟลเดอร์ปลายทาง เช่น D3_NCDs/โรคไต/ชื่อตัวชี้วัด
    title_th: str = ""
    report_url: str = ""
    years: list[str] | None = None   # None = ทุกปีที่ดึงได้


def _actor(email: str | None) -> str:
    return (email or "unknown").strip()


@router.post("/preview")
async def preview(body: PreviewReq):
    """แกะแหล่งที่มา แล้วส่องดูว่าจะได้อะไรก่อนนำเข้าจริง — ยังไม่เขียนอะไรทั้งนั้น

    ส่ง `resolved` กลับไปด้วยเพื่อให้หน้าเว็บเติมชื่อ/โฟลเดอร์ให้อัตโนมัติ
    ผู้ใช้จะได้ไม่ต้องพิมพ์ชื่อตัวชี้วัดยาว ๆ เอง (และไม่ต้องรู้จักชื่อตารางเลย)
    """
    info = hdc.resolve_source(body.source)
    if not info.get("table"):
        raise HTTPException(400, (
            "แกะแหล่งข้อมูลไม่ออก — วางได้ 3 แบบ: ชื่อตาราง (เช่น `s_ckd_stage_typearea`) · "
            "ลิงก์หน้า metadata ของ HDC (`.../standard-report-detail/{id}`) · "
            "หรือลิงก์ตารางสรุป (`.../summary-table/{catalog}/{table}/{id}`)"
        ))
    try:
        data = hdc.preview(info["table"])
    except Exception as exc:
        raise HTTPException(502, f"ติดต่อ Open Data API ไม่สำเร็จ: {exc}") from exc

    # เสนอที่เก็บให้ตามโครงสร้างโดเมนที่คลังใช้จริง ผู้ใช้จะได้ไม่ต้องเดาเอง
    folder, fname = build_vault_path(info.get("title_th") or info["table"],
                                     info.get("category", ""))
    return {**data, "resolved": info, "suggest": {"vault_path": folder, "title_th": fname}}


class SubcatalogReq(BaseModel):
    source: str                      # URL หน้า standard-subcatalog หรือ cat_id


@router.post("/subcatalog")
async def subcatalog(body: SubcatalogReq):
    """ลิสต์ตัวชี้วัดทั้งหมดในหมวดเดียว พร้อมบอกว่าตัวไหนนำเข้าไปแล้ว

    ใช้สารบัญจริงของต้นทาง ไม่ใช่การค้นด้วยคำ จึงไม่มีทางจับคู่ผิดหมวด
    """
    cat_id = hdc.parse_subcatalog_id(body.source)
    if not cat_id:
        raise HTTPException(400, (
            "ต้องเป็นลิงก์หน้าหมวด (`.../standard-subcatalog/{id}`) "
            "หรือลิงก์ที่มี `?subcatalogId=` ต่อท้าย"
        ))
    try:
        items = hdc.list_subcatalog(cat_id)
    except Exception as exc:
        # 503 = ล่มชั่วคราว กดลองใหม่ได้ · 502 = พังจริง ลองใหม่ก็ไม่ช่วย
        # หน้าเว็บใช้รหัสนี้ตัดสินว่าจะโชว์ปุ่ม "ลองใหม่" ไหม
        raise HTTPException(
            503 if hdc.is_retryable(exc) else 502,
            f"ติดต่อ Open Data API ไม่สำเร็จ: {exc}"
            + (" — ต้นทางล่มชั่วคราว กดลองใหม่ได้" if hdc.is_retryable(exc) else ""),
        ) from exc
    if not items:
        raise HTTPException(404, "หมวดนี้ไม่มีรายงาน หรือรหัสหมวดไม่ถูกต้อง")

    done = {r["table_name"]: r["file_id"]
            for r in query_db("SELECT table_name, file_id FROM hdc_import")}
    for it in items:
        it["imported_file_id"] = done.get(it["table"], "")
        # เสนอที่เก็บจากฝั่งเซิร์ฟเวอร์ — หน้าเว็บต้องไม่คำนวณโฟลเดอร์เอง
        # (เคยพลาดมาแล้ว: หน้าเว็บใช้ตรรกะของตัวเอง ไฟล์เลยไปกองที่ "HDC/" ทั้งหมด)
        folder, fname = build_vault_path(it["title_th"] or it["table"], it["category"])
        it["suggest"] = {"vault_path": folder, "title_th": fname}
    return {"catId": cat_id, "category": items[0]["category"], "items": items}


@router.get("/categories")
async def categories():
    """สารบัญหมวดทั้งหมด — ให้หน้าเว็บทำ dropdown ได้โดยผู้ใช้ไม่ต้องรู้ cat_id"""
    try:
        return {"items": hdc.list_categories()}
    except Exception as exc:
        raise HTTPException(502, f"ติดต่อ Open Data API ไม่สำเร็จ: {exc}") from exc


def _quote_within(text: str, max_encoded: int) -> str:
    """URL-encode แล้วตัดให้ผลลัพธ์ยาวไม่เกิน max_encoded ไบต์ (ตัดที่ขอบอักขระ)

    ⚠️ ต้องวัดที่ "ความยาวหลังเข้ารหัส" ไม่ใช่ก่อน — ภาษาไทย 1 ตัวอักษรกลายเป็น
    `%E0%B8%81` = 9 ไบต์ ⇒ ชื่อ 150 ตัวอักษรบวมเป็น ~1,350 ไบต์
    เดิมตัดที่ 150 ตัวอักษรก่อนเข้ารหัส ทำให้ MinIO ปฏิเสธด้วย MetadataTooLarge
    (เจอจริงกับ `s_kpi_ckd_hba1c` ที่ชื่อยาว — นำเข้าล้มด้วย 500 โดยไม่มีคำอธิบาย)

    ตัดทีละอักขระเพื่อไม่ให้ตัดกลาง escape sequence จนได้สตริงที่ decode ไม่ออก
    """
    out = ""
    for ch in text:
        nxt = out + urllib.parse.quote(ch)
        if len(nxt) > max_encoded:
            break
        out = nxt
    return out


def _write_csv_to_minio(file_id: str, vault_path: str, file_name: str, blob: bytes) -> None:
    """เขียนไฟล์เข้า bucket เดียวกับที่อัปโหลดผ่านหน้าเว็บ พร้อม metadata แบบเดียวกัน

    ⚠️ ต้องใส่ x-amz-meta-path ให้ครบ — `_load_path_index()` ใช้ค่านี้สร้าง folder tree
    ที่ File Finder Agent ใช้เลือกไฟล์ ถ้าไม่มี ไฟล์จะมองไม่เห็นจากปุ่ม "ข้อมูลสถิติ"
    """
    import io

    client, bucket = _get_client(), _bucket()
    client.put_object(
        bucket, file_id, io.BytesIO(blob), len(blob),
        content_type="text/csv",
        metadata={
            # งบรวมของ metadata ทั้งก้อนที่ MinIO ยอมคือ ~2 KB — แบ่งให้ path มากกว่า
            # เพราะเป็นค่าที่ระบบใช้จริงในการค้นหา ส่วน name ใช้แค่แสดงผล
            "name": _quote_within(file_name, 500),
            "path": _quote_within(f"{vault_path}/{file_name}", 1000),
            "extension": "csv",
            "previewkind": "csv",
            "size": str(len(blob)),
            "source": "hdc_opendata",
        },
    )
    _load_path_index(force=True)     # ให้ค้นเจอทันทีโดยไม่ต้องรีสตาร์ท


def _do_import(table: str, vault_path: str, title: str, report_url: str,
               years: list[str] | None, actor: str, file_id: str = "",
               force: bool = False) -> dict:
    schema = hdc.get_schema(table)
    result = hdc.fetch_zone10(table, years)
    rows = result["rows"]
    if not rows:
        raise HTTPException(422, f"ดึงข้อมูลไม่ได้เลยสักปี (ตาราง {table})")

    # ── กันการรีเฟรชแล้วข้อมูลหายเงียบ ๆ ────────────────────────────────────
    # เจอจริง: รีเฟรชครั้งที่ 2 ได้ 9 ปี / 72,724 แถว จากเดิม 10 ปี / 75,557 แถว
    # เพราะปีหนึ่งพลาดชั่วคราว (API คืน error ชั่วครู่) ถ้าเขียนทับไปเลย
    # ข้อมูลทั้งปีจะหายโดยไม่มีใครรู้ — ต้องหยุดแล้วให้คนตัดสินใจ
    if file_id and not force:
        prev = query_db(
            "SELECT usable_years, row_count FROM hdc_import WHERE file_id=%s", (file_id,)
        )
        if prev:
            old_years = set(prev[0].get("usable_years") or [])
            new_years = set(result["usableYears"])
            lost = sorted(old_years - new_years)
            if lost:
                raise HTTPException(409, (
                    f"หยุดไว้ก่อน — ดึงรอบนี้ได้ไม่ครบเท่าเดิม ปีที่หายไป: {', '.join(lost)} "
                    f"(เดิม {len(old_years)} ปี / {prev[0].get('row_count'):,} แถว "
                    f"→ รอบนี้ {len(new_years)} ปี / {len(rows):,} แถว) "
                    f"ไฟล์เดิมยังอยู่ครบ ไม่ได้ถูกเขียนทับ · "
                    f"ถ้าต้นทางตัดข้อมูลปีเก่าออกจริง ให้กดรีเฟรชแบบยืนยันทับ"
                ))

    blob = hdc.to_csv(table, rows, schema)
    file_id = file_id or str(uuid.uuid4().int)[:6]
    # ⚠️ กัน "/" ที่ปนมาในชื่อตัวชี้วัด (เช่น "ผู้ป่วย DM และ/หรือ HT")
    # ถ้าปล่อยผ่าน MinIO จะตีความเป็นตัวคั่นโฟลเดอร์ แล้วเกิดโฟลเดอร์ซ้อนที่อ่านไม่รู้เรื่อง
    # กันที่นี่ด้วยอีกชั้น ต่อให้ผู้เรียกลืมล้างมาก็ไม่พัง
    name = f"{safe_segment(title) or table}.csv"
    vault_path = "/".join(safe_segment(p) for p in vault_path.split("/") if safe_segment(p))
    _write_csv_to_minio(file_id, vault_path, name, blob)

    provinces = sorted({hdc.ZONE10[r["areacode"][:2]] for r in rows})
    date_com = {}
    for r in rows:
        date_com.setdefault(hdc.ZONE10[r["areacode"][:2]], r["date_com"])

    execute_db(
        """INSERT INTO hdc_import (file_id, table_name, title_th, vault_path, report_url,
                                   declared_years, usable_years, row_count, provinces,
                                   date_com, last_sync_at, last_sync_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW(),%s)
           ON CONFLICT (file_id) DO UPDATE SET
             usable_years=EXCLUDED.usable_years, row_count=EXCLUDED.row_count,
             provinces=EXCLUDED.provinces, date_com=EXCLUDED.date_com,
             last_sync_at=NOW(), last_sync_by=EXCLUDED.last_sync_by""",
        (file_id, table, title or table, vault_path, report_url,
         [p["year"] for p in result["perYear"]], result["usableYears"],
         len(rows), provinces, json.dumps(date_com, ensure_ascii=False), actor),
    )

    # สร้างพจนานุกรมข้อมูลทันที — ไฟล์ใหม่จึงมี metadata ครบตั้งแต่วันแรก
    # พร้อมคำอธิบายคอลัมน์ภาษาไทยจาก report_schema ซึ่งไฟล์อัปโหลดเองไม่มี
    dict_warn = ""
    try:
        from src.tools.data_dict import build_data_dict
        d = build_data_dict(file_id, f"{vault_path}/{name}", name, blob)
        desc_map = {c["name"]: c["desc"] for c in schema if c.get("desc")}
        for col in d["columns"]:
            if desc_map.get(col["name"]):
                col["desc"] = desc_map[col["name"]]
        # `build_data_dict` ตัดสิน unknown_cols จาก "ชื่อคอลัมน์" ก่อนที่เราจะเติมคำอธิบาย
        # ⇒ คอลัมน์อย่าง target/result1 ที่ต้นทางอธิบายไว้ครบ จะยังติดธงว่าไม่รู้จักอยู่ดี
        # ต้องล้างธงตามหลัง ไม่งั้น Schema Analyst จะเห็นเป็นคอลัมน์ปริศนาทั้งที่มีนิยามแล้ว
        d["unknown_cols"] = [c for c in d["unknown_cols"] if not desc_map.get(c)]
        # `build_data_dict` ตรวจจากเนื้อไฟล์มาแล้ว (แถวผลรวมปน ฯลฯ) ส่วนตัวนี้ตรวจ
        # จากสถิติรายปีตอนดึงซึ่งไฟล์ไม่มี ⇒ ต้องรวมกัน ไม่ใช่เขียนทับ
        # dict.fromkeys กันซ้ำเผื่อทั้งสองทางจับ Work Load ได้พร้อมกัน (ข้อความเดียวกันเป๊ะ)
        d["caveats"] = list(dict.fromkeys(
            (d.get("caveats") or []) + _detect_caveats(table, schema, result)
        ))
        # นิยามเชิงปฏิบัติการจากหน้า HDC — opendata ไม่มีข้อมูลชุดนี้เลย
        # (รหัสโรค ICD ที่รวม/ตัดออก · รหัส LAB ที่ต้องมี · เกณฑ์ตัดค่า)
        # เป็นของเสริม ดึงไม่ได้ก็ต้องนำเข้าต่อได้
        rid = _report_code(report_url)
        if rid:
            n = hdc.get_report_notice(rid)
            d["definition"] = n.get("notice", "")
            d["denominator_th"] = n.get("b_name", "")
            d["numerator_th"] = n.get("a_name", "")
            d["kpi_target"] = n.get("target", "")
        _save_dict(d, table)
    except Exception as exc:
        dict_warn = f"สร้างพจนานุกรมไม่สำเร็จ: {exc}"
        logger.warning(dict_warn)

    return {
        "ok": True, "file_id": file_id, "table": table, "rows": len(rows),
        "usableYears": result["usableYears"], "provinces": provinces,
        "perYear": result["perYear"], "vault_path": f"{vault_path}/{name}",
        "warning": dict_warn,
    }


_RID_RE = re.compile(r"/standard-report-detail/([a-f0-9]{16,40})")


def _report_code(report_url: str) -> str:
    """ดึง reportCode จากลิงก์หน้า HDC — ใช้เป็นกุญแจไปขอนิยามตัวชี้วัด"""
    m = _RID_RE.search(report_url or "")
    return m.group(1) if m else ""


def _detect_caveats(table: str, schema: list[dict], result: dict) -> list[str]:
    """หาข้อควรระวังจากข้อมูลที่เพิ่งดึงมา แล้วเก็บลง `csv_data_dict.caveats`

    `describe_for_prompt()` แนบ caveats เข้าพรอมต์ให้ทุกครั้งอยู่แล้ว การเติมตรงนี้
    จึงถึงมือ AI ทันทีโดยไม่ต้องแก้ pipeline

    ตรวจจากตัวเลขจริง ไม่ใช่เดาจากชื่อ — เพราะรอยต่อวิธีนับดูออกจากจำนวนแถวเท่านั้น
    """
    out: list[str] = []

    # ── 1. รอยต่อวิธีนับหน่วยบริการ ────────────────────────────────────────
    # เจอจริงกับ s_ckd_stage_hosp: 2560=486 · 2562=535 แล้ว 2563 ร่วงเหลือ 75
    # (เดิมน่าจะรวม รพ.สต. ต่อมานับเฉพาะโรงพยาบาล)
    # ถ้าไม่เตือน AI จะสรุปว่า "ผู้ป่วยลดลง 85%" ทั้งที่เป็นการเปลี่ยนวิธีนับ
    ok = [p for p in result.get("perYear", []) if p.get("ok") and p.get("rows")]
    for a, b in zip(ok, ok[1:]):
        hi, lo = max(a["rows"], b["rows"]), min(a["rows"], b["rows"])
        if hi >= lo * 3:
            out.append(
                f"จำนวนแถวเปลี่ยนกะทันหันระหว่างปี {a['year']} ({a['rows']:,} แถว) "
                f"กับ {b['year']} ({b['rows']:,} แถว) — น่าจะเปลี่ยนวิธีนับหน่วยบริการ "
                f"**ห้ามนำสองช่วงนี้มาเทียบแนวโน้มกันตรง ๆ** ถ้าจะทำกราฟแนวโน้ม "
                f"ให้ใช้เฉพาะตั้งแต่ปี {b['year']} เป็นต้นไป หรือบอกผู้ใช้ว่ามีรอยต่อ"
            )

    # ── 2. Work Load ไม่ใช่จำนวนคน ─────────────────────────────────────────
    # ตาราง `*_hosp` นับ "ผู้มารับบริการที่โรงพยาบาล" — HDC ระบุเองว่า
    # "ผู้ป่วย 1 คน สามารถเป็นผู้รับบริการได้มากกว่า 1 โรงพยาบาล"
    if table.endswith("_hosp"):
        out.append(
            "ตัวเลขนี้เป็น Work Load ไม่ใช่จำนวนคน — ผู้ป่วย 1 คนไปรับบริการหลาย "
            "โรงพยาบาลจะถูกนับหลายครั้ง ต้องตอบว่า 'มีการมารับบริการ N ราย' "
            "ห้ามตอบว่า 'มีผู้ป่วย N คน'"
        )

    # ── 3. ปีที่ต้นทางให้ไม่ครบ ────────────────────────────────────────────
    partial = [p for p in result.get("perYear", [])
               if p.get("rows") and not p.get("ok")]
    for p in partial:
        out.append(
            f"ปี {p['year']} ไม่ได้ถูกบันทึกลงไฟล์ เพราะต้นทางไม่มีข้อมูลของ "
            f"{', '.join(p['missing'])} — ถ้าผู้ใช้ถามถึงปีนี้ ให้บอกว่าข้อมูลไม่ครบทั้งเขต"
        )
    return out


def _save_dict(d: dict, table: str) -> None:
    """บันทึกพจนานุกรมลง DB

    ⚠️ นิยามจากต้นทาง (definition/numerator_th/denominator_th/kpi_target) ต้องอยู่
    ในคำสั่งนี้ด้วย — เดิม `_do_import` ใส่ค่าลง dict `d` แล้วแต่ INSERT ไม่ได้รับไป
    ⇒ ดึงมาได้แต่ทิ้งทุกครั้ง ทั้งคนและ AI จึงไม่เคยเห็นนิยามเลยสักไฟล์
    """
    execute_db(
        """INSERT INTO csv_data_dict (
              file_id, vault_path, file_name, domain, indicator_th,
              year_min, year_max, years, provinces, granularity, row_count, col_count,
              key_province, key_district, key_year, keywords,
              columns_json, unknown_cols, caveats, counting_basis, confidence, source,
              definition, numerator_th, denominator_th, kpi_target, built_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,
                   %s,%s,%s,%s,NOW())
           ON CONFLICT (file_id) DO UPDATE SET
             years=EXCLUDED.years, year_min=EXCLUDED.year_min, year_max=EXCLUDED.year_max,
             provinces=EXCLUDED.provinces, row_count=EXCLUDED.row_count,
             columns_json=EXCLUDED.columns_json, unknown_cols=EXCLUDED.unknown_cols,
             caveats=EXCLUDED.caveats,
             counting_basis=EXCLUDED.counting_basis,
             -- เขียนทับเฉพาะเมื่อรอบนี้ดึงนิยามมาได้ — ต้นทางล่มแล้วได้ค่าว่าง
             -- ต้องไม่ไปล้างนิยามที่เคยดึงสำเร็จไว้แล้วทิ้ง
             definition=coalesce(nullif(EXCLUDED.definition,''), csv_data_dict.definition),
             numerator_th=coalesce(nullif(EXCLUDED.numerator_th,''), csv_data_dict.numerator_th),
             denominator_th=coalesce(nullif(EXCLUDED.denominator_th,''), csv_data_dict.denominator_th),
             kpi_target=coalesce(nullif(EXCLUDED.kpi_target,''), csv_data_dict.kpi_target),
             built_at=NOW()""",
        (d["file_id"], d["vault_path"], d["file_name"], d["domain"] or None, d["indicator_th"],
         d["year_min"], d["year_max"], d["years"], d["provinces"], d["granularity"],
         d["row_count"], d["col_count"], d["key_province"] or None, d["key_district"] or None,
         d["key_year"] or None, d["keywords"], json.dumps(d["columns"], ensure_ascii=False),
         d["unknown_cols"], d.get("caveats") or [],
         d["counting_basis"] or None, "auto", "hdc_opendata",
         d.get("definition", ""), d.get("numerator_th", ""),
         d.get("denominator_th", ""), d.get("kpi_target", "")),
    )


@router.post("/import")
async def import_table(body: ImportReq, x_user_email: str | None = Header(default=None)):
    info = hdc.resolve_source(body.source)
    table = info.get("table")
    if not table:
        raise HTTPException(400, "ระบุชื่อตารางหรือ URL ไม่ถูกต้อง")
    if not body.vault_path.strip():
        raise HTTPException(400, "ต้องระบุโฟลเดอร์ปลายทาง")
    # ผู้ใช้ที่วางแค่ URL มาไม่ต้องพิมพ์ชื่อ/ลิงก์เอง — เติมจากที่แกะได้ให้เลย
    return _do_import(table, body.vault_path.strip("/"),
                      body.title_th.strip() or info.get("title_th", ""),
                      body.report_url.strip() or info.get("report_url", ""),
                      body.years, _actor(x_user_email))


@router.post("/refresh/{file_id}")
async def refresh(file_id: str, force: bool = False,
                  x_user_email: str | None = Header(default=None)):
    """ดึงใหม่ทับไฟล์เดิม — ใช้ค่าที่บันทึกไว้ตอนนำเข้าครั้งแรก

    force=false (ค่าเริ่มต้น) จะหยุดถ้าดึงได้ปีน้อยกว่าเดิม เพื่อไม่ให้ข้อมูลหายเงียบ ๆ
    """
    rows = query_db(
        "SELECT table_name, title_th, vault_path, report_url FROM hdc_import WHERE file_id=%s",
        (file_id,),
    )
    if not rows:
        raise HTTPException(404, "ไฟล์นี้ไม่ได้มาจาก HDC Open Data จึงรีเฟรชไม่ได้")
    r = rows[0]
    return _do_import(r["table_name"], r["vault_path"], r["title_th"] or "",
                      r["report_url"] or "", None, _actor(x_user_email),
                      file_id=file_id, force=force)


@router.get("/imports")
async def list_imports():
    """รายการไฟล์ที่มาจาก HDC — หน้าเว็บใช้แสดงปุ่มรีเฟรช"""
    return {"items": query_db(
        """SELECT file_id, table_name, title_th, vault_path, report_url,
                  usable_years, row_count, provinces, date_com,
                  last_sync_at, last_sync_by
           FROM hdc_import ORDER BY last_sync_at DESC"""
    )}
