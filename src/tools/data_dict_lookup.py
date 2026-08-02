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
            -- ข้ามไฟล์ที่ถูกแทนที่ด้วยเวอร์ชันใหม่กว่า (ดู migration 036)
            -- ตาราง HDC เดียวเคยถูกนำเข้าซ้ำถึง 4 ครั้ง ⇒ File Finder เห็นหลายเวอร์ชัน
            -- แล้วเลือกแบบสุ่ม ทำให้คำถามเดิมได้คำตอบต่างกันทุกครั้ง
            WHERE superseded_by IS NULL
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


def catalog_for_finder(domain: str = "", limit: int = 120) -> str:
    """สารบัญ metadata แบบย่อ สำหรับให้ File Finder เลือกไฟล์

    **ทำไมต้องมี:** เดิม File Finder เห็นแค่ "ชื่อโฟลเดอร์" ตอนตัดสินใจ
    ส่วน metadata (ตัวชี้วัด · ปี · จังหวัด · ระดับ · คำเตือน) ถูกส่งให้ *หลังจาก*
    เลือกไฟล์ไปแล้ว ⇒ ตัวที่ต้องตัดสินใจกลับเป็นตัวที่ไม่มีข้อมูลประกอบเลย

    อธิบายความผิดพลาดที่วัดได้จริง 2026-08-03 ทั้ง 3 เคส:
      - ถาม "ผู้ป่วยความดันควบคุมได้ดี" → หยิบไฟล์ **เบาหวาน**
      - ถาม "อัตราฆ่าตัวตายสำเร็จ"      → หยิบไฟล์ **ทำร้ายตนเองเข้าถึงบริการ**
      - ถาม "ผู้ป่วยซึมเศร้าเข้าถึงบริการ" → หยิบไฟล์ **SMI-V**
    ทั้งสามเคส `indicator_th` ในพจนานุกรมบอกไว้ถูกต้องอยู่แล้ว แค่ไม่ถูกส่งไปให้ดู

    เขียนให้สั้นที่สุดที่ยังตัดสินใจได้ เพราะต้องใส่ทั้งโดเมนลงใน 1 พรอมต์
    """
    try:
        from src.db.pool import query_db
        sql = """
            SELECT file_id, indicator_th, year_min, year_max, granularity,
                   array_length(provinces, 1) AS n_prov,
                   array_length(caveats, 1)   AS n_cav,
                   vault_path
            FROM csv_data_dict
            WHERE superseded_by IS NULL
        """
        params: list = []
        if domain:
            sql += " AND domain = %s"
            params.append(domain)
        sql += " ORDER BY indicator_th LIMIT %s"
        params.append(limit)
        rows = query_db(sql, tuple(params))
    except Exception as exc:
        logger.warning("อ่านสารบัญพจนานุกรมไม่ได้: %s", exc)
        return ""

    if not rows:
        return ""

    out = ["รายการชุดข้อมูลที่มีจริง (เลือกได้เฉพาะ ID ที่อยู่ในรายการนี้):"]
    for r in rows:
        bits = []
        if r["year_min"]:
            bits.append(f"ปี {r['year_min']}-{r['year_max']}")
        if r["n_prov"]:
            bits.append(f"{r['n_prov']} จว.")
        if r["granularity"]:
            bits.append(f"ระดับ{r['granularity']}")
        if r["n_cav"]:
            # ติดธงไว้ให้เห็นตั้งแต่ตอนเลือก จะได้เลี่ยงไฟล์ที่มีกับดักถ้ามีตัวเลือกอื่น
            bits.append(f"⚠️{r['n_cav']}")
        name = (r["indicator_th"] or r["vault_path"] or "").strip()[:90]
        out.append(f"[ID:{r['file_id']}] {name} ({' · '.join(bits)})")
    return "\n".join(out)


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

    # ── กฎการเลือกคอลัมน์ ────────────────────────────────────────────────
    # เจอจริง 2026-08-03: ถาม "ผู้ป่วยความดันควบคุมได้ดี" แล้ว AI หยิบคอลัมน์
    # `ได้รับการตรวจวัดความดัน` (role=measure) มาเป็นตัวตั้งแทน `ควบคุมได้ตามเกณฑ์`
    # (role=numerator) ⇒ ตอบ 95.27% แทนที่จะเป็น 52.87% แล้วยังชมว่า "สูงกว่าเป้าหมาย"
    # ตัวเลขจริงทุกตัว แต่**หยิบผิดคอลัมน์** — พจนานุกรมรู้ถูกอยู่แล้วแต่ไม่ได้บอกเป็นกฎ
    pct = [c["name"] for c in cols if c.get("role") == "percentage"]
    if pct:
        lines.append(
            "⚠️ ไฟล์นี้**มีคอลัมน์ร้อยละคำนวณไว้ให้แล้ว**: " + ", ".join(pct[:8])
        )
        lines.append(
            "   ⇒ ถ้าคำถามถามหาร้อยละ **ให้อ่านค่าจากคอลัมน์นี้ตรง ๆ ห้ามหารเอง**"
        )

    # ── จับคู่ A/B กับชื่อคอลัมน์จริง ────────────────────────────────────
    # เดิมบอกแค่ "ตัวตั้ง (A) = จำนวนผู้ป่วยที่ได้รับการวินิจฉัยและรักษา" (นิยาม)
    # แต่ **ไม่บอกว่า A คือคอลัมน์ไหน** ⇒ AI ต้องเดาเอง และเดาผิด
    # เจอจริง 2026-08-03: เดาว่า A = `pop` (ประชากร) แทน `result1` ⇒ ได้ 3703%
    num = [c["name"] for c in cols if c.get("role") == "numerator"]
    den = [c["name"] for c in cols if c.get("role") == "denominator"]
    if num and den:
        lines.append(
            f"✅ **สูตรที่ต้องใช้: ({num[0]}) ÷ ({den[0]}) × 100** "
            f"— ตัวตั้งคือ `{num[0]}` ตัวหารคือ `{den[0]}` ห้ามใช้คอลัมน์อื่นแทน"
        )

    pops = [c["name"] for c in cols if c.get("role") == "population"]
    if pops:
        lines.append(
            "🚫 คอลัมน์ต่อไปนี้เป็น **ฐานประชากร ไม่ใช่จำนวนผู้ป่วย** "
            "ห้ามใช้เป็นตัวตั้งหรือตัวหารของร้อยละเด็ดขาด: " + ", ".join(pops[:8])
        )
        lines.append(
            "   (ใช้ผิดแล้วร้อยละจะพุ่งเกิน 100 ซึ่งเป็นไปไม่ได้ — เคยได้ 3703% มาแล้ว)"
        )

    measures = [c["name"] for c in cols if c.get("role") == "measure"]
    if measures:
        # ต้องบอกชื่อออกมาให้ครบ เดิมกรองทิ้งตั้งแต่ต้น AI จึงไม่รู้ว่าห้ามใช้
        lines.append(
            "⚠️ คอลัมน์ต่อไปนี้เป็น **ค่าวัดกลางทาง ห้ามใช้เป็นตัวตั้งของร้อยละ**: "
            + ", ".join(measures[:12])
        )
        lines.append(
            "   (เช่น 'ได้รับการตรวจ...' คือจำนวนคนที่*ถูกตรวจ* ไม่ใช่คนที่*ผ่านเกณฑ์*)"
        )

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
