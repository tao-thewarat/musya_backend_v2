"""ดึงข้อมูลสถิติจาก MoPH Open Data API แล้วแปลงเป็น CSV เข้าคลังเหมือนไฟล์อัปโหลด

ยืนยันด้วยการเรียกจริง 2026-07-31:
  - ไม่ต้องล็อกอิน ไม่ต้องมี API key · ปรับปรุงรายวัน
  - ดึง "ทั้งประเทศ" 985 แถวใน 0.5 วิ ⇒ ถูกกว่าวนดึงทีละจังหวัด 5 ครั้ง
  - report_year ประกาศ 15 ปี แต่ดึงจริงได้ 10 ปี (2555–2559 คืน HTTP 400)
    ⇒ **ห้ามเชื่อรายการปีที่ประกาศ ต้องลองดึงจริงแล้วบันทึกว่าปีไหนใช้ได้**
  - date_com ต่างกันรายจังหวัด (แต่ละจังหวัดประมวลผลคนละเวลา)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
import urllib.error
import urllib.request

from src.tools.amphoe_zone10 import amphoe_name, unknown_codes

logger = logging.getLogger(__name__)

BASE = "https://opendata.moph.go.th/api"
HDRS = {"Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "MUSYA/1.0 (+health-region-10)"}

ZONE10 = {"34": "อุบลราชธานี", "33": "ศรีสะเกษ", "35": "ยโสธร",
          "37": "อำนาจเจริญ", "49": "มุกดาหาร"}

# คอลัมน์ที่ทุกตารางมีเหมือนกัน — ที่เหลือคือคอลัมน์ตัวชี้วัดของตารางนั้น
COMMON_COLS = ("id", "hospcode", "areacode", "date_com", "b_year")

# เป็นบริการสาธารณะที่ไม่ประกาศ rate limit — หน่วงเองเพื่อไม่เบียดเบียนทรัพยากรส่วนรวม
_DELAY = 0.3


# ── สถานะที่ "ลองใหม่แล้วมีโอกาสสำเร็จ" ──────────────────────────────────────
# วัดจริง 2026-08-01: ยิงหมวดสาขาไต 6 ครั้งติด สำเร็จ 2–3 ครั้ง ที่เหลือได้ 403
# ทั้งที่ใช้ URL เดิม พารามิเตอร์เดิม และเปลี่ยน User-Agent เป็นเบราว์เซอร์ก็ไม่ช่วย
# ⇒ เป็น rate-limit/bot-protection ของ Cloudflare ที่กันแบบสุ่ม ไม่ใช่ "เราไม่มีสิทธิ์"
#
# ⚠️ 403 ปกติแปลว่า "ไม่มีสิทธิ์" ซึ่งลองใหม่ไปก็เท่านั้น — แต่กับต้นทางนี้ตรงกันข้าม
# ถ้าไม่นับเป็น retryable ผู้ใช้จะเจอ "โหลดไม่สำเร็จ" ราวครึ่งหนึ่งของการกดทุกครั้ง
_RETRYABLE_HTTP = {403, 408, 425, 429, 500, 502, 503, 504}

# ถอยแบบทวีคูณ รวม ~31 วินาที — ของเดิมรอรวมแค่ 4.5 วิ ซึ่งสั้นกว่าช่วงที่ต้นทางล่มจริง
# จึงใช้ retry ครบทั้ง 3 ครั้งไปโดยที่ต้นทางยังไม่ทันฟื้น แล้วรายงานว่าล้มเหลว
_BACKOFF = (1, 2, 4, 8, 16)


def is_retryable(exc: BaseException) -> bool:
    """แยก "ล่มชั่วคราว" ออกจาก "ขอผิด/ไม่มีของ" — ตัวหลังลองใหม่กี่ครั้งก็ไม่ได้"""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_HTTP
    # timeout / connection reset / DNS ชั่วคราว — ลองใหม่ได้หมด
    return not isinstance(exc, (ValueError, TypeError, KeyError))


def _req(url: str, payload: dict | None = None, tries: int = 5):
    last: BaseException | None = None
    for i in range(tries):
        try:
            data = json.dumps(payload).encode() if payload else None
            req = urllib.request.Request(url, data=data, headers=HDRS)
            with urllib.request.urlopen(req, timeout=90) as r:
                if i:
                    logger.info("สำเร็จหลังลองใหม่ครั้งที่ %s: %s", i + 1, url)
                return json.loads(r.read().decode())
        except Exception as exc:
            last = exc
            if not is_retryable(exc) or i == tries - 1:
                raise
            wait = _BACKOFF[min(i, len(_BACKOFF) - 1)]
            logger.warning("ต้นทางล่มชั่วคราว (%s) — รอ %ss แล้วลองใหม่ [%s/%s] %s",
                           type(exc).__name__, wait, i + 2, tries, url)
            time.sleep(wait)
    if last:
        raise last
    return []


APP = "https://opendata.moph.go.th"

# .../summary-table/{catalogId}/{tableName}/{reportId} — มีชื่อตารางอยู่ใน URL เลย
_SUMMARY_RE = re.compile(r"/summary-table/[^/]+/([a-z0-9_]+)")
# .../standard-report-detail/{reportId} — หน้า metadata ของ HDC มีแต่ report id
# ต้องยิงถามต้นทางว่า id นี้คือตารางไหน
_DETAIL_RE = re.compile(r"/standard-report-detail/([a-f0-9]{16,40})")


def lookup_by_report_id(report_id: str) -> dict:
    """แปลง reportId จากหน้า HDC → ชื่อตาราง + ชื่อไทย + หมวด

    เป็นคนละ API กับ `/api/report_*` (อยู่ที่ `/opendata_api/`) และ **ต้องส่ง
    User-Agent ที่ไม่ว่าง** ไม่งั้น Cloudflare ตอบหน้า challenge แทน JSON
    (ส่ง UA อะไรก็ได้ ไม่จำเป็นต้องปลอมเป็นเบราว์เซอร์ — `HDRS` ที่ใช้อยู่ผ่าน)
    """
    d = _req(f"{APP}/opendata_api/reports/by-report-id/{report_id}")
    if not isinstance(d, dict) or not d.get("source_table"):
        return {}
    return {
        "table": d["source_table"],
        "title_th": clean_title(d.get("report_name") or ""),
        "category": (d.get("category_name") or "").strip(),
        "report_id": report_id,
    }


def resolve_source(text: str) -> dict:
    """รับได้ 3 แบบ: ชื่อตารางตรง ๆ · URL summary-table · URL standard-report-detail

    คืน `{table, title_th, category, report_id, report_url, via}`
    (`via` บอกว่าแกะมาได้ยังไง — เอาไว้แสดงให้ผู้ใช้เห็นว่าระบบเข้าใจถูกไหม)
    """
    t = (text or "").strip()
    if not t:
        return {}

    if "://" not in t:
        return {"table": t, "title_th": "", "category": "", "report_id": "",
                "report_url": "", "via": "ชื่อตาราง"}

    m = _DETAIL_RE.search(t)
    if m:
        info = lookup_by_report_id(m.group(1))
        if info:
            # เก็บ URL ที่ผู้ใช้วางมาไว้ตรง ๆ เพราะเป็นหน้าที่มีนิยามตัวชี้วัดให้คนอ่าน
            return {**info, "report_url": t, "via": "หน้า metadata ของ HDC"}
        return {}

    m = _SUMMARY_RE.search(t)
    if m:
        rid = t.rstrip("/").split("/")[-1].split("?")[0]
        info = lookup_by_report_id(rid) if re.fullmatch(r"[a-f0-9]{16,40}", rid) else {}
        return {"table": m.group(1), "title_th": info.get("title_th", ""),
                "category": info.get("category", ""), "report_id": rid,
                "report_url": t, "via": "URL ตารางสรุป"}
    return {}


def parse_table_name(text: str) -> str:
    """คงไว้เพื่อความเข้ากันได้ — คืนเฉพาะชื่อตาราง"""
    return resolve_source(text).get("table", "")


# .../standard-subcatalog/{catId} หรือ ...?subcatalogId={catId}
_SUBCAT_RE = re.compile(r"/standard-subcatalog/([a-f0-9]{16,40})")
_SUBCAT_Q_RE = re.compile(r"[?&]subcatalogId=([a-f0-9]{16,40})")


def parse_subcatalog_id(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    for rx in (_SUBCAT_RE, _SUBCAT_Q_RE):
        m = rx.search(t)
        if m:
            return m.group(1)
    return t if re.fullmatch(r"[a-f0-9]{16,40}", t) else ""


def list_subcatalog(cat_id: str) -> list[dict]:
    """รายงานทั้งหมดในหมวดหนึ่ง — ใช้แทนการเดาคำค้น

    ดีกว่า `/api/report_name/...` ตรงที่เป็นสารบัญจริงของต้นทาง จึงไม่มีทางจับคู่ผิด
    (API ค้นหาให้คะแนนตามคำที่ตรงกัน เคยจับ "เบาหวานระยะสงบ" ไปคู่กับ
    "ซึมเศร้าหายทุเลา" เพราะคำว่า Remission เหมือนกันคำเดียว)

    `opendata_id` คือ id ที่อยู่ใน URL หน้า metadata ของ HDC
    (`.../standard-report-detail/{opendata_id}`) จึงคืนลิงก์ให้เลย

    ต้นทางมีรายการซ้ำ (ตารางเดียวโผล่หลาย `report_id`) — ยุบให้เหลือตารางละรายการ
    """
    rows = _req(f"{APP}/opendata_api/reports/categorie/by-category-id/{cat_id}")
    if not isinstance(rows, list):
        return []

    out: dict[str, dict] = {}
    for r in rows:
        tbl = r.get("source_table")
        if not tbl or tbl in out:
            continue
        oid = r.get("opendata_id") or ""
        out[tbl] = {
            "table": tbl,
            "title_th": clean_title(r.get("report_name") or ""),
            "category": (r.get("category_name") or "").strip(),
            "report_id": oid,
            "report_url": (
                f"https://hdc.moph.go.th/center/public/standard-report-detail/"
                f"{oid}?subcatalogId={cat_id}" if oid else ""
            ),
        }
    return sorted(out.values(), key=lambda d: d["title_th"])


# API ของ HDC เอง (คนละตัวกับ opendata) — หน้า standard-report-detail ใช้ตัวนี้
HDC_API = "https://api-center-hdc.moph.go.th/v1"


def clean_html(v: str) -> str:
    """ล้าง HTML จากข้อความของ HDC — ใช้ได้ทั้งชื่อตัวชี้วัดและหมายเหตุ

    `<br>` คือตัวแบ่งบรรทัดที่มีความหมาย ห้ามลบทิ้งเฉย ๆ เพราะนิยามเป็นรายการ
    ข้อ 1/2/3/4 ถ้าเชื่อมติดกันจะอ่านไม่รู้เรื่อง

    ⚠️ ห้ามใช้ `<[^>]+>` ลบแท็ก — หมายเหตุของ HDC เต็มไปด้วยเครื่องหมาย
    น้อยกว่า/มากกว่าที่เป็น **เกณฑ์จริง** เช่น

        - ปกติ (Risk = 0) หมายถึง ระดับน้ำตาล >=70 ถึง < 100 mg%
        - เสี่ยง (Risk = 1) หมายถึง ระดับน้ำตาล => 100 ถึง < 126 mg%

    pattern นั้นจะจับตั้งแต่ `< 100 mg% … =` (ไปจบที่ `>` ของ `=>` บรรทัดถัดไป)
    แล้วลบทิ้งทั้งก้อน ⇒ **บรรทัด "เสี่ยง" หายไปทั้งบรรทัด**

    จึงบังคับว่าตัวถัดจาก `<` ต้องเป็นตัวอักษรหรือ `/` เท่านั้นถึงจะนับเป็นแท็ก
    """
    s = re.sub(r"<br\s*/?>", "\n", v or "", flags=re.I)
    s = re.sub(r"</?[A-Za-z][^<>]*>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def clean_title(v: str) -> str:
    """ล้าง HTML แล้วยุบเป็นบรรทัดเดียว — สำหรับชื่อที่จะกลายเป็นชื่อโฟลเดอร์

    ต้นทางใส่ `<font color=red>` ในชื่อรายงานเพื่อเน้นคำ ถ้าไม่ล้างจะได้โฟลเดอร์ชื่อ
    `ประชากร<font color=red>ทะเบียนราษฏร์< font> ย้อนหลัง 3 ปี` ซึ่งอ่านไม่รู้เรื่อง
    และทำให้ AI จับคู่ชื่อโฟลเดอร์กับคำถามไม่ได้ (เจอจริง 2 ส.ค. 2569)
    """
    return re.sub(r"\s+", " ", clean_html(v)).strip()


def get_report_notice(report_code: str, byear: str = "") -> dict:
    """นิยามเชิงปฏิบัติการของตัวชี้วัด — **ไม่มีใน opendata API เลย**

    `report_schema` ให้แค่คำอธิบายคอลัมน์สั้น ๆ ("จำนวนผู้ป่วย (B1)") แต่หน้า HDC
    มีนิยามจริงที่บอกว่านับใครบ้าง: รหัสโรค ICD ที่รวม/ตัดออก · รหัส LAB ที่ต้องมี ·
    เกณฑ์ตัดค่า เช่น "UPCR > 150 mg/g หรือ eGFR < 60"

    ถ้าไม่มีข้อมูลชุดนี้ AI จะตอบได้แค่ตัวเลข แต่ตอบไม่ได้ว่า "ตัวเลขนี้นับใคร"
    ซึ่งเป็นคำถามแรกที่คนทำนโยบายถามเสมอ

    คืน {} เมื่อดึงไม่ได้ — เป็นข้อมูลเสริม ห้ามทำให้การนำเข้าล้ม
    """
    if not report_code:
        return {}
    # ⚠️ ไม่ส่ง byear แล้วต้นทางคืน HTTP 500 (ไม่ใช่ 400) ⇒ ต้องหาปีมาก่อนเสมอ
    if not byear:
        byear = next(iter(get_report_info(report_code).get("hdc_years") or []), "")
        if not byear:
            return {}
    try:
        d = _req(f"{HDC_API}/report-public/detail?reportCode={report_code}&byear={byear}")
    except Exception as exc:
        logger.warning("ดึงหมายเหตุจาก HDC ไม่สำเร็จ (%s): %s", report_code, exc)
        return {}

    rows = d.get("rows") if isinstance(d, dict) else None
    if not isinstance(rows, dict):
        return {}

    def clean(v: str) -> str:
        """ต้นทางเก็บเป็น HTML — <br> คือตัวแบ่งบรรทัดที่มีความหมาย ห้ามลบทิ้งเฉย ๆ
        เพราะนิยามเป็นรายการข้อ 1/2/3/4 ถ้าเชื่อมติดกันจะอ่านไม่รู้เรื่อง

        ⚠️ ห้ามใช้ `<[^>]+>` ลบแท็ก — หมายเหตุของ HDC เต็มไปด้วยเครื่องหมาย
        น้อยกว่า/มากกว่าที่เป็น **เกณฑ์จริง** เช่น

            - ปกติ (Risk = 0) หมายถึง ระดับน้ำตาล >=70 ถึง < 100 mg%
            - เสี่ยง (Risk = 1) หมายถึง ระดับน้ำตาล => 100 ถึง < 126 mg%

        pattern นั้นจะจับตั้งแต่ `< 100 mg% … =` (ไปจบที่ `>` ของ `=>` บรรทัดถัดไป)
        แล้วลบทิ้งทั้งก้อน ⇒ **บรรทัด "เสี่ยง" หายไปทั้งบรรทัด** เหลือข้อความกำกวมว่า
        "ระดับน้ำตาล >=70 ถึง 100 ถึง = 126 mg%" ซึ่งผิดความหมายโดยสิ้นเชิง

        จึงบังคับว่าตัวถัดจาก `<` ต้องเป็นตัวอักษรหรือ `/` เท่านั้นถึงจะนับเป็นแท็ก
        """
        s = re.sub(r"<br\s*/?>", "\n", v or "", flags=re.I)
        s = re.sub(r"</?[A-Za-z][^<>]*>", "", s)
        s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
        return re.sub(r"\n{3,}", "\n\n", s).strip()

    # `target` = เป้าหมายร้อยละของตัวชี้วัด (เช่น 90.00) — ต้นทางให้มาแต่เดิมไม่ได้เก็บ
    # มีค่ามากเวลาตอบคำถามแนว "ผ่านเกณฑ์ไหม" เพราะ AI จะรู้เกณฑ์โดยไม่ต้องให้คนบอก
    target = str(rows.get("target") or "").strip()
    return {
        "notice": clean(rows.get("notice") or ""),
        "b_name": clean(rows.get("bname") or ""),
        "a_name": clean(rows.get("aname") or ""),
        "target": target if target and target not in ("0", "0.00") else "",
        "years": [str(y) for y in (rows.get("byear_list") or [])],
    }


def get_report_info(report_code: str, cat_id: str = "") -> dict:
    """ข้อมูลหัวรายงานจาก HDC — สำคัญตรง `byear_list`

    ⚠️ รายการปีของ HDC กับของ opendata **ไม่ตรงกัน** เจอจริง: opendata ประกาศ 15 ปี
    แต่ HDC บอก 5 ปี (2565–2569) ⇒ ใช้ตรวจสอบไขว้ว่าปีไหนเชื่อได้จริง
    """
    if not report_code:
        return {}
    url = f"{HDC_API}/report-public/info?reportCode={report_code}"
    if cat_id:
        url += f"&subCatalogId={cat_id}"
    try:
        d = _req(url)
    except Exception:
        return {}
    r = d.get("rows") if isinstance(d, dict) else None
    if not isinstance(r, dict):
        return {}
    return {
        "title_th": clean_title(r.get("report_name") or ""),
        "table": (r.get("source_table") or "").strip(),
        "category": (r.get("category_main_name") or "").strip(),
        "hdc_years": [str(y) for y in (r.get("byear_list") or [])],
    }


def list_categories() -> list[dict]:
    """สารบัญหมวดทั้งหมด — ไว้ให้ผู้ใช้เลือกจากหน้าเว็บโดยไม่ต้องรู้ cat_id"""
    rows = _req(f"{APP}/opendata_api/reports/categories")
    return [{"cat_id": r["cat_id"], "name": (r.get("category_name") or "").strip()}
            for r in rows if isinstance(r, dict) and r.get("cat_id")] if isinstance(rows, list) else []


def get_years(table: str) -> list[str]:
    """ปีที่ API **ประกาศ**ว่ามี — ยังไม่ได้แปลว่าดึงได้จริง"""
    return [d["b_year"] for d in _req(f"{BASE}/report_year/{table}")]


def get_schema(table: str) -> list[dict]:
    """โครงสร้างคอลัมน์พร้อมคำอธิบายภาษาไทยจากต้นทาง

    นี่คือ data dictionary ที่ไม่ต้องเขียนเอง — แก้ปัญหาชื่อคอลัมน์ที่สื่อความหมายไม่ได้
    ที่ต้นตอ (สำหรับข้อมูลที่ดึงผ่าน API เท่านั้น ไฟล์เก่าที่ export มาช่วยไม่ได้)
    """
    return [
        {"name": c["COLUMN_NAME"], "type": c["COLUMN_TYPE"],
         "desc": (c.get("COLUMN_COMMENT") or "").strip()}
        for c in _req(f"{BASE}/report_schema/{table}")
    ]


def preview(table: str) -> dict:
    """ข้อมูลสรุปให้ผู้ใช้ตัดสินใจก่อนกดนำเข้าจริง (ไม่ดึงข้อมูลทั้งหมด)"""
    schema = get_schema(table)
    years = get_years(table)
    metrics = [c for c in schema if c["name"] not in COMMON_COLS]
    sample_rows, sample_year = [], ""
    for y in reversed(years):                     # ลองจากปีล่าสุดถอยหลัง
        try:
            d = _req(f"{BASE}/report_data", {"tableName": table, "year": y, "type": "json"})
            sample_rows = [r for r in d if r["areacode"][:2] in ZONE10][:5]
            sample_year = y
            break
        except Exception:
            continue
    return {
        "table": table,
        "declaredYears": years,
        "sampleYear": sample_year,
        "columns": schema,
        "metrics": [c["name"] for c in metrics],
        "sample": sample_rows,
    }


def fetch_zone10(table: str, years: list[str] | None = None, on_progress=None) -> dict:
    """ดึงข้อมูลเขต 10 ทุกจังหวัด ทุกปีที่ดึงได้จริง

    ทางหลัก: ดึงทั้งประเทศแล้วกรอง (เร็วกว่า + ได้ค่าประเทศไว้เทียบ)
    ทางสำรอง: ถ้าทางหลักพัง ไล่ทีละจังหวัด — ปีเดียวพังต้องไม่ล้มทั้งงาน
    """
    years = years or get_years(table)
    rows, per_year = [], []

    for y in years:
        got, how = [], ""
        try:
            d = _req(f"{BASE}/report_data", {"tableName": table, "year": y, "type": "json"})
            got = [r for r in d if r["areacode"][:2] in ZONE10]
            how = f"ทั้งประเทศ {len(d)} แถว"
        except Exception as exc:
            for pc in ZONE10:
                try:
                    d = _req(f"{BASE}/report_data",
                             {"tableName": table, "year": y, "province": pc, "type": "json"})
                    # ⚠️ ต้องกรองเขต 10 ในทางสำรองด้วย — บางตารางไม่สนใจพารามิเตอร์
                    # `province` แล้วคืนมาทั้งประเทศ (เจอจริงกับ `s_new_ckd5`)
                    # ถ้าไม่กรอง ข้อมูลจังหวัดอื่นจะปนเข้าคลัง แล้วพังตอนแปลงรหัส
                    # เป็นชื่อจังหวัดด้วย KeyError — และถ้าบังเอิญไม่พัง ก็ได้ข้อมูลผิด
                    got += [r for r in d if r.get("areacode", "")[:2] in ZONE10]
                except Exception:
                    pass
                time.sleep(_DELAY)
            how = f"สำรองรายจังหวัด (ทางหลักพัง {type(exc).__name__})"

        per = {c: len([r for r in got if r["areacode"][:2] == c]) for c in ZONE10}
        missing = [ZONE10[c] for c, n in per.items() if n == 0]
        rows += got
        per_year.append({"year": y, "rows": len(got), "byProvince": per,
                         "missing": missing, "how": how,
                         "ok": bool(got) and not missing})
        if on_progress:
            on_progress(y, len(got), missing)
        time.sleep(_DELAY)

    usable = [p["year"] for p in per_year if p["ok"]]
    return {"table": table, "rows": rows, "perYear": per_year, "usableYears": usable}


def to_csv(table: str, rows: list[dict], schema: list[dict]) -> bytes:
    """แปลงเป็น CSV รูปแบบเดียวกับไฟล์ในคลัง เพื่อให้ CSV pipeline ใช้ได้ทันที

    ใส่คอลัมน์ `จังหวัด` / `อำเภอ` / `ปีงบประมาณ` ให้ตรงกับที่ `_detect_geo_keys`
    และ `_detect_year_keys` รู้จัก ไม่งั้น pipeline จะเชื่อมข้อมูลข้ามไฟล์ไม่ได้

    คอลัมน์ `อำเภอ` ต้องเป็น**ชื่อ** ไม่ใช่รหัส เพราะ File Finder / Code Generator
    จับคู่จากคำที่ผู้ใช้พิมพ์ ("อำเภอคำชะอีเป็นยังไง") ถ้าเขียนรหัส "05" ลงไป
    ไฟล์นี้จะไม่มีวันถูกเลือกมาตอบเลย — เก็บรหัสไว้ใน `รหัสอำเภอ` สำหรับ join แทน
    """
    metrics = [c["name"] for c in schema if c["name"] not in COMMON_COLS]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["จังหวัด", "อำเภอ", "ปีงบประมาณ", "hospcode", "areacode", "รหัสอำเภอ",
                *metrics, "รวมทุกตัวชี้วัด", "date_com"])
    for r in rows:
        a = r["areacode"]
        vals = [r.get(m) for m in metrics]
        nums = [v for v in vals if isinstance(v, (int, float))]
        w.writerow([ZONE10.get(a[:2], a[:2]), amphoe_name(a), r["b_year"], r["hospcode"], a,
                    a[2:4],
                    *[v if v is not None else "" for v in vals],
                    sum(nums) if nums else "", r["date_com"]])

    unknown = unknown_codes(rows)
    if unknown:
        # ต้นทางเพิ่มอำเภอใหม่ = ตาราง AMPHOE ตกยุค ต้องมีคนรู้ ไม่ใช่เงียบไป
        logger.warning("ตาราง %s มีรหัสอำเภอที่ยังไม่รู้จัก %s — โปรดเพิ่มใน amphoe_zone10.AMPHOE",
                       table, unknown)
    return buf.getvalue().encode("utf-8-sig")
