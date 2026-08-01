"""PDF Ingest Router — แปลง PDF → Markdown chunks → บันทึกใน PostgreSQL (obsidian_notes).
จัดระเบียบตามโครงสร้าง เขต10 / จังหวัด / อำเภอ ของสาธารณสุขเขต 10.
หมายเหตุ (028): MD chunks ถูกเก็บใน database โดยตรง (ไม่ใช่ filesystem)
"""
import hashlib as _hashlib
import io
import threading as _threading
import json
import logging
import os
import re
import time
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from concurrent.futures import ThreadPoolExecutor, as_completed

import pdfplumber
from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from fastapi.responses import StreamingResponse
from google import genai
from google.genai import types

from src.config import get_settings
from src.tools.minio import _get_client, _bucket
from src.db.pool import execute_db, query_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pdf", tags=["PDF Ingest"])

# ── Job / batch store ─────────────────────────────────────────────────────────
# dict ในหน่วยความจำเป็น "เจ้าของ" สถานะ (worker ที่รัน ingest เขียนที่นี่)
_jobs: dict[str, dict] = {}
# คิว ingest หลายไฟล์ — เก็บฝั่งเซิร์ฟเวอร์ให้เดินต่อได้แม้ผู้ใช้ปิดแท็บ
_batches: dict[str, dict] = {}

# จำนวนบรรทัด log ต่อไฟล์ที่ส่งกลับใน batch status — กันคิวไฟล์ใหญ่ทำ payload บวม
# เอกสาร 400 หน้า = 20 chunk ยังไม่ถึงเพดาน ปกติจึงได้ครบทุกบรรทัด
_MAX_ITEM_LOGS = 120

# uvicorn รันหลาย worker (--workers 4) แต่ละ process มี dict ของตัวเอง
# ⇒ POST ลงไปที่ worker A แต่ GET status เด้งไป worker B แล้ว 404
# แก้โดย "มิเรอร์" สถานะขึ้น Redis ทุกครั้งที่เปลี่ยน แล้วให้ฝั่งอ่านตกไปหา Redis
# ถ้าไม่เจอใน dict ตัวเอง — ถ้าไม่มี Redis ก็ยังทำงานได้เหมือนเดิม (fallback เงียบ)
_STATE_TTL = 24 * 3600
_redis_client = None
_redis_down = False


def _redis():
    """คืน Redis client หรือ None ถ้าต่อไม่ได้ (ไม่ให้ ingest ล้มเพราะ Redis)"""
    global _redis_client, _redis_down
    if _redis_down:
        return None
    if _redis_client is None:
        try:
            import redis as redis_lib
            _redis_client = redis_lib.from_url(
                get_settings().REDIS_URL, decode_responses=True,
                socket_connect_timeout=2, socket_timeout=2,
            )
            _redis_client.ping()
        except Exception as exc:
            logger.warning("Redis ใช้ไม่ได้ ใช้ state ในหน่วยความจำอย่างเดียว: %s", exc)
            _redis_client, _redis_down = None, True
            return None
    return _redis_client


def _publish(kind: str, key: str, value: dict) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.setex(f"pdf_ingest:{kind}:{key}", _STATE_TTL, json.dumps(value, default=str))
    except Exception as exc:
        logger.debug("มิเรอร์ %s %s ขึ้น Redis ไม่สำเร็จ: %s", kind, key, exc)


def _read_shared(kind: str, key: str) -> dict | None:
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(f"pdf_ingest:{kind}:{key}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _get_job(job_id: str) -> dict | None:
    return _jobs.get(job_id) or _read_shared("job", job_id)


def _get_batch(batch_id: str) -> dict | None:
    return _batches.get(batch_id) or _read_shared("batch", batch_id)


def _request_cancel(batch_id: str) -> None:
    """ตั้งธงยกเลิก — ต้องผ่าน Redis เพราะคนกดยกเลิกอาจไม่ได้อยู่ worker ที่ถือคิว"""
    if batch_id in _batches:
        _batches[batch_id]["cancel_requested"] = True
        _publish("batch", batch_id, _batches[batch_id])
    r = _redis()
    if r is not None:
        try:
            r.setex(f"pdf_ingest:cancel:{batch_id}", _STATE_TTL, "1")
        except Exception:
            pass


def _cancel_requested(batch: dict) -> bool:
    if batch.get("cancel_requested"):
        return True
    r = _redis()
    if r is None:
        return False
    try:
        return bool(r.get(f"pdf_ingest:cancel:{batch['batch_id']}"))
    except Exception:
        return False


def _pdf_bucket() -> str:
    return get_settings().PDF_BUCKET


def _ensure_pdf_bucket() -> None:
    """Create the PDF bucket if it doesn't exist."""
    client = _get_client()
    bucket = _pdf_bucket()
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info(f"Created PDF bucket: {bucket}")
    except Exception as e:
        logger.warning(f"Could not ensure PDF bucket: {e}")



# ── Zone 10 Geographic Structure ─────────────────────────────────────────────
ZONE10_PROVINCES: dict[str, list[str]] = {
    "ส่วนกลาง": [],
    "อุบลราชธานี": [
        "เมืองอุบลราชธานี", "ศรีเมืองใหม่", "โขงเจียม", "เขื่องใน", "เขมราฐ",
        "เดชอุดม", "นาจะหลวย", "น้ำยืน", "บุณฑริก", "ตระการพืชผล",
        "กุดข้าวปุ้น", "ม่วงสามสิบ", "วารินชำราบ", "พิบูลมังสาหาร", "ตาลสุม",
        "โพธิ์ไทร", "สำโรง", "ดอนมดแดง", "สิรินธร", "ทุ่งศรีอุดม",
        "นาเยีย", "นาตาล", "เหล่าเสือโก้ก", "สว่างวีระวงศ์", "น้ำขุ่น",
    ],
    "ศรีสะเกษ": [
        "เมืองศรีสะเกษ", "ยางชุมน้อย", "กันทรารมย์", "กันทรลักษ์", "ขุขันธ์",
        "ไพรบึง", "ปรางค์กู่", "ขุนหาญ", "ราษีไศล", "อุทุมพรพิสัย",
        "บึงบูรพ์", "ห้วยทับทัน", "โนนคูณ", "ศรีรัตนะ", "น้ำเกลี้ยง",
        "วังหิน", "ภูสิงห์", "เมืองจันทร์", "เบญจลักษ์", "พยุห์",
        "โพธิ์ศรีสุวรรณ", "ศิลาลาด",
    ],
    "ยโสธร": [
        "เมืองยโสธร", "ทรายมูล", "กุดชุม", "คำเขื่อนแก้ว", "ป่าติ้ว",
        "มหาชนะชัย", "ค้อวัง", "เลิงนกทา", "ไทยเจริญ",
    ],
    "อำนาจเจริญ": [
        "เมืองอำนาจเจริญ", "ชานุมาน", "ปทุมราชวงศา", "พนา",
        "เสนางคนิคม", "หัวตะพาน", "ลืออำนาจ",
    ],
    "มุกดาหาร": [
        "เมืองมุกดาหาร", "นิคมคำสร้อย", "ดอนตาล", "ดงหลวง",
        "คำชะอี", "หว้านใหญ่", "หนองสูง",
    ],
}

# Alias map for fuzzy matching (common abbreviations / alternative spellings)
_PROVINCE_ALIASES: dict[str, str] = {
    "อุบล": "อุบลราชธานี",
    "อุบลฯ": "อุบลราชธานี",
    "ศรีสะเกษ": "ศรีสะเกษ",
    "ศรีสะเกษ": "ศรีสะเกษ",
    "ยโสธร": "ยโสธร",
    "อำนาจ": "อำนาจเจริญ",
    "มุกดา": "มุกดาหาร",
    "มุกดาหาร": "มุกดาหาร",
}


# ── Gemini client ─────────────────────────────────────────────────────────────
def _get_gemini_client():
    s = get_settings()
    return genai.Client(api_key=s.GEMINI_API_KEY)


def _gemini_model() -> str:
    return get_settings().GEMINI_MODEL_PRO  # gemini-2.5-pro (markdown conversion)


def _gemini_model_fast() -> str:
    return get_settings().GEMINI_MODEL      # gemini-2.0-flash (filename / location)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize_filename(name: str) -> str:
    """ลบอักขระพิเศษที่ไม่เหมาะกับชื่อไฟล์ แต่รักษา Thai/Unicode"""
    name = re.sub(r'[\\/:*?"<>|]', '-', name)
    name = name.strip('. ')
    return name[:120] if len(name) > 120 else name


def _find_duplicate_object(client_minio, bucket: str, size: int, digest: str) -> str | None:
    """หา object ใน bucket ที่ "เนื้อหาตรงกันทุกไบต์" — คืน object_name หรือ None

    กรองด้วยขนาดก่อนแล้วค่อย hash เฉพาะตัวที่ขนาดชน (bucket มีเป็นร้อย object
    การ hash ทุกตัวทุกครั้งที่อัปโหลดจะช้าเกินรับได้)
    """
    for obj in client_minio.list_objects(bucket, recursive=True):
        if obj.size != size:
            continue
        try:
            r = client_minio.get_object(bucket, obj.object_name)
            try:
                h = _hashlib.sha256()
                while chunk := r.read(1 << 20):
                    h.update(chunk)
            finally:
                r.close(); r.release_conn()
            if h.hexdigest() == digest:
                return obj.object_name
        except Exception:
            continue
    return None


# ── PDF → รูปภาพ (ทางเลือกแทนการอัป PDF ทั้งไฟล์ให้ Gemini) ───────────────────
#
# Gemini ตอบ 400 INVALID_ARGUMENT เมื่อ PDF ใหญ่มาก (วัดจริง: 92–95 MB พัง, 23 MB ผ่าน)
# ซึ่งเจอบ่อยกับ "สไลด์นำเสนอ" ที่หน้าน้อยแต่ฝังรูปความละเอียดสูง
# เรนเดอร์เฉพาะหน้าที่ต้องใช้เป็น JPEG แล้วส่งภาพแทน ลดขนาดจาก 92 MB เหลือ ~1.9 MB
# และได้เนื้อหามากกว่าเดิม ~19 เท่า เพราะโมเดลอ่านแผนภาพในสไลด์ออกด้วย
_RENDER_DPI = 150
_JPEG_QUALITY = 80
# จำกัดด้านยาวของภาพ — สไลด์ 16:9 ที่ 150 DPI ได้ 3000×1688 px (5 ล้านพิกเซล)
# ซึ่งเกินที่โมเดลใช้จริง (ย่อยเป็น tile ~768 px อยู่แล้ว) แต่กินแรมตอนเรนเดอร์
# ~370 MB/หน้า ตัดเหลือ 1600 px ลดแรมและขนาด JPEG ลงราว 3 เท่าโดยไม่เสียรายละเอียด
_MAX_RENDER_PX = 1600
# เรนเดอร์ทีละหน้าทั้งโปรเซส — chunk หลายตัวรันขนานกันอยู่แล้ว ถ้าปล่อยให้เรนเดอร์
# พร้อมกันด้วยจะกินแรมทวีคูณ ส่วนที่ช้าจริงคือการรอ LLM ไม่ใช่การเรนเดอร์
_render_lock = _threading.Lock()
# เกินขนาดนี้ให้ข้ามการอัป PDF ไปใช้ภาพเลย ไม่ต้องรอพังก่อน
_PDF_SIZE_LIMIT_MB = 40
# สไลด์เนื้อหาแน่นกว่าเอกสารต่อหนึ่งหน้า แบ่ง chunk ให้เล็กลงไม่งั้นโดน max_output_tokens ตัด
_SLIDE_CHUNK_PAGES = 6
# หน้าแนวนอนที่ข้อความไม่แน่นเกินค่านี้ถือเป็นสไลด์ (สไลด์จริงวัดได้ 951 ตัวอักษร/หน้า)
_SLIDE_MAX_CHARS_PER_PAGE = 2000


def _render_page_images(
    file_bytes: bytes, page_start: int, page_end: int, dpi: int = _RENDER_DPI,
) -> list[bytes]:
    """เรนเดอร์หน้า page_start..page_end (นับจาก 1) เป็น JPEG

    ใช้ pypdfium2 ตัวเดียวกับที่ pdfplumber พึ่งพาอยู่แล้ว ไม่ต้องเพิ่ม dependency
    """
    import pypdfium2 as pdfium

    out: list[bytes] = []
    with _render_lock:
        doc = pdfium.PdfDocument(io.BytesIO(file_bytes))
        try:
            last = min(page_end, len(doc))
            for i in range(page_start - 1, last):
                page = doc[i]
                w_pt, h_pt = page.get_size()
                scale = dpi / 72
                # อย่าให้ด้านยาวเกิน _MAX_RENDER_PX (สไลด์แผ่นใหญ่จะพุ่งไปหลายพันพิกเซล)
                longest = max(w_pt, h_pt) * scale
                if longest > _MAX_RENDER_PX:
                    scale *= _MAX_RENDER_PX / longest
                pil = page.render(scale=scale).to_pil().convert("RGB")
                buf = io.BytesIO()
                pil.save(buf, "JPEG", quality=_JPEG_QUALITY)
                out.append(buf.getvalue())
                # คืนแรมทันที ไม่รอ GC — pdfium ค้าง resource ของหน้าไว้
                # ถ้าไม่ปิด แรมจะไต่ขึ้นราว 150 MB ต่อหน้าที่เรนเดอร์
                pil.close()
                page.close()
        finally:
            doc.close()
    return out


# ตัวอักษรเดียวซ้ำติดกันเกินค่านี้ = โมเดลวนลูป ไม่ใช่เนื้อหาจริง
# (เจอจริง: โน้ตหนึ่งได้ 128,067 ตัวอักษรจาก 15 หน้า เพราะพ่นจุดยาวเป็นหมื่นตัว)
_MAX_CHAR_RUN = 80
_REPEAT_RE = re.compile(r"(\S)\1{" + str(_MAX_CHAR_RUN) + r",}")
# ลองไล่ temperature ขึ้นเมื่อผลลัพธ์ใช้ไม่ได้ — ที่ 0.2 โมเดลจะวนลูปเดิมซ้ำทุกครั้ง
_CONVERT_TEMPERATURES = (0.2, 0.5, 0.8)
# วัดจริงกับหน้าสแกน: 15 หน้าต่อครั้งทำให้วนลูปทุกครั้ง แต่ 6 หน้าผ่านสบาย
_SPLIT_MAX_PAGES = 6


def _is_degenerate(text: str) -> bool:
    """เนื้อหาที่โมเดลวนซ้ำจนใช้ไม่ได้ — "ไม่ว่าง" แต่ก็ไม่ใช่เนื้อหา"""
    return bool(_REPEAT_RE.search(text))


def _as_contents(source) -> list:
    """แปลง "แหล่งเนื้อหา" ให้เป็น list ของ content parts

    รับได้ทั้ง uploaded_file (โหมด PDF) และ list ของ JPEG bytes (โหมดภาพ)
    """
    if source is None:
        return []
    if isinstance(source, (list, tuple)):
        return [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in source]
    return [source]


def _looks_like_slides(file_bytes: bytes, pages: list[str]) -> bool:
    """เดาว่าเป็นสไลด์นำเสนอไหม — หน้าแนวนอน + ข้อความต่อหน้าน้อย

    สไลด์ความหมายอยู่ในภาพ/แผนภาพ ไม่ใช่ข้อความ ถ้าใช้พรอมป์เอกสารปกติจะได้แค่
    ข้อความหัวข้อสั้น ๆ ไม่กี่บรรทัด
    """
    if not pages:
        return False
    # อัตราส่วนหน้าเป็นสัญญาณหลัก — สไลด์เป็น 16:9 (1.78) หรือ 4:3 (1.33)
    # ส่วนเอกสารเป็น A4 แนวตั้ง (0.71) วัดจริงกับสไลด์ตัวอย่างได้ 1440×810 pt
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(io.BytesIO(file_bytes))
        try:
            w, h = doc[0].get_size()
        finally:
            doc.close()
    except Exception:
        return False                          # ไฟล์เสีย/ไม่ใช่ PDF → ใช้โหมดเอกสารตามเดิม
    if w <= h * 1.2:
        return False
    # แนวนอนแล้วยังต้องข้อความไม่แน่นเกิน กัน "เอกสาร A4 แนวนอน" ที่เป็นตารางยาว ๆ
    # (สไลด์ตัวอย่างเฉลี่ย 951 ตัวอักษร/หน้า — เกณฑ์ 900 เดิมต่ำไปจนพลาด)
    avg_chars = sum(len(p or "") for p in pages) / len(pages)
    return avg_chars < _SLIDE_MAX_CHARS_PER_PAGE


def _sanitize_folder_path(path: str) -> str:
    """ทำความสะอาด "โฟลเดอร์ซ้อนชั้น" ที่ผู้ใช้พิมพ์เอง เช่น
       "3.1 ข้อมูลอุบัติเหตุทางถนน/R_รายงานอุบัติเหตุ-2567"

    รองรับหลายชั้นเพื่อให้จัดหมวดหมู่ได้เหมือนโครงสร้างเอกสารต้นทางจริง
    (บางจังหวัดมีชั้นหมวดหมู่คั่นก่อนถึงโฟลเดอร์เอกสาร)

    ⚠️ ต้องกัน path traversal และเซกเมนต์ขยะ — ค่านี้มาจากผู้ใช้โดยตรงและถูกเอาไป
    ประกอบเป็น relative_path/note_id ถ้าปล่อย ".." หรือ "/" นำหน้าผ่านไปได้
    จะเขียนโน้ตออกนอกขอบเขต vault ที่ตั้งใจ
    """
    parts: list[str] = []
    for seg in (path or "").replace("\\", "/").split("/"):
        seg = _sanitize_filename(seg.strip())
        if not seg or seg in {".", ".."}:
            continue
        parts.append(seg)
    return "/".join(parts[:4])          # ลึกสุด 4 ชั้น กันโครงสร้างบานปลาย


# ── DB helpers ────────────────────────────────────────────────────────────────

VAULT_ID = "health_region_10"


def _upsert_note(
    note_id: str,
    relative_path: str,
    title: str,
    province: str | None,
    district: str | None,
    note_type: str,
    tags: list[str],
    source_file: str,
    content: str,
    file_id: str | None = None,
    chunk_index: int = 0,
    is_index: bool = False,
    year: int | None = None,
) -> None:
    """UPSERT หนึ่ง note เข้า obsidian_notes."""
    import re as _re
    # strip YAML frontmatter เพื่อเก็บใน content_stripped
    stripped = _re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, count=1, flags=_re.DOTALL).strip()
    execute_db(
        """
        INSERT INTO obsidian_notes
            (note_id, vault_id, relative_path, title, province, district,
             note_type, tags, source_file, year, content, content_stripped,
             file_id, chunk_index, is_index, updated_at, indexed_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        ON CONFLICT (note_id) DO UPDATE SET
            title            = EXCLUDED.title,
            province         = EXCLUDED.province,
            district         = EXCLUDED.district,
            note_type        = EXCLUDED.note_type,
            tags             = EXCLUDED.tags,
            source_file      = EXCLUDED.source_file,
            year             = EXCLUDED.year,
            content          = EXCLUDED.content,
            content_stripped = EXCLUDED.content_stripped,
            file_id          = EXCLUDED.file_id,
            chunk_index      = EXCLUDED.chunk_index,
            is_index         = EXCLUDED.is_index,
            updated_at       = NOW()
        """,
        (
            note_id, VAULT_ID, relative_path, title, province, district,
            note_type, tags, source_file, year, content, stripped,
            file_id, chunk_index, is_index,
        ),
    )


def _extract_pages(file_bytes: bytes) -> list[str]:
    """Extract text from each page of PDF."""
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    return pages


def _chunk_pages(pages: list[str], chunk_size: int) -> list[list[str]]:
    """Split pages into chunks of chunk_size."""
    chunks = []
    for i in range(0, len(pages), chunk_size):
        chunks.append(pages[i: i + chunk_size])
    return chunks


# ── AI: Detect province & district from content ───────────────────────────────

def _build_zone10_list_text() -> str:
    """สร้าง text แสดงรายชื่อจังหวัด/อำเภอทั้งหมดให้ AI ใช้อ้างอิง."""
    lines = ["สาธารณสุขเขต 10 ประกอบด้วย 5 จังหวัด:"]
    for province, districts in ZONE10_PROVINCES.items():
        lines.append(f"\n**{province}**: {', '.join(districts)}")
    return "\n".join(lines)


def _ai_detect_location(client, uploaded_file, sample_text: str, original_pdf_name: str) -> dict:
    """
    ให้ Gemini Pro วิเคราะห์เนื้อหา PDF แล้วระบุจังหวัดและอำเภอในเขต 10.

    Returns:
        {
            "province": "อุบลราชธานี" | None,
            "district": "เมืองอุบลราชธานี" | None,
            "confidence": "high" | "medium" | "low",
            "reason": "เหตุผล..."
        }
    """
    zone_info = _build_zone10_list_text()

    prompt = f"""คุณเป็น AI ผู้เชี่ยวชาญด้านสาธารณสุขเขต 10 ของประเทศไทย มีหน้าที่ระบุว่าเอกสาร PDF นี้เป็นของ "จังหวัดใด" ในเขต 10

{zone_info}

═══════════════════════════════════════
กฎการวิเคราะห์ (ห้ามละเลยข้อใดข้อหนึ่ง)
═══════════════════════════════════════
1. อ่านเนื้อหา PDF ที่แนบมาทุกหน้าก่อนตอบ
2. ค้นหาเบาะแสต่อไปนี้ แล้วให้ confidence ตามนี้:

   ★ confidence="high" — เมื่อพบสิ่งเหล่านี้อย่างน้อย 1 อย่าง:
     • ชื่อจังหวัดปรากฏในเอกสาร (แม้แค่ครั้งเดียว)
     • ชื่ออำเภอใดๆ ของจังหวัดนั้นปรากฏในเอกสาร
     • ชื่อโรงพยาบาล / สสจ. / หน่วยงานสาธารณสุขของจังหวัดนั้น
     • ที่อยู่หรือสถานที่ที่ระบุจังหวัดนั้น

   ★ confidence="medium" — เมื่อมีหลักฐานทางอ้อม:
     • ข้อมูลสถิติของพื้นที่ที่น่าจะเป็นจังหวัดนั้น แต่ไม่ได้ระบุชื่อตรงๆ
     • บริบทของเอกสารที่ชี้ไปยังจังหวัดนั้นโดยรวม

   ★ confidence="low" — เฉพาะเมื่อไม่พบหลักฐานใดๆ เลย

3. ห้ามเดาโดยไม่มีหลักฐาน
4. หากเอกสารเปรียบเทียบทุกจังหวัดหรือเป็นระดับประเทศ → ระบุ "ส่วนกลาง"
5. หากไม่พบหลักฐานของจังหวัดใดเลย → ระบุ "ส่วนกลาง" ห้ามเดาเป็นอุบลราชธานี

═══════════════════════════════════════
ตัวอย่างการวิเคราะห์ที่ถูกต้อง
═══════════════════════════════════════
✅ พบคำว่า "ศรีสะเกษ" หรือ "ขุขันธ์" → province: "ศรีสะเกษ", confidence: "high"
✅ พบ "โรงพยาบาลเลิงนกทา" → province: "ยโสธร", district: "เลิงนกทา", confidence: "high"
✅ พบ "สสจ.มุกดาหาร" → province: "มุกดาหาร", confidence: "high"
✅ พบ "อุบลราชธานี" แค่ 1 ครั้ง → confidence: "high" (พบหลักฐาน)
✅ ข้อมูลเขต 10 ทุกจังหวัด → province: "ส่วนกลาง", confidence: "high"
❌ ห้าม: ระบุ "อุบลราชธานี" โดยไม่มีหลักฐานใดๆ

ตอบในรูปแบบ JSON เท่านั้น:
{{"province":"ชื่อจังหวัด หรือ ส่วนกลาง","district":"ชื่ออำเภอ หรือ null","confidence":"high หรือ medium หรือ low","reason":"อธิบายหลักฐานที่พบ"}}"""

    try:
        response = client.models.generate_content(
            model=_gemini_model(),  # Pro: ความแม่นยำสูงสุดสำหรับ location
            contents=[*_as_contents(uploaded_file), prompt],
            config=types.GenerateContentConfig(
                max_output_tokens=400,
                temperature=0.02,  # ลด temperature ให้ deterministic มากขึ้น
            ),
        )
        raw = response.text.strip()
        logger.info(f"AI location raw response: {raw}")
        # Extract JSON — รองรับ nested objects และ markdown code blocks
        json_match = re.search(r'\{.*?\}', raw.replace('\n', ' '), re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            province = data.get("province", "").strip()
            district = (data.get("district") or "").strip() or None

            # Normalize confidence value
            conf_raw = str(data.get("confidence", "low")).strip().lower()
            if conf_raw in ("high", "สูง", "high confidence"):
                confidence = "high"
            elif conf_raw in ("medium", "กลาง", "ปานกลาง", "medium confidence"):
                confidence = "medium"
            else:
                confidence = "low"

            # "ส่วนกลาง" เป็น valid province
            if province == "ส่วนกลาง":
                return {
                    "province": "ส่วนกลาง",
                    "district": None,
                    "confidence": confidence,
                    "reason": data.get("reason", ""),
                }

            # Validate province is in Zone 10
            if province and province not in ZONE10_PROVINCES:
                province = _PROVINCE_ALIASES.get(province, None)

            # Validate district belongs to province
            if province and district and district not in ZONE10_PROVINCES.get(province, []):
                district = None  # district not valid for this province

            if province:
                return {
                    "province": province,
                    "district": district,
                    "confidence": confidence,
                    "reason": data.get("reason", ""),
                }
    except Exception as e:
        logger.warning(f"AI location detection failed: {e}")

    # Fallback: keyword search in text only (ignoring filename to prevent misclassification)
    combined = sample_text[:3000].lower()
    best_province = None
    best_district = None
    best_count = 0

    for province, districts in ZONE10_PROVINCES.items():
        if province == "ส่วนกลาง":
            continue
        count = combined.count(province.lower()) + combined.count(province[:4].lower())
        if count > best_count:
            best_count = count
            best_province = province
            best_district = None
            for district in districts:
                if district.lower() in combined:
                    best_district = district
                    break

    if best_province and best_count >= 1:  # แค่พบ 1 ครั้งก็ถือว่า medium
        conf = "high" if best_count >= 3 else "medium"
        return {"province": best_province, "district": best_district, "confidence": conf, "reason": f"keyword match (พบ {best_count} ครั้ง)"}

    return {"province": "ส่วนกลาง", "district": None, "confidence": "medium", "reason": "ไม่พบความเชื่อมโยงกับจังหวัดใดในเขต 10"}



def _resolve_rel_path(
    province: str | None,
    district: str | None,
    base_filename: str,
) -> str:
    """
    คำนวณ relative path (ใช้เป็น note_id prefix) ตามโครงสร้างเขต 10.

    - มีจังหวัดเท่านั้น → เขต10/{province}/{base_filename}
    - มีจังหวัด+อำเภอ  → เขต10/{province}/{district}/{base_filename}
    - ไม่มีจังหวัด     → {base_filename}

    base_filename ใส่ "/" ได้เพื่อสร้างชั้นหมวดหมู่ย่อย เช่น
    "3.1 ข้อมูลอุบัติเหตุทางถนน/R_รายงานอุบัติเหตุ-2567" (ดู _sanitize_folder_path)
    """
    safe_name = _sanitize_folder_path(base_filename) or _sanitize_filename(base_filename)
    if province and district:
        return f"เขต10/{province}/{district}/{safe_name}"
    if province:
        return f"เขต10/{province}/{safe_name}"
    return safe_name


def _update_province_index_db(
    province: str,
    district: str | None,
    base_filename: str,
    index_note_id: str,
    total_pages: int,
):
    """อัปเดต INDEX note ของจังหวัด/อำเภอใน DB เพิ่ม link ไปยังเอกสารใหม่."""
    safe_name = _sanitize_filename(base_filename)
    date_str = datetime.now().strftime('%d/%m/%Y')

    if district:
        doc_link = f"{district}/{safe_name}/{safe_name}-INDEX"
    else:
        doc_link = f"{safe_name}/{safe_name}-INDEX"

    entry = f"\n- [[{doc_link}|📄 {base_filename}]] ({total_pages} หน้า · {date_str})"

    try:
        # อัปเดต INDEX note ของจังหวัด
        prov_note_id = f"{VAULT_ID}::เขต10/{province}/INDEX"
        rows = query_db(
            "SELECT content FROM obsidian_notes WHERE note_id = %s",
            (prov_note_id,),
        )
        if rows:
            old_content = rows[0]["content"] or ""
            new_content = old_content + entry
            execute_db(
                "UPDATE obsidian_notes SET content = %s, content_stripped = %s, updated_at = NOW() WHERE note_id = %s",
                (new_content, new_content, prov_note_id),
            )

        # อัปเดต INDEX note ของอำเภอ (ถ้ามี)
        if district:
            dist_note_id = f"{VAULT_ID}::เขต10/{province}/{district}/INDEX"
            rows = query_db(
                "SELECT content FROM obsidian_notes WHERE note_id = %s",
                (dist_note_id,),
            )
            if not rows:
                # สร้าง INDEX note ของอำเภอใหม่
                dist_content = (
                    f"# {district} — {province}\n\n"
                    f"> **เขตสุขภาพที่ 10** · [[{province}/INDEX|← {province}]]\n\n"
                    f"## เอกสาร\n"
                )
                _upsert_note(
                    note_id=dist_note_id,
                    relative_path=f"เขต10/{province}/{district}/INDEX.md",
                    title=f"INDEX — {district}",
                    province=province,
                    district=district,
                    note_type="MOC",
                    tags=["index", province, district],
                    source_file="",
                    content=dist_content,
                    is_index=True,
                )
                rows = [{"content": dist_content}]
            old_content = rows[0]["content"] or ""
            dist_entry = f"\n- [[{safe_name}/{safe_name}-INDEX|📄 {base_filename}]] ({total_pages} หน้า · {date_str})"
            new_content = old_content + dist_entry
            execute_db(
                "UPDATE obsidian_notes SET content = %s, content_stripped = %s, updated_at = NOW() WHERE note_id = %s",
                (new_content, new_content, dist_note_id),
            )
    except Exception as e:
        logger.warning(f"Failed to update province index in DB: {e}")


# ── AI: Generate filename from content ───────────────────────────────────────

def _ai_generate_filename(client, uploaded_file, original_pdf_name: str) -> str:
    """ให้ Gemini วิเคราะห์เนื้อหาและสร้างชื่อโฟลเดอร์ภาษาไทยที่ระบุได้ชัดเจน"""
    prompt = f"""วิเคราะห์ไฟล์ PDF นี้ทั้งหมด แล้วตั้งชื่อโฟลเดอร์สำหรับเอกสารนี้ โดยใช้รูปแบบ:
[prefix]ชื่อเรื่อง-สถานที่-ปี

**prefix** (เลือกอันเดียว):
- R_ = รายงาน / สรุปผลการดำเนินงาน / ผลการประเมิน
- M_ = งานวิจัย / วิทยานิพนธ์ / จะแนะการศึกษา
- F_ = ฟอร์มสำรวจ / สถิติ / ข้อมูลระดับเขต
- P_ = แผนงาน / โครงการ / นโยบาย
- A_ = คู่มือ / แนวทางปฏิบัติ / มาตรฐาน
- I_ = บันทึกการประชุม / รายงานการตรวจราชการ

**ชื่อเรื่อง** (บังคับ):
- ต้องระบุเนื้อหาสำคัญสูงสุดให้ชัดเจน เช่น: พฤติกรรมสุขภาพ-ผู้สูงอายุ, โรคทางเดินอาหาร, อะฟลาทอกสาย
- หามใช้คำกำกวม เช่น: การดำเนินงาน, สาธารณสุข

**สถานที่** (ใส่ถ้าระบุไว้ในเอกสาร): ชื่อจังหวัดหรืออำเภอ เช่น: มุกดาหาร, อุบลราชธานี

**ปี** (ใส่ถ้าพบ): ปี พ.ศ. สั้น เช่น: 2565, 2567

ตัวอย่างชื่อที่ดี:
- R_รายงานตรวจราชการ-มุกดาหาร-2565
- M_พฤติกรรมสุขภาพ-ผู้สูงอายุ-อุบล-2564
- F_สถิติอะฟลาทอกสาย-ศรีสะเกษ
- P_แผนปฏิบัติการ-เขต10-2566

ชื่อไฟล์ต้นฉบับ: {original_pdf_name}
กฎ: ใช้ขีดกลาง (-) แทนช่องว่าง ห้ามไอ้อักษรพิเศษ ความยาว 20-100 ตัวอักษร ตอบเฉพาะชื่อเท่านั้น"""

    try:
        response = client.models.generate_content(
            model=_gemini_model_fast(),
            contents=[*_as_contents(uploaded_file), prompt],
            config=types.GenerateContentConfig(
                max_output_tokens=120,
                temperature=0.2,
            ),
        )
        raw_name = response.text.strip()
        logger.info(f"AI filename raw response: {raw_name}")
        name = raw_name.split('\n')[0].strip()
        name = re.sub(r'[\\/:*?"<>|]', '-', name)
        name = name.strip('- ')
        return name if name else _sanitize_filename(original_pdf_name.replace('.pdf', ''))
    except Exception as e:
        logger.warning(f"AI filename generation failed: {e}")
        base = original_pdf_name.replace('.pdf', '').replace('.PDF', '')
        return _sanitize_filename(base)


def _existing_doc_folders(province: str | None) -> list[str]:
    """โฟลเดอร์เอกสารที่ "มีอยู่จริง" ของจังหวัดนั้น พร้อมจำนวนโน้ต (มากไปน้อย)"""
    if not province:
        return []
    rows = query_db(
        """
        SELECT split_part(relative_path, '/', 3) AS folder, count(*) AS n
        FROM obsidian_notes
        WHERE vault_id = %s AND relative_path LIKE %s
          AND array_length(string_to_array(relative_path, '/'), 1) >= 4
        GROUP BY 1 ORDER BY 2 DESC
        """,
        (VAULT_ID, f"เขต10/{province}/%"),
    )
    return [r["folder"] for r in rows if r["folder"]]


def _ai_suggest_folder_names(client, uploaded_file, original_pdf_name: str,
                             existing: list[str]) -> list[str]:
    """เสนอชื่อโฟลเดอร์ 3 ตัวเลือก โดย **ให้ AI เห็นชื่อที่มีอยู่แล้วในคลัง**

    ทำไมต้องส่งชื่อเดิมไปด้วย: เดิม AI ตั้งชื่อโดยไม่รู้ว่าคลังมีอะไร จึงได้ชื่อที่
    ความหมายเดียวกันแต่สะกดต่างกันเต็มไปหมด (เช่น "R_รายงานตรวจราชการ-ยโสธร-2565"
    กับ "R_รายงานการตรวจราชการ-ยโสธร-2564") ทำให้เอกสารชุดเดียวกันกระจายอยู่คนละโฟลเดอร์
    และจับคู่ source_file ไม่ติดในภายหลัง
    """
    sample = "\n".join(f"- {f}" for f in existing[:40]) or "- (ยังไม่มีเอกสารในจังหวัดนี้)"
    prompt = f"""วิเคราะห์ไฟล์ PDF นี้ แล้วเสนอ "ชื่อโฟลเดอร์เอกสาร" 3 ตัวเลือก

รูปแบบชื่อ: [prefix]ชื่อเรื่อง-สถานที่-ปี
prefix: R_=รายงาน · M_=งานวิจัย · F_=สำรวจ/สถิติ · P_=แผน/นโยบาย · A_=คู่มือ · I_=บันทึกประชุม

**โฟลเดอร์ที่มีอยู่แล้วในจังหวัดนี้:**
{sample}

กติกาสำคัญ:
- ถ้าเอกสารนี้เป็น "ชุดเดียวกัน" กับโฟลเดอร์ที่มีอยู่ (ต่างแค่ปี/รอบ) ให้เสนอชื่อที่
  **สะกดตามแบบเดิมเป๊ะ** เปลี่ยนแค่ปี — อย่าคิดคำใหม่ที่ความหมายเหมือนกัน
- ถ้าเป็นเอกสารที่ควรอยู่โฟลเดอร์เดิมพอดี ให้เสนอชื่อโฟลเดอร์นั้นตรง ๆ ได้เลย
- ใช้ขีดกลางแทนช่องว่าง · ยาว 20-100 ตัวอักษร · ห้ามอักขระพิเศษ

ชื่อไฟล์ต้นฉบับ: {original_pdf_name}

ตอบเป็น JSON array ของสตริง 3 ตัวเท่านั้น ห้ามมีข้อความอื่น เช่น
["R_รายงานตรวจราชการ-ยโสธร-2568","R_เอกสารรับการตรวจราชการ-ยโสธร-2568","I_ตรวจราชการรอบ2-ยโสธร-2568"]"""
    try:
        resp = client.models.generate_content(
            model=_gemini_model_fast(),
            contents=[*_as_contents(uploaded_file), prompt],
            config=types.GenerateContentConfig(max_output_tokens=300, temperature=0.2),
        )
        m = re.search(r"\[.*\]", (resp.text or ""), re.DOTALL)
        if not m:
            return []
        out = []
        for x in json.loads(m.group(0)):
            name = _sanitize_filename(str(x).strip())
            if 5 <= len(name) <= 120 and name not in out:
                out.append(name)
        return out[:3]
    except Exception as e:
        logger.warning(f"AI folder suggestion failed: {e}")
        return []



# ── AI: Convert text chunk to Markdown ───────────────────────────────────────

def _ai_convert_to_markdown(
    client,
    uploaded_file,
    chunk_index: int,
    total_chunks: int,
    base_filename: str,
    page_start: int,
    page_end: int,
    prev_link: str | None,
    next_link: str | None,
    province: str | None,
    district: str | None,
    location_confidence: str,
    page_images: list[bytes] | None = None,
    is_slides: bool = False,
) -> str:
    """แปลงเนื้อหา 1 chunk เป็น Markdown พร้อม frontmatter และ wikilinks

    page_images ถ้าส่งมา จะใช้ "ภาพของหน้าที่ต้องการ" แทนการอ้าง PDF ทั้งไฟล์
    (จำเป็นกับไฟล์ใหญ่/สไลด์ ที่ Gemini ปฏิเสธ PDF ทั้งก้อนด้วย 400)

    ⚠️ แปลงไม่สำเร็จให้ raise — ห้ามคืนโน้ตเปล่า เดิมคืน stub ทำให้ job รายงาน
    completed ทั้งที่ไม่มีเนื้อหา ความล้มเหลวเลยมองไม่เห็น (เจอ 29 โน้ตจาก 6 เอกสาร)
    """

    nav_links = []
    if prev_link:
        nav_links.append(f"← [[{prev_link}|ส่วนก่อนหน้า]]")
    if next_link:
        nav_links.append(f"[[{next_link}|ส่วนถัดไป]] →")
    nav_str = "  |  ".join(nav_links) if nav_links else ""

    location_tag = ""
    location_breadcrumb = ""
    if province:
        location_tag = f", {province}"
        if district:
            location_tag += f", {district}"
            location_breadcrumb = f"[[เขต10/INDEX|เขต 10]] > [[เขต10/{province}/INDEX|{province}]] > [[เขต10/{province}/{district}/INDEX|{district}]]"
        else:
            location_breadcrumb = f"[[เขต10/INDEX|เขต 10]] > [[เขต10/{province}/INDEX|{province}]]"

    tags_hint = f'pdf-ingest{location_tag}'

    # สไลด์: ความหมายอยู่ในภาพ ไม่ใช่ข้อความ ต้องสั่งให้อธิบายสิ่งที่เห็นด้วย
    slide_rules = """
   - **เอกสารนี้เป็นสไลด์นำเสนอ** ความหมายส่วนใหญ่อยู่ในภาพ ไม่ใช่ข้อความ:
     • อธิบายแผนภูมิ/แผนภาพ/ไดอะแกรม/อินโฟกราฟิกที่เห็นเป็นข้อความให้ครบ
       (ชนิดของแผนภูมิ แกน ค่าตัวเลข แนวโน้ม สิ่งที่สื่อ)
     • ถ้ามีตัวเลขในภาพ ให้ถอดออกมาเป็นตาราง Markdown
     • ระบุหัวข้อของแต่ละสไลด์เป็น ### แล้วตามด้วยเนื้อหาของสไลด์นั้น
     • อย่าคัดแค่ข้อความหัวข้อสั้น ๆ — ต้องได้สาระที่สไลด์ต้องการสื่อจริง""" if is_slides else ""

    # ส่วนหัวของโน้ต — ปกติสั่งให้โมเดลพิมพ์ตามนี้ แต่ถ้าต้องแบ่งช่วงหน้าย่อย
    # จะประกอบเองจากตัวแปรนี้แทน (แม่นกว่าให้โมเดลสร้างซ้ำหลายรอบ)
    header = f"""```yaml
---
title: "{base_filename} (ส่วนที่ {chunk_index}/{total_chunks})"
source_pages: "หน้า {page_start}–{page_end}"
part: {chunk_index}/{total_chunks}
province: "{province or 'ไม่ระบุ'}"
district: "{district or 'ไม่ระบุ'}"
location_confidence: "{location_confidence}"
tags: [{tags_hint}]
created: {datetime.now().strftime('%Y-%m-%d')}
---
```

{location_breadcrumb}

{nav_str if nav_str else '*ไม่มีลิ้งนำทาง*'}"""

    if page_images:
        source_hint = f"ภาพของหน้า {page_start} ถึง {page_end} ที่แนบมา (เรียงตามลำดับหน้า)"
        scope_rule = f"ใช้เฉพาะภาพที่แนบมาเท่านั้น ({page_end - page_start + 1} หน้า)"
    else:
        source_hint = f"ไฟล์ PDF ที่แนบมา เฉพาะหน้าที่ {page_start} ถึงหน้าที่ {page_end} เท่านั้น"
        scope_rule = f"กรุณาดึงและแปลงเนื้อหาเฉพาะจากไฟล์ PDF หน้าที่ {page_start} ถึงหน้าที่ {page_end} เท่านั้น (ห้ามดึงหน้าอื่นมาปนเด็ดขาด)"

    prompt = f"""คุณเป็น AI ผู้เชี่ยวชาญด้านการจัดการความรู้ (Knowledge Management) สาธารณสุขเขต 10
ดึงเนื้อหาจาก{source_hint} แล้วแปลงให้เป็น Markdown ที่สวยงาม อ่านง่าย และเป็นระเบียบ

**ข้อกำหนด:**
1. เริ่มด้วย YAML frontmatter ดังนี้ (ห้ามเปลี่ยนรูปแบบ):
```yaml
---
title: "{base_filename} (ส่วนที่ {chunk_index}/{total_chunks})"
source_pages: "หน้า {page_start}–{page_end}"
part: {chunk_index}/{total_chunks}
province: "{province or 'ไม่ระบุ'}"
district: "{district or 'ไม่ระบุ'}"
location_confidence: "{location_confidence}"
tags: [{tags_hint}]
created: {datetime.now().strftime('%Y-%m-%d')}
---
```

2. หลัง frontmatter ใส่ breadcrumb location และ navigation bar:
```
{location_breadcrumb}

{nav_str if nav_str else '*ไม่มีลิ้งนำทาง*'}
```
จากนั้นใส่เส้นคั่น `---`

3. แปลงเนื้อหาเป็น Markdown:
   - ใช้ # ## ### สำหรับหัวข้อ
   - ใช้ bullet list และ numbered list ตามความเหมาะสม
   - สร้าง table หากมีข้อมูลตาราง
   - ใช้ **ตัวหนา** สำหรับคำสำคัญ
   - รักษาความหมายเดิมไว้ทุกประการ{slide_rules}
   - **สำคัญมาก:** ข้อความต้นฉบับอาจมีสระหรือวรรณยุกต์ภาษาไทยตกหล่น (เช่น 'มุ งเน น' หรือ 'ด าน' หรือมีอักขระสี่เหลี่ยม) ให้คุณช่วยแก้ไขคำให้ถูกต้องตามความหมายและบริบทของภาษาไทยโดยอัตโนมัติ (เช่น แก้เป็น 'มุ่งเน้น', 'ด้าน')
   - ถ้าเนื้อหาเป็นภาษาไทย ให้ใช้ภาษาไทย

4. ท้ายไฟล์ใส่ navigation ซ้ำ และ `---`

{scope_rule}"""

    if page_images:
        contents = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in page_images]
        contents.append(prompt)
    else:
        contents = [uploaded_file, prompt]

    # หน้าสแกนล้วน (ไม่มี text layer) ทำให้โมเดลวนลูปพ่นอักขระซ้ำได้ ที่ temperature ต่ำ
    # จะวนซ้ำเดิมทุกครั้ง — ไล่เพิ่ม temperature ทีละขั้นเพื่อให้หลุดลูป
    last_err = ""
    for temperature in _CONVERT_TEMPERATURES:
        response = client.models.generate_content(
            model=_gemini_model(),
            contents=contents,
            config=types.GenerateContentConfig(
                max_output_tokens=8192,
                temperature=temperature,
            ),
        )
        text = (response.text or "").strip()
        if not text:
            last_err = "AI คืนเนื้อหาว่าง"
            continue
        if _is_degenerate(text):
            last_err = "AI วนซ้ำจนเนื้อหาใช้ไม่ได้"
            logger.warning("chunk %s วนซ้ำที่ temperature %s — ลองใหม่", chunk_index, temperature)
            continue
        return text

    # ยังวนซ้ำอยู่ — สาเหตุคือ "จำนวนหน้าต่อครั้งมากเกิน" ไม่ใช่ temperature
    # (วัดจริงกับ 15 หน้าสแกน: 15 หน้าวนลูปทุกครั้ง แต่ 6 หน้าผ่านสบาย)
    # แบ่งเป็นช่วงย่อยแล้วประกอบ frontmatter/nav เองแทนที่จะให้โมเดลสร้าง
    if page_images and len(page_images) > _SPLIT_MAX_PAGES:
        logger.warning("chunk %s แบ่งเป็นช่วงย่อยละ %s หน้า", chunk_index, _SPLIT_MAX_PAGES)
        bodies: list[str] = []
        for off in range(0, len(page_images), _SPLIT_MAX_PAGES):
            sub = page_images[off:off + _SPLIT_MAX_PAGES]
            bodies.append(_ai_convert_body(
                client, sub, page_start + off, page_start + off + len(sub) - 1, is_slides,
            ))
        return "\n\n".join([header, "---", "\n\n".join(bodies), "---", nav_str]).strip()

    raise RuntimeError(f"{last_err}สำหรับส่วนที่ {chunk_index}")


def _ai_convert_body(
    client, page_images: list[bytes], page_start: int, page_end: int, is_slides: bool,
) -> str:
    """ถอดเนื้อหาของช่วงหน้าย่อยเป็น Markdown "เฉพาะตัวเนื้อหา" (ไม่มี frontmatter/nav)

    ใช้ตอนที่ช่วงหน้าใหญ่เกินจนโมเดลวนลูป — ผู้เรียกจะประกอบส่วนหัวเองทีเดียว
    """
    extra = "\n- อธิบายแผนภูมิ/แผนภาพที่เห็นเป็นข้อความด้วย" if is_slides else ""
    prompt = f"""ถอดเนื้อหาจากภาพหน้า {page_start}–{page_end} ที่แนบมาเป็น Markdown ภาษาไทย

- ใช้ # ## ### สำหรับหัวข้อ, สร้างตาราง Markdown หากเป็นข้อมูลตาราง
- แก้สระ/วรรณยุกต์ภาษาไทยที่ตกหล่นจากการสแกนให้ถูกต้องตามบริบท
- ห้ามใส่ YAML frontmatter หรือลิงก์นำทาง เอาเฉพาะเนื้อหา{extra}"""

    contents = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in page_images]
    contents.append(prompt)
    for temperature in _CONVERT_TEMPERATURES:
        response = client.models.generate_content(
            model=_gemini_model(),
            contents=contents,
            config=types.GenerateContentConfig(max_output_tokens=8192, temperature=temperature),
        )
        text = (response.text or "").strip()
        if text and not _is_degenerate(text):
            return text
    raise RuntimeError(f"ถอดเนื้อหาหน้า {page_start}–{page_end} ไม่สำเร็จ")


# ── Core ingest function ──────────────────────────────────────────────────────

def _do_ingest(
    job_id: str,
    file_id: str,
    original_name: str,
    override_province: str | None = None,
    override_district: str | None = None,
    override_folder_name: str | None = None,
) -> None:
    """รัน ingest ใน background thread."""
    job = _jobs[job_id]
    job["status"] = "running"
    job["progress"] = []
    _publish("job", job_id, job)

    def log(msg: str):
        logger.info(f"[{job_id}] {msg}")
        job["progress"].append({"time": time.time(), "msg": msg})
        _publish("job", job_id, job)   # ให้ worker อื่นเห็น progress ล่าสุดด้วย

    try:
        s = get_settings()
        chunk_size = s.PDF_INGEST_PAGES_PER_CHUNK

        # 1. ดึง PDF จาก MinIO
        log(f"📥 กำลังดึงไฟล์ {original_name} จาก MinIO...")
        client_minio = _get_client()
        resp = client_minio.get_object(_pdf_bucket(), file_id)
        file_bytes = resp.read()
        log(f"✅ ดึงไฟล์สำเร็จ ({len(file_bytes):,} bytes)")

        # 2. Extract pages
        log("📄 กำลังอ่านหน้า PDF...")
        pages = _extract_pages(file_bytes)
        total_pages = len(pages)
        log(f"✅ อ่าน PDF สำเร็จ: {total_pages} หน้า")

        if total_pages == 0:
            job["status"] = "error"
            job["error"] = "ไม่สามารถอ่านข้อความจาก PDF ได้ (อาจเป็น scanned PDF)"
            _publish("job", job_id, job)   # return ตรงนี้ไม่ผ่าน log() ต้องมิเรอร์เอง
            return

        # 3. เลือกวิธีป้อนเนื้อหาให้ AI: PDF ทั้งไฟล์ หรือ เรนเดอร์หน้าเป็นภาพ
        size_mb = len(file_bytes) / 1024 / 1024
        is_slides = _looks_like_slides(file_bytes, pages)
        too_big = size_mb > _PDF_SIZE_LIMIT_MB
        use_images = too_big or is_slides
        if is_slides:
            chunk_size = min(chunk_size, _SLIDE_CHUNK_PAGES)
            log(f"🖼️ ตรวจพบว่าเป็นสไลด์นำเสนอ — ใช้โหมดอ่านภาพ ส่วนละ {chunk_size} หน้า")
        elif too_big:
            log(f"🖼️ ไฟล์ใหญ่ {size_mb:.0f} MB (เกิน {_PDF_SIZE_LIMIT_MB} MB) — เรนเดอร์เป็นภาพแทนการอัป PDF ทั้งไฟล์")

        chunks = _chunk_pages(pages, chunk_size)
        total_chunks = len(chunks)
        log(f"✂️ แบ่งเป็น {total_chunks} ส่วน (ส่วนละ {chunk_size} หน้า)")

        # 4. Gemini client
        gemini_client = _get_gemini_client()

        # 5. อัปโหลด PDF ให้ Gemini (Native PDF Understanding) — ข้ามถ้าใช้โหมดภาพ
        uploaded_file = None
        tmp_path = None            # โหมดภาพไม่ได้เขียนไฟล์ชั่วคราว แต่ finally อ้างถึงเสมอ
        if not use_images:
            log("📤 กำลังอัปโหลด PDF ให้ AI...")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name

            max_attempts = 4
            for attempt in range(1, max_attempts + 1):
                try:
                    uploaded_file = gemini_client.files.upload(file=tmp_path, config={'mime_type': 'application/pdf'})
                    break
                except Exception as upload_err:
                    if attempt == max_attempts:
                        raise upload_err
                    wait_time = attempt * 2
                    log(f"⚠️ อัปโหลดล้มเหลวชั่วคราว ({upload_err}) กำลังลองใหม่ใน {wait_time} วินาที... (ครั้งที่ {attempt}/{max_attempts})")
                    time.sleep(wait_time)
            log("✅ อัปโหลดไฟล์ให้ AI สำเร็จ (หลีกเลี่ยงปัญหาฟอนต์ภาษาไทยของ PDF)")

            # ไฟล์ใหญ่ต้องรอ Gemini ประมวลผลก่อน (state: PROCESSING → ACTIVE)
            # ไม่งั้นเรียก generate_content ทันทีจะได้ 400 INVALID_ARGUMENT ทุก chunk พร้อมกัน
            log("⏳ กำลังรอ AI ประมวลผลไฟล์...")
            wait_elapsed = 0
            poll_interval = 3
            max_wait = 300
            while uploaded_file.state.name == "PROCESSING":
                if wait_elapsed >= max_wait:
                    raise RuntimeError(f"AI ประมวลผลไฟล์ไม่เสร็จภายใน {max_wait} วินาที")
                time.sleep(poll_interval)
                wait_elapsed += poll_interval
                uploaded_file = gemini_client.files.get(name=uploaded_file.name)
            if uploaded_file.state.name == "FAILED":
                raise RuntimeError(f"AI ประมวลผลไฟล์ล้มเหลว: {uploaded_file.error}")
            log(f"✅ ไฟล์พร้อมใช้งาน (state: {uploaded_file.state.name})")

        sample_text = "\n\n".join(pages[:min(5, total_pages)])

        # โหมดภาพไม่มี uploaded_file — ใช้ภาพหน้าแรก ๆ ให้ AI ดูตอนตั้งชื่อ/เดาจังหวัดแทน
        meta_source = uploaded_file
        if use_images:
            meta_source = _render_page_images(file_bytes, 1, min(5, total_pages))

        run_ai_name = not override_folder_name
        run_ai_loc = not override_province or override_province == "auto"

        base_filename = override_folder_name or ""
        province = override_province if (override_province and override_province != "auto") else None
        district = override_district if (override_district and override_district != "auto") else None
        confidence = "manual"
        reason = "กำหนดโดยผู้ใช้"

        if run_ai_name or run_ai_loc:
            log("🤖 AI กำลังวิเคราะห์ข้อมูลเพิ่มเติม...")
            with ThreadPoolExecutor(max_workers=2) as meta_pool:
                fut_name = None
                fut_loc = None
                if run_ai_name:
                    fut_name = meta_pool.submit(_ai_generate_filename, gemini_client, meta_source, original_name)
                if run_ai_loc:
                    fut_loc  = meta_pool.submit(_ai_detect_location,  gemini_client, meta_source, sample_text, original_name)
                
                if fut_name:
                    base_filename = fut_name.result()
                if fut_loc:
                    location = fut_loc.result()
                    province = location["province"]
                    district = location["district"]
                    confidence = location["confidence"]
                    reason = location["reason"]

        log(f"📝 ชื่อเอกสาร: {base_filename}")
        if province == "ส่วนกลาง":
            district = None
        if district == "none" or district == "ไม่มี" or not district:
            district = None

        if not province or province not in ZONE10_PROVINCES:
            province = "ส่วนกลาง"
            district = None

        loc_str = f"{province}"
        if district:
            loc_str += f" / {district}"
        log(f"📍 ตำแหน่ง: {loc_str} (ความแม่นยำ: {confidence})")
        log(f"   เหตุผล: {reason}")

        # 6. Resolve relative path prefix (ใช้แทน filesystem path)
        rel_path = _resolve_rel_path(province, district, base_filename)
        log(f"📁 โครงสร้าง DB path: {rel_path}/")

        # Pre-compute all chunk names
        safe_name = _sanitize_filename(base_filename)
        chunk_filenames = []
        for i in range(total_chunks):
            if total_chunks == 1:
                chunk_filenames.append(safe_name)
            else:
                chunk_filenames.append(f"{safe_name}-ส่วนที่{i+1:02d}")

        # 7. Convert each chunk to MD (parallel)
        created_files: list[str] = []
        md_results: dict[int, str] = {}

        def _convert_chunk(i: int) -> tuple[int, str]:
            chunk_num = i + 1
            page_start = i * chunk_size + 1
            page_end = min(page_start + chunk_size - 1, total_pages)
            prev_link = chunk_filenames[i - 1] if i > 0 else None
            next_link = chunk_filenames[i + 1] if i < total_chunks - 1 else None

            def _call(images: list[bytes] | None) -> str:
                return _ai_convert_to_markdown(
                    client=gemini_client,
                    uploaded_file=uploaded_file,
                    chunk_index=chunk_num,
                    total_chunks=total_chunks,
                    base_filename=base_filename,
                    page_start=page_start,
                    page_end=page_end,
                    prev_link=prev_link,
                    next_link=next_link,
                    province=province,
                    district=district,
                    location_confidence=confidence,
                    page_images=images,
                    is_slides=is_slides,
                )

            if use_images:
                return i, _call(_render_page_images(file_bytes, page_start, page_end))
            try:
                return i, _call(None)
            except Exception as exc:
                # PDF ทั้งไฟล์ไม่ผ่าน (ส่วนใหญ่คือ 400 เพราะไฟล์ใหญ่) ลองใหม่ด้วยภาพเฉพาะหน้านี้
                logger.warning("chunk %s ผ่าน PDF ไม่สำเร็จ (%s) — ลองใหม่ด้วยภาพ", chunk_num, exc)
                log(f"⚠️ ส่วนที่ {chunk_num} อ่านจาก PDF ไม่สำเร็จ กำลังลองใหม่ด้วยการเรนเดอร์เป็นภาพ...")
                return i, _call(_render_page_images(file_bytes, page_start, page_end))

        max_parallel = min(s.PDF_INGEST_MAX_PARALLEL, total_chunks)
        log(f"⚡ แปลง {total_chunks} ส่วนพร้อมกัน (สูงสุด {max_parallel} threads)...")

        failed_chunks: list[str] = []
        with ThreadPoolExecutor(max_workers=max_parallel) as chunk_pool:
            futures = {chunk_pool.submit(_convert_chunk, i): i for i in range(total_chunks)}
            for fut in as_completed(futures):
                idx = futures[fut]
                chunk_num = idx + 1
                page_start = idx * chunk_size + 1
                page_end = min(page_start + chunk_size - 1, total_pages)
                try:
                    _, content = fut.result()
                except Exception as chunk_err:
                    # ไม่เขียนโน้ตเปล่าแทน — ต้องรายงานว่าส่วนนี้พัง ไม่ใช่แกล้งว่าสำเร็จ
                    logger.error("chunk %s ล้มเหลว: %s", chunk_num, chunk_err, exc_info=True)
                    log(f"❌ แปลงส่วนที่ {chunk_num}/{total_chunks} ไม่สำเร็จ: {chunk_err}")
                    failed_chunks.append(f"ส่วนที่ {chunk_num} (หน้า {page_start}–{page_end})")
                    continue
                log(f"✅ แปลงส่วนที่ {chunk_num}/{total_chunks} สำเร็จ (หน้า {page_start}–{page_end})")
                md_results[idx] = content

        if not md_results:
            raise RuntimeError(
                f"แปลงเนื้อหาไม่สำเร็จเลยสักส่วน ({total_chunks} ส่วน) — {'; '.join(failed_chunks[:3])}"
            )

        # ── บันทึก chunks ลง Database (แทน filesystem) ────────────────────────
        current_year = datetime.now().year
        for i in sorted(md_results):
            chunk_name = chunk_filenames[i]
            note_id = f"{VAULT_ID}::{rel_path}/{chunk_name}"
            chunk_num = i + 1
            page_start = i * chunk_size + 1
            page_end = min(page_start + chunk_size - 1, total_pages)
            _upsert_note(
                note_id=note_id,
                relative_path=f"{rel_path}/{chunk_name}.md",
                title=f"{base_filename} (ส่วนที่ {chunk_num}/{total_chunks})",
                province=province,
                district=district,
                note_type="report",
                tags=["pdf-ingest", province or "ส่วนกลาง"],
                source_file=original_name,
                content=md_results[i],
                file_id=file_id,
                chunk_index=i + 1,
                is_index=False,
                year=current_year,
            )
            created_files.append(f"{rel_path}/{chunk_name}.md")
            log(f"💾 บันทึก DB: {rel_path}/{chunk_name}.md")

        index_note_id = f"{VAULT_ID}::{rel_path}/{safe_name}-INDEX"

        # 8. Create INDEX note ใน DB
        log("📚 กำลังสร้าง Index note ใน DB...")
        breadcrumb = ""
        if province:
            breadcrumb = f"\n\n[[เขต10/INDEX|เขต 10]] > [[เขต10/{province}/INDEX|{province}]]"
            if district:
                breadcrumb += f" > [[เขต10/{province}/{district}/INDEX|{district}]]"
            breadcrumb += f" > 📁 {safe_name}"

        index_lines = [
            f"# {base_filename} — ดัชนีเนื้อหา\n",
            f"> **แหล่งที่มา:** `{original_name}` | **MinIO ID:** `{file_id}`\n",
            f"> **จำนวนหน้า:** {total_pages} หน้า | **แบ่งเป็น:** {total_chunks} ส่วน\n",
            f"> **จังหวัด:** {province or 'ไม่ระบุ'} | **อำเภอ:** {district or 'ไม่ระบุ'}\n",
            f"> **วันที่ ingest:** {datetime.now().strftime('%d %B %Y %H:%M')}\n",
            breadcrumb,
            "\n---\n\n## 📎 ไฟล์ PDF ต้นฉบับ (MinIO)\n",
            f"- file_id: `{file_id}` (ดูผ่าน /pdf/view/{file_id})\n",
            "\n## 📑 รายการส่วน\n",
        ]
        for i, fname in enumerate(chunk_filenames):
            page_start = i * chunk_size + 1
            page_end = min(page_start + chunk_size - 1, total_pages)
            index_lines.append(f"- [[{fname}|ส่วนที่ {i+1}: หน้า {page_start}–{page_end}]]")

        index_content = "\n".join(index_lines)
        _upsert_note(
            note_id=index_note_id,
            relative_path=f"{rel_path}/{safe_name}-INDEX.md",
            title=f"{base_filename} — ดัชนี",
            province=province,
            district=district,
            note_type="MOC",
            tags=["pdf-ingest", "index", province or "ส่วนกลาง"],
            source_file=original_name,
            content=index_content,
            file_id=file_id,
            chunk_index=0,
            is_index=True,
            year=current_year,
        )
        index_filename = f"{safe_name}-INDEX.md"
        log(f"✅ สร้าง Index note: {rel_path}/{index_filename}")

        # 8.5 Register PDF ใน obsidian_pdf_assets (link กลับ MinIO)
        # ต้องทำ "หลัง" สร้าง INDEX note เพราะ note_id เป็น FK ไป obsidian_notes
        # (เดิมอยู่ก่อนหน้านี้ → insert ล้ม FK ทุกครั้ง ตารางจึงแทบว่าง)
        log("📎 ลงทะเบียน PDF ต้นฉบับใน obsidian_pdf_assets...")
        try:
            s_cfg = get_settings()
            scheme = "https" if s_cfg.MINIO_USE_SSL else "http"
            minio_url = f"{scheme}://{s_cfg.minio_endpoint_url}/{s_cfg.PDF_BUCKET}/{file_id}"
            execute_db(
                """
                INSERT INTO obsidian_pdf_assets
                    (province, note_id, filename, minio_path, minio_url, file_size, content_type)
                VALUES (%s, %s, %s, %s, %s, %s, 'application/pdf')
                ON CONFLICT (minio_path) DO UPDATE SET
                    filename  = EXCLUDED.filename,
                    note_id   = EXCLUDED.note_id,
                    minio_url = EXCLUDED.minio_url,
                    file_size = EXCLUDED.file_size
                """,
                (province or "ส่วนกลาง", index_note_id, original_name, file_id, minio_url, len(file_bytes)),
            )
            log("✅ ลงทะเบียน PDF ใน obsidian_pdf_assets เรียบร้อย")
        except Exception as pdf_reg_err:
            logger.warning(f"Failed to register PDF in obsidian_pdf_assets: {pdf_reg_err}")
            log(f"⚠️ ไม่สามารถลงทะเบียน PDF ได้: {pdf_reg_err}")

        # 9. อัปเดต INDEX note ของจังหวัด/อำเภอใน DB
        if province:
            _update_province_index_db(province, district, base_filename, index_note_id, total_pages)
            log(f"🔗 อัปเดต Index จังหวัด {province} ใน DB แล้ว")

        # 9.5 Update metadata in MinIO to persist ingest status
        try:
            from minio.commonconfig import CopySource
            import urllib.parse
            bucket = _pdf_bucket()
            stat = client_minio.stat_object(bucket, file_id)
            meta = {k.lower(): v for k, v in (stat.metadata or {}).items()}
            
            custom_meta = {}
            for k, v in meta.items():
                if k.startswith('x-amz-meta-'):
                    key = k[len('x-amz-meta-'):]
                    custom_meta[key] = v
            
            custom_meta['ingested'] = 'true'
            custom_meta['province'] = urllib.parse.quote(province or "")
            custom_meta['district'] = urllib.parse.quote(district or "")
            custom_meta['savedat'] = urllib.parse.quote(rel_path or "")
            custom_meta['confidence'] = confidence or "low"
            # Ensure Content-Type is preserved if present
            content_type = stat.metadata.get('Content-Type') or stat.metadata.get('content-type') or 'application/pdf'
            custom_meta['Content-Type'] = content_type
            
            client_minio.copy_object(
                bucket,
                file_id,
                CopySource(bucket, file_id),
                metadata=custom_meta,
                metadata_directive='REPLACE'
            )
            log(f"💾 อัปเดตสถานะ Ingested ลงใน MinIO Metadata เรียบร้อยแล้ว")
        except Exception as meta_err:
            logger.warning(f"Failed to update MinIO metadata: {meta_err}", exc_info=True)
            log(f"⚠️ ไม่สามารถอัปเดตสถานะลงใน MinIO Metadata ได้: {meta_err}")

        job["status"] = "partial" if failed_chunks else "completed"
        if failed_chunks:
            job["error"] = f"แปลงไม่สำเร็จ {len(failed_chunks)} จาก {total_chunks} ส่วน: " + "; ".join(failed_chunks)
            log(f"⚠️ เสร็จแบบไม่ครบ — ขาด {len(failed_chunks)} จาก {total_chunks} ส่วน")
        job["result"] = {
            "base_filename": base_filename,
            "total_pages": total_pages,
            "total_chunks": total_chunks,
            "failed_chunks": failed_chunks,
            "created_files": created_files,
            "index_file": f"{rel_path}/{index_filename}",
            "vault_path": f"[database] {rel_path}",
            "province": province,
            "district": district,
            "location_confidence": confidence,
            "saved_at": rel_path,
        }
        log(f"🎉 Ingest สำเร็จ! บันทึก {len(created_files)} ไฟล์ MD ที่ {rel_path}")

    except Exception as e:
        logger.error(f"Error in background ingest: {e}", exc_info=True)
        job["status"] = "error"
        job["error"] = str(e)
        log(f"❌ ล้มเหลว: {e}")
    finally:
        if uploaded_file:
            try:
                gemini_client.files.delete(name=uploaded_file.name)
                log("🧹 ลบไฟล์ PDF ชั่วคราวออกจาก Google Cloud")
            except Exception as e:
                logger.warning(f"Failed to delete file from Gemini: {e}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass


# ── Routes ────────────────────────────────────────────────────────────────────

import urllib.parse as _urllib_parse
from fastapi import UploadFile, File

@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """รับไฟล์ PDF และบันทึกลงใน MinIO pdf-library bucket."""
    if not (file.filename or "").lower().endswith(".pdf") and file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    _ensure_pdf_bucket()
    client_minio = _get_client()
    bucket = _pdf_bucket()

    original_name = file.filename or "document.pdf"

    # Check duplicate name in MinIO
    try:
        objects = list(client_minio.list_objects(bucket, recursive=True))
        for obj in objects:
            if obj.object_name.endswith("__apa.json") or obj.object_name.endswith("__path.json"):
                continue
            try:
                stat = client_minio.stat_object(bucket, obj.object_name)
                meta = {k.lower(): v for k, v in (stat.metadata or {}).items()}
                orig_name = _urllib_parse.unquote(meta.get("x-amz-meta-name", ""))
                if orig_name == original_name:
                    raise HTTPException(
                        status_code=400,
                        detail=f"ไฟล์ '{original_name}' เคยอัปโหลดในระบบแล้ว"
                    )
            except HTTPException:
                raise
            except Exception:
                pass
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Error checking duplicate file in MinIO: {e}")

    # ── ตรวจไฟล์ซ้ำจาก "เนื้อหา" ไม่ใช่แค่ชื่อ ────────────────────────────────
    # การเช็คชื่อด้านบนจับไม่ได้เมื่อไฟล์เดียวกันถูกดาวน์โหลดซ้ำแล้วเปลี่ยนชื่อ
    # (เจอจริง: "5.คำชะอี.pdf" กับ "5.คำชะอี (1).pdf" เนื้อหาเหมือนกันเป๊ะ)
    body = await file.read()
    await file.seek(0)
    digest = _hashlib.sha256(body).hexdigest()
    dup = _find_duplicate_object(client_minio, bucket, len(body), digest)
    if dup:
        used_in = query_db(
            "SELECT relative_path FROM obsidian_notes WHERE vault_id=%s AND file_id=%s LIMIT 1",
            (VAULT_ID, dup),
        )
        where = used_in[0]["relative_path"] if used_in else None
        raise HTTPException(
            status_code=409,
            detail={
                "message": "ไฟล์นี้มีอยู่ในระบบแล้ว (เนื้อหาตรงกันทุกไบต์)",
                "existing_file_id": dup,
                "used_in": where,
            },
        )

    # Generate unique file ID
    import random
    for _ in range(100):
        file_id = str(random.randint(100000, 999999))
        try:
            client_minio.stat_object(bucket, file_id)
        except Exception:
            break  # ID is available

    file_bytes = await file.read()
    file_size = len(file_bytes)
    original_name = file.filename or "document.pdf"
    uploaded_at = int(time.time() * 1000)

    import io as _io
    stream = _io.BytesIO(file_bytes)
    meta = {
        "x-amz-meta-name": _urllib_parse.quote(original_name[:150]),
        "x-amz-meta-extension": "pdf",
        "x-amz-meta-size": str(file_size),
        "x-amz-meta-uploadedat": str(uploaded_at),
        "Content-Type": "application/pdf",
    }
    client_minio.put_object(bucket, file_id, stream, file_size, metadata=meta)
    logger.info(f"Uploaded PDF to {bucket}/{file_id}: {original_name}")

    return {
        "id": file_id,
        "name": original_name,
        "size": file_size,
        "uploadedAt": uploaded_at,
        "ingested": False,
    }


@router.post("/analyze-placement/{file_id}")
async def analyze_placement(file_id: str, original_name: str = "document.pdf"):
    """วิเคราะห์ "ที่เก็บที่เหมาะสม" ให้ผู้ใช้ตรวจ **ก่อน** สั่ง ingest จริง

    ทำไมต้องแยกออกมา: ingest เต็มใช้เวลาหลายนาทีและกินโควตา Gemini มาก ถ้า AI เดา
    จังหวัด/ชื่อโฟลเดอร์ผิด ผู้ใช้จะรู้ตอนจบแล้วต้องลบโน้ตทิ้งแล้วทำใหม่ทั้งรอบ
    ตัวนี้อ่านแค่ 5 หน้าแรกจึงเร็วกว่ามาก และคืนตัวเลือกให้แก้ก่อนยืนยัน

    Returns: province/district/confidence/reason + folder_suggestions (มี existing flag)
    """
    s = get_settings()
    if not s.GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="ยังไม่ได้ตั้งค่า GEMINI_API_KEY")

    client_minio = _get_client()
    try:
        resp = client_minio.get_object(_pdf_bucket(), file_id)
        file_bytes = resp.read()
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"ไม่พบไฟล์: {exc}")
    finally:
        try:
            resp.close(); resp.release_conn()
        except Exception:
            pass

    pages = _extract_pages(file_bytes)
    sample_text = "\n\n".join(pages[:5])

    gemini_client = genai.Client(api_key=s.GEMINI_API_KEY)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(file_bytes); tmp.close()
        uploaded = gemini_client.files.upload(file=tmp.name)
        waited = 0
        while uploaded.state.name == "PROCESSING" and waited < 120:
            time.sleep(3); waited += 3
            uploaded = gemini_client.files.get(name=uploaded.name)
        if uploaded.state.name == "FAILED":
            raise HTTPException(status_code=502, detail="AI ประมวลผลไฟล์ไม่สำเร็จ")

        loc = _ai_detect_location(gemini_client, uploaded, sample_text, original_name)
        province = loc.get("province") if loc.get("province") in ZONE10_PROVINCES else "ส่วนกลาง"
        existing = _existing_doc_folders(province)
        names = _ai_suggest_folder_names(gemini_client, uploaded, original_name, existing)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if not names:      # AI ล่ม → ยังต้องเสนออะไรสักอย่างให้ผู้ใช้เลือก
        names = [_sanitize_filename(original_name.rsplit(".", 1)[0])]

    # จำนวนโน้ตของแต่ละโฟลเดอร์ที่มีอยู่ — ให้หน้าจอบอกได้ว่า "เข้าโฟลเดอร์เดิมที่มี N โน้ต"
    counts = {r["folder"]: r["n"] for r in query_db(
        """
        SELECT split_part(relative_path,'/',3) AS folder, count(*) AS n
        FROM obsidian_notes WHERE vault_id=%s AND relative_path LIKE %s
        GROUP BY 1
        """, (VAULT_ID, f"เขต10/{province}/%"))}
    suggestions = [
        {"name": n, "existing": n in counts, "notes": counts.get(n)}
        for n in names
    ]

    return {
        "file_id": file_id,
        "province": province,
        "district": loc.get("district"),
        "confidence": loc.get("confidence"),
        "reason": loc.get("reason"),
        "pages": len(pages),
        "folder_suggestions": suggestions,
        "existing_folders": existing,
    }


@router.post("/ingest/{file_id}")
async def ingest_pdf(
    file_id: str,
    background_tasks: BackgroundTasks,
    original_name: str = "document.pdf",
    province: str = None,
    district: str = None,
    folder_name: str = None,
):
    """เริ่ม ingest PDF จาก MinIO → Markdown → Obsidian vault (จัดเก็บตามจังหวัด/อำเภอ เขต 10)."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "file_id": file_id,
        "original_name": original_name,
        "status": "queued",
        "progress": [],
        "created_at": time.time(),
    }
    background_tasks.add_task(_do_ingest, job_id, file_id, original_name, province, district, folder_name)
    return {"job_id": job_id, "status": "queued"}


def _run_batch(batch_id: str) -> None:
    """รัน ingest ทีละไฟล์ "เรียงลำดับ" จนครบทั้งคิว

    ⚠️ ตั้งใจรันทีละไฟล์ ไม่ขนาน — ingest หนึ่งไฟล์ต้องอัป PDF ให้ Gemini แล้วรอ
    ประมวลผล + แตก chunk เรียก LLM อีกหลายครั้ง ถ้ายิงขนานหลายไฟล์จะชน 429
    quota-per-minute ทันที (ที่อื่นในระบบถึงต้องหน่วง 1.5 วิ ตอนยิง 5 สายพร้อมกัน)

    เก็บสถานะไว้ฝั่งเซิร์ฟเวอร์ ทำให้คิวเดินต่อแม้ผู้ใช้ปิดแท็บหรือเปลี่ยนหน้า
    """
    batch = _batches[batch_id]
    batch["status"] = "running"
    _publish("batch", batch_id, batch)
    for item in batch["items"]:
        if _cancel_requested(batch):
            item["status"] = "cancelled"
            _publish("batch", batch_id, batch)
            continue
        job_id = item["job_id"]
        _jobs[job_id] = {
            "job_id": job_id,
            "file_id": item["file_id"],
            "original_name": item["original_name"],
            "status": "queued",
            "progress": [],
            "created_at": time.time(),
        }
        item["status"] = "running"
        _publish("batch", batch_id, batch)
        try:
            _do_ingest(
                job_id, item["file_id"], item["original_name"],
                item.get("province"), item.get("district"), item.get("folder_name"),
            )
            job = _jobs.get(job_id, {})
            item["status"] = job.get("status", "completed")
            item["error"] = job.get("error")
            item["result"] = job.get("result")
        except Exception as exc:                       # ไฟล์เดียวพังต้องไม่ล้มทั้งคิว
            logger.exception("[batch %s] ไฟล์ %s ล้มเหลว", batch_id, item["file_id"])
            item["status"] = "error"
            item["error"] = str(exc)
        _publish("batch", batch_id, batch)
    done = sum(1 for i in batch["items"] if i["status"] == "completed")
    batch["status"] = "completed" if done == len(batch["items"]) else "partial"
    batch["finished_at"] = time.time()
    _publish("batch", batch_id, batch)


@router.post("/ingest-batch")
async def ingest_batch(background_tasks: BackgroundTasks, body: dict = Body(...)):
    """สั่ง ingest หลายไฟล์เป็นคิวเดียว — คืน batch_id ทันที

    body = {"items": [{"file_id","original_name","province?","district?","folder_name?"}, ...]}
    """
    items_in = body.get("items") or []
    if not items_in:
        raise HTTPException(status_code=400, detail="ต้องระบุ items อย่างน้อย 1 ไฟล์")
    if len(items_in) > 50:
        raise HTTPException(status_code=400, detail="สั่งได้สูงสุด 50 ไฟล์ต่อครั้ง")

    batch_id = str(uuid.uuid4())
    items = []
    for it in items_in:
        if not it.get("file_id"):
            raise HTTPException(status_code=400, detail="ทุก item ต้องมี file_id")
        items.append({
            "file_id": str(it["file_id"]),
            "original_name": it.get("original_name") or "document.pdf",
            "province": it.get("province") or None,
            "district": it.get("district") or None,
            "folder_name": it.get("folder_name") or None,
            "job_id": str(uuid.uuid4()),
            "status": "pending",
            "error": None,
            "result": None,
        })
    _batches[batch_id] = {
        "batch_id": batch_id,
        "status": "queued",
        "items": items,
        "created_at": time.time(),
        "cancel_requested": False,
    }
    _publish("batch", batch_id, _batches[batch_id])
    background_tasks.add_task(_run_batch, batch_id)
    return {"batch_id": batch_id, "total": len(items), "status": "queued"}


@router.get("/ingest-batch/status/{batch_id}")
async def get_batch_status(batch_id: str):
    """สถานะคิว + progress ของไฟล์ที่กำลังทำอยู่"""
    batch = _get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    items = []
    for it in batch["items"]:
        job = _get_job(it["job_id"]) or {}
        progress = job.get("progress") or []
        items.append({
            **{k: it[k] for k in ("file_id", "original_name", "status", "error", "job_id")},
            "last_log": (progress or [{}])[-1].get("msg"),
            # ── log ทั้งก้อนต่อไฟล์ ────────────────────────────────────────────
            # เดิมส่งแค่ last_log เพื่อประหยัด payload แต่ผลคือพอไฟล์เสร็จแล้ว log
            # ของมันหายไปหมด เหลือแค่ "✅ เสร็จสิ้น" ผู้ใช้ไม่เห็นว่าอ่านกี่หน้า
            # แตกกี่ส่วน ส่วนไหนพัง หรือเก็บไว้ที่ไหน — ตรวจสอบอะไรไม่ได้เลย
            #
            # จำกัดที่ _MAX_ITEM_LOGS บรรทัดท้ายกันคิวไฟล์ใหญ่ทำ payload บวม
            # (เอกสาร 400 หน้า = 20 chunk ยังไม่ถึงเพดาน ปกติจึงได้ครบทุกบรรทัด)
            "progress": [p.get("msg") for p in progress[-_MAX_ITEM_LOGS:]],
            "progress_truncated": max(0, len(progress) - _MAX_ITEM_LOGS),
            "result": it.get("result"),
        })
    counts = {}
    for it in batch["items"]:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "total": len(batch["items"]),
        "counts": counts,
        "items": items,
    }


@router.post("/ingest-batch/cancel/{batch_id}")
async def cancel_batch(batch_id: str):
    """ขอหยุดคิว — ไฟล์ที่กำลังทำอยู่จะทำจนจบ ที่เหลือจะถูกข้าม"""
    if not _get_batch(batch_id):
        raise HTTPException(status_code=404, detail="Batch not found")
    _request_cancel(batch_id)
    return {"batch_id": batch_id, "cancel_requested": True}


@router.get("/ingest/status/{job_id}")
async def get_ingest_status(job_id: str):
    """ดู status และ progress ของ ingest job."""
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _stream_job_progress(job_id: str) -> AsyncIterator[str]:
    """SSE stream สำหรับ real-time progress."""
    last_count = 0
    max_wait = 300  # 5 minutes timeout
    start = time.time()

    while time.time() - start < max_wait:
        if job_id not in _jobs:
            yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
            return

        job = _jobs[job_id]
        progress = job.get("progress", [])

        if len(progress) > last_count:
            for msg_obj in progress[last_count:]:
                yield f"data: {json.dumps({'msg': msg_obj['msg'], 'status': job['status']})}\n\n"
            last_count = len(progress)

        if job["status"] in ("completed", "error"):
            final_data = {
                "status": job["status"],
                "done": True,
                "result": job.get("result"),
                "error": job.get("error"),
            }
            yield f"data: {json.dumps(final_data)}\n\n"
            return

        import asyncio
        await asyncio.sleep(1)

    yield f"data: {json.dumps({'error': 'Timeout', 'done': True})}\n\n"


@router.get("/ingest/stream/{job_id}")
async def stream_ingest_progress(job_id: str):
    """SSE endpoint สำหรับ real-time progress streaming."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return StreamingResponse(
        _stream_job_progress(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/view/{file_id}")
async def view_pdf(file_id: str):
    """Stream PDF จาก MinIO pdf-library bucket."""
    from fastapi.responses import Response
    client_minio = _get_client()
    bucket = _pdf_bucket()
    try:
        resp = client_minio.get_object(bucket, file_id)
        data = resp.read()
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline"},
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"File not found: {exc}")


@router.get("/files")
async def list_pdf_files():
    """List PDF files ทั้งหมดใน MinIO bucket พร้อม cross-check ข้อมูล ingest จาก DB."""
    import urllib.parse

    client_minio = _get_client()
    bucket = _pdf_bucket()

    # ดึงข้อมูล ingest ทั้งหมดจาก DB ครั้งเดียว (key = file_id)
    db_all = query_db(
        """
        SELECT file_id,
               source_file,
               COUNT(*) AS chunks,
               MIN(province)  AS province,
               MIN(district)  AS district,
               MIN(relative_path) AS sample_path
        FROM obsidian_notes
        WHERE file_id IS NOT NULL OR source_file IS NOT NULL
        GROUP BY file_id, source_file
        """,
        ()
    )
    # Map 1: by file_id (exact MinIO object name) — highest priority
    db_by_fileid: dict = {}
    # Map 2: by source_file name (original PDF name) — fallback for legacy data
    db_by_sourcefile: dict = {}
    for r in db_all:
        fid = str(r["file_id"]) if r["file_id"] else None
        sfn = str(r["source_file"]) if r["source_file"] else None
        if fid:
            prev = db_by_fileid.get(fid)
            if not prev or int(r["chunks"]) > int(prev["chunks"]):
                db_by_fileid[fid] = r
        if sfn:
            prev = db_by_sourcefile.get(sfn)
            if not prev or int(r["chunks"]) > int(prev["chunks"]):
                db_by_sourcefile[sfn] = r

    try:
        objects = list(client_minio.list_objects(bucket, recursive=True))
        files = []
        for obj in objects:
            name = obj.object_name
            if name.endswith("__apa.json") or name.endswith("__path.json") or name.endswith(".json"):
                continue
            try:
                stat = client_minio.stat_object(bucket, name)
                meta = {k.lower(): v for k, v in (stat.metadata or {}).items()}
                ext = meta.get("x-amz-meta-extension", "").lower()
                orig_name = urllib.parse.unquote(meta.get("x-amz-meta-name", name))

                if ext and ext not in ("pdf", ""):
                    continue
                if not orig_name.lower().endswith(".pdf") and not name.lower().endswith(".pdf") and ext != "pdf":
                    continue

                # --- ข้อมูลจาก DB (source of truth) ---
                # Priority: match by file_id > match by source_file name > none
                db_row = db_by_fileid.get(str(name))
                if not db_row:
                    db_row = db_by_sourcefile.get(orig_name)
                db_ingested = db_row is not None and int(db_row.get("chunks") or 0) > 0
                db_province = db_row["province"] if db_row else None
                db_district = db_row["district"] if db_row else None
                db_chunks = int(db_row["chunks"]) if db_row else 0
                db_saved_at = "/".join(db_row["sample_path"].split("/")[:-1]) if db_row and db_row.get("sample_path") else None

                # --- ข้อมูลจาก MinIO metadata (fallback) ---
                meta_ingested = meta.get("x-amz-meta-ingested") == "true"
                meta_province = urllib.parse.unquote(meta.get("x-amz-meta-province", "")) or None
                meta_district = urllib.parse.unquote(meta.get("x-amz-meta-district", "")) or None
                meta_savedat = urllib.parse.unquote(meta.get("x-amz-meta-savedat", "")) or None
                meta_confidence = meta.get("x-amz-meta-confidence", "") or None

                # --- in-memory job (running/queued override) ---
                in_memory_job = next(
                    (j for j in _jobs.values() if j.get("file_id") == name),
                    None
                )

                if in_memory_job:
                    job_status = in_memory_job.get("status")
                    job_result = in_memory_job.get("result") or {}
                    ingested = db_ingested or (job_status == "completed") or meta_ingested
                    province = job_result.get("province") or db_province or meta_province
                    district = job_result.get("district") or db_district or meta_district
                    saved_at = job_result.get("saved_at") or db_saved_at or meta_savedat
                    location_confidence = job_result.get("location_confidence") or meta_confidence
                    ingest_status = job_status
                    ingest_job_id = in_memory_job.get("job_id")
                else:
                    ingested = db_ingested or meta_ingested
                    province = db_province or meta_province
                    district = db_district or meta_district
                    saved_at = db_saved_at or meta_savedat
                    location_confidence = meta_confidence
                    ingest_status = "completed" if ingested else None
                    ingest_job_id = None

                files.append({
                    "id": name,
                    "name": orig_name,
                    "size": obj.size,
                    "uploaded_at": meta.get("x-amz-meta-uploadedat", str(int(obj.last_modified.timestamp() * 1000)) if obj.last_modified else ""),
                    "ingested": ingested,
                    "ingestJobId": ingest_job_id,
                    "ingestStatus": ingest_status,
                    "province": province,
                    "district": district,
                    "saved_at": saved_at,
                    "location_confidence": location_confidence,
                    "notes_count": db_chunks,   # จำนวน chunks ใน DB
                })
            except Exception:
                pass
        files.sort(key=lambda f: f.get("uploaded_at", ""), reverse=True)
        return {"files": files}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/files/{file_id:path}")
async def delete_pdf_file(file_id: str):
    """ลบไฟล์ PDF จาก MinIO bucket."""
    client_minio = _get_client()
    bucket = _pdf_bucket()
    try:
        client_minio.remove_object(bucket, file_id)
        # ลบ metadata files ที่เกี่ยวข้อง (ถ้ามี)
        for suffix in ("__apa.json", "__path.json"):
            try:
                client_minio.remove_object(bucket, file_id + suffix)
            except Exception:
                pass
        return {"deleted": True, "file_id": file_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/vault/files")
async def list_vault_files():
    """List Markdown notes จาก database (obsidian_notes) แบบ tree structure.

    หมายเหตุ (028): เปลี่ยนจาก filesystem scan เป็น DB query
    """
    rows = query_db(
        """
        SELECT note_id, relative_path, title, province, district, note_type,
               is_index, chunk_index, file_id,
               length(content) AS size,
               EXTRACT(EPOCH FROM updated_at)::bigint AS modified_at
        FROM obsidian_notes
        WHERE vault_id = %s
        ORDER BY province NULLS LAST, relative_path
        """,
        (VAULT_ID,),
    )

    # ── Build tree grouped by province/district ────────────────────────────────
    from collections import defaultdict

    # group notes by province
    by_province: dict[str, list] = defaultdict(list)
    root_notes = []
    for r in rows:
        prov = r.get("province")
        if prov and r["relative_path"].startswith("เขต10/"):
            by_province[prov].append(r)
        else:
            root_notes.append(r)

    def _note_to_file(r: dict) -> dict:
        rel = r["relative_path"]  # e.g. เขต10/อุบล/docname/chunk.md
        name = rel.split("/")[-1]  # filename
        return {
            "type": "file",
            "name": name,
            "path": _strip_md(rel),   # path ไม่มี .md (ตัดเฉพาะท้าย ไม่ใช่ replace ทั้งสตริง)
            "size": r.get("size") or 0,
            "modified_at": r.get("modified_at") or 0,
            "file_id": r.get("file_id"),
            "is_index": r.get("is_index") or False,
        }

    zone10_tree = []
    for province in ZONE10_PROVINCES:
        prov_notes = by_province.get(province, [])
        if not prov_notes:
            continue

        # group by district
        by_district: dict[str | None, list] = defaultdict(list)
        for r in prov_notes:
            by_district[r.get("district")].append(r)

        prov_children = []
        for dist, dist_notes in by_district.items():
            # group by document folder (base_name = path segment after province/district)
            by_doc: dict[str, list] = defaultdict(list)
            for r in dist_notes:
                parts = _strip_md(r["relative_path"]).split("/")
                # parts: [เขต10, province, (district?), docfolder, filename]
                doc_folder = parts[-2] if len(parts) >= 2 else "root"
                by_doc[doc_folder].append(r)

            if dist:
                dist_children = []
                for doc_folder, doc_notes in sorted(by_doc.items()):
                    folder_files = [_note_to_file(r) for r in doc_notes]
                    # Try get file_id from index note
                    idx_note = next((r for r in doc_notes if r.get("is_index")), None)
                    dist_children.append({
                        "type": "folder",
                        "name": doc_folder,
                        "path": f"เขต10/{province}/{dist}/{doc_folder}",
                        "children": folder_files,
                        "file_id": idx_note["file_id"] if idx_note else None,
                    })
                prov_children.append({
                    "type": "folder",
                    "name": dist,
                    "path": f"เขต10/{province}/{dist}",
                    "children": dist_children,
                })
            else:
                for doc_folder, doc_notes in sorted(by_doc.items()):
                    folder_files = [_note_to_file(r) for r in doc_notes]
                    idx_note = next((r for r in doc_notes if r.get("is_index")), None)
                    prov_children.append({
                        "type": "folder",
                        "name": doc_folder,
                        "path": f"เขต10/{province}/{doc_folder}",
                        "children": folder_files,
                        "file_id": idx_note["file_id"] if idx_note else None,
                    })

        zone10_tree.append({
            "type": "folder",
            "name": province,
            "path": f"เขต10/{province}",
            "children": prov_children,
        })

    root_files = [_note_to_file(r) for r in root_notes]

    return {
        "zone10": zone10_tree,
        "root_files": root_files,
        "provinces": list(ZONE10_PROVINCES.keys()),
        "vault_path": "[database]",
    }


@router.get("/vault/db-stats")
async def get_vault_db_stats():
    """
    แสดงสถิติข้อมูล Vault ที่เก็บใน database — ใช้สำหรับตรวจสอบว่าข้อมูลเข้า DB จริง.
    """
    # summary
    summary = query_db("""
        SELECT
            COUNT(*)                                    AS total_notes,
            COUNT(CASE WHEN is_index THEN 1 END)        AS index_notes,
            COUNT(CASE WHEN NOT is_index THEN 1 END)    AS chunk_notes,
            COUNT(DISTINCT province)                    AS provinces,
            COUNT(DISTINCT source_file)                 AS source_files,
            COUNT(file_id)                              AS with_pdf_link,
            MAX(updated_at)                             AS last_updated
        FROM obsidian_notes
        WHERE vault_id = %s
    """, (VAULT_ID,))

    # per province breakdown
    by_province = query_db("""
        SELECT
            COALESCE(province, '(ไม่ระบุ)')    AS province,
            COUNT(*)                            AS notes,
            COUNT(CASE WHEN is_index THEN 1 END) AS index_notes,
            COUNT(DISTINCT district)            AS districts,
            COUNT(file_id)                      AS with_pdf,
            COUNT(DISTINCT source_file)         AS documents
        FROM obsidian_notes
        WHERE vault_id = %s
        GROUP BY province
        ORDER BY province NULLS LAST
    """, (VAULT_ID,))

    # recent 10 notes
    recent = query_db("""
        SELECT
            note_id,
            relative_path,
            title,
            province,
            district,
            is_index,
            chunk_index,
            file_id,
            TO_CHAR(updated_at, 'YYYY-MM-DD HH24:MI') AS updated
        FROM obsidian_notes
        WHERE vault_id = %s
        ORDER BY updated_at DESC
        LIMIT 10
    """, (VAULT_ID,))

    s = summary[0] if summary else {}
    return {
        "vault_id": VAULT_ID,
        "summary": {
            "total_notes": s.get("total_notes", 0),
            "index_notes": s.get("index_notes", 0),
            "chunk_notes": s.get("chunk_notes", 0),
            "provinces": s.get("provinces", 0),
            "source_files": s.get("source_files", 0),
            "with_pdf_link": s.get("with_pdf_link", 0),
            "last_updated": str(s.get("last_updated", "")) if s.get("last_updated") else None,
        },
        "by_province": by_province,
        "recent_notes": recent,
    }


@router.get("/zone10")
async def get_zone10_structure():
    """ดูโครงสร้าง 5 จังหวัดเขต 10 พร้อมรายชื่ออำเภอ."""
    return {
        "zone": "สาธารณสุขเขต 10",
        "provinces": {
            province: {
                "name": province,
                "districts": districts,
                "district_count": len(districts),
            }
            for province, districts in ZONE10_PROVINCES.items()
        },
        "total_provinces": len(ZONE10_PROVINCES),
        "total_districts": sum(len(d) for d in ZONE10_PROVINCES.values()),
    }


# ── Vault CRUD endpoints (DB-based since migration 028) ───────────────────────

from fastapi import Body


def _strip_md(path: str) -> str:
    """ตัดนามสกุล .md เฉพาะ "ท้ายสุด" เท่านั้น

    ⚠️ ห้ามใช้ .replace(".md", "") — มันแทนที่ทุกตำแหน่งในสตริง ถ้าชื่อโฟลเดอร์หรือ
    ชื่อไฟล์มี ".md" อยู่กลางทาง (เช่น "รายงาน.md-สรุป/ก.md") path จะเพี้ยนทันที
    """
    return path.removesuffix(".md")


def _note_id_from_path(path: str) -> str:
    """แปลง relative path เป็น note_id รูปแบบ "ไม่มี .md" (ใช้ตอน "สร้าง" note ใหม่)

    ⚠️ ห้ามใช้ตัวนี้ "ค้นหา" note ที่มีอยู่แล้ว — ใช้ _resolve_note_id() แทน
    เพราะคลังมี note_id อยู่ 2 รูปแบบ (ดูคอมเมนต์ที่ _resolve_note_id)
    """
    return f"{VAULT_ID}::{_strip_md(path)}"


def _resolve_note_id(path: str) -> str | None:
    """หา note_id จริงใน DB จาก path ที่ frontend ส่งมา — คืน None ถ้าไม่มีจริง

    ⚠️ เหตุผลที่ต้องมีฟังก์ชันนี้: คลังมี note_id **2 รูปแบบปนกัน** ตามที่มาของโน้ต
      • โน้ตจาก ingest PDF (754 ตัว) : "health_region_10::เขต10/.../ชื่อ"      ← ไม่มี .md
      • โน้ตที่เขียนเองบนดิสก์ (526 ตัว): "health_region_10::มุกดาหาร/มุกดาหาร.md"  ← มี .md

    ของเดิมตัด .md ทิ้งเสมอแล้วค้นตรง ๆ จึงหาโน้ตตระกูลที่สองไม่เจอเลยสักตัว (404)
    ทำให้ทั้งดู/แก้ไข/ลบ/เปลี่ยนชื่อ ใช้กับโน้ต 526 ตัวนั้นไม่ได้ และหน้าจอขึ้นว่า
    "ไม่มีเนื้อหา" ทั้งที่ในฐานข้อมูลมีเนื้อหาอยู่จริง

    ลำดับการค้น: note_id แบบมี .md → แบบไม่มี .md → สุดท้ายค้นด้วย relative_path ตรง ๆ
    """
    clean = _strip_md(path)
    for candidate in (f"{VAULT_ID}::{clean}.md", f"{VAULT_ID}::{clean}"):
        rows = query_db("SELECT note_id FROM obsidian_notes WHERE note_id = %s", (candidate,))
        if rows:
            return rows[0]["note_id"]

    # ทางสำรอง: บาง note_id อาจไม่ได้ตั้งตามสูตร VAULT_ID::relative_path — ค้นจาก path จริง
    rows = query_db(
        "SELECT note_id FROM obsidian_notes WHERE vault_id = %s AND relative_path IN (%s, %s)",
        (VAULT_ID, f"{clean}.md", clean),
    )
    return rows[0]["note_id"] if rows else None


@router.get("/vault/file")
async def read_vault_file(path: str):
    """อ่านเนื้อหา Markdown note จาก database."""
    note_id = _resolve_note_id(path)
    rows = query_db(
        "SELECT note_id, relative_path, title, content, is_index, "
        "length(content) AS size, EXTRACT(EPOCH FROM updated_at)::bigint AS modified_at "
        "FROM obsidian_notes WHERE note_id = %s",
        (note_id,),
    ) if note_id else []
    if not rows:
        raise HTTPException(status_code=404, detail=f"Note not found: {path}")
    r = rows[0]
    name = r["relative_path"].split("/")[-1]
    return {
        "path": path,
        "name": name,
        "content": r["content"] or "",
        "size": r.get("size") or 0,
        "modified_at": r.get("modified_at") or 0,
    }


@router.put("/vault/file")
async def write_vault_file(
    path: str,
    body: dict = Body(...),
):
    """อัปเดตหรือสร้าง Markdown note ใน database."""
    import re as _re
    content = body.get("content", "")
    stripped = _re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, count=1, flags=_re.DOTALL).strip()
    name = _strip_md(path).split("/")[-1]
    relative_path = path if path.endswith(".md") else path + ".md"

    # แก้ไขของเดิม → ต้อง resolve ให้เจอ note_id จริง (รองรับทั้ง 2 รูปแบบ)
    # ถ้าไม่มีจริง → สร้างใหม่ด้วยรูปแบบมาตรฐาน (ไม่มี .md) ตามเดิม
    existing_id = _resolve_note_id(path)
    if existing_id:
        execute_db(
            "UPDATE obsidian_notes SET content = %s, content_stripped = %s, updated_at = NOW() WHERE note_id = %s",
            (content, stripped, existing_id),
        )
    else:
        note_id = _note_id_from_path(path)
        # สร้าง note ใหม่
        _upsert_note(
            note_id=note_id,
            relative_path=relative_path,
            title=name,
            province=None,
            district=None,
            note_type="report",
            tags=[],
            source_file="",
            content=content,
        )
    return {"path": path, "size": len(content.encode()), "modified_at": int(time.time())}


@router.delete("/vault/file")
async def delete_vault_file(path: str):
    """ลบ note หรือ prefix ทั้งหมดออกจาก database."""
    # ลบ exact match (resolve ก่อน — ไม่งั้นโน้ตตระกูล .md จะหาไม่เจอแล้วตกไปลบทั้งโฟลเดอร์)
    note_id = _resolve_note_id(path)
    if note_id:
        execute_db("DELETE FROM obsidian_notes WHERE note_id = %s", (note_id,))
        return {"deleted": True, "path": path, "count": 1}
    # ลบทั้ง prefix (folder delete)
    clean = _strip_md(path)
    execute_db(
        "DELETE FROM obsidian_notes WHERE vault_id = %s AND relative_path LIKE %s",
        (VAULT_ID, f"{clean}%"),
    )
    return {"deleted": True, "path": path}


@router.post("/vault/rename")
async def rename_vault_file(body: dict = Body(...)):
    """เปลี่ยนชื่อ note ใน database (อัปเดต note_id และ relative_path)."""
    old_path = body.get("old_path", "")
    new_path = body.get("new_path", "")
    if not old_path or not new_path:
        raise HTTPException(status_code=400, detail="old_path and new_path required")
    old_id = _resolve_note_id(old_path)
    if not old_id:
        raise HTTPException(status_code=404, detail="Source not found")
    # ปลายทางตั้งชื่อตามรูปแบบเดิมของต้นทาง — ถ้าเดิมมี .md ก็คงไว้ ไม่งั้นจะกลายเป็น
    # โน้ตคนละรูปแบบกับไฟล์จริงบนดิสก์ที่ยังใช้ชื่อเดิมอยู่
    new_id = (
        f"{VAULT_ID}::{_strip_md(new_path)}.md"
        if old_id.endswith(".md")
        else _note_id_from_path(new_path)
    )
    new_rel = new_path if new_path.endswith(".md") else new_path + ".md"
    execute_db(
        "UPDATE obsidian_notes SET note_id = %s, relative_path = %s, updated_at = NOW() WHERE note_id = %s",
        (new_id, new_rel, old_id),
    )
    return {"renamed": True, "old_path": old_path, "new_path": new_path}


@router.get("/vault/folders")
async def list_vault_folders():
    """List folder paths ที่มีใน obsidian_notes (สำหรับ dropdown)."""
    rows = query_db(
        "SELECT DISTINCT relative_path FROM obsidian_notes WHERE vault_id = %s ORDER BY relative_path",
        (VAULT_ID,),
    )
    folders: set[str] = set()
    for r in rows:
        parts = _strip_md(r["relative_path"]).split("/")
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]))
    return {"folders": sorted(folders)}


# ── PDF View Endpoints ────────────────────────────────────────────────────────

@router.get("/view/obsidian/{asset_id}")
async def view_obsidian_pdf(asset_id: int):
    """Stream PDF จาก MinIO โดยค้นหา minio_path จาก obsidian_pdf_assets.

    ใช้ asset_id (PK ของ obsidian_pdf_assets) เพื่อ serve PDF ต้นฉบับแบบ inline.
    """
    from fastapi.responses import Response
    rows = query_db(
        "SELECT minio_path, filename, content_type FROM obsidian_pdf_assets WHERE id = %s",
        (asset_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")
    r = rows[0]
    minio_path = r["minio_path"]
    content_type = r.get("content_type") or "application/pdf"
    filename = r.get("filename") or "document.pdf"

    # ลอง pdf-library bucket ก่อน (file_id เก็บ minio_path โดยตรง)
    client_minio = _get_client()
    s_cfg = get_settings()
    for bucket in (s_cfg.PDF_BUCKET, "obsidian-pdfs", s_cfg.MINIO_BUCKET):
        try:
            resp = client_minio.get_object(bucket, minio_path)
            data = resp.read()
            return Response(
                content=data,
                media_type=content_type,
                headers={"Content-Disposition": f'inline; filename="{filename}"'},
            )
        except Exception:
            continue
    raise HTTPException(status_code=404, detail=f"PDF not found in MinIO: {minio_path}")


@router.get("/view/obsidian/by-note/{note_id:path}")
async def view_obsidian_pdf_by_note(note_id: str):
    """Stream PDF ต้นฉบับของ note ที่กำหนด (ค้นจาก obsidian_pdf_assets.note_id)."""
    from fastapi.responses import Response
    full_note_id = f"{VAULT_ID}::{note_id}" if not note_id.startswith(VAULT_ID) else note_id
    rows = query_db(
        "SELECT id, minio_path, filename, content_type FROM obsidian_pdf_assets WHERE note_id = %s",
        (full_note_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No PDF linked to note: {note_id}")
    r = rows[0]
    minio_path = r["minio_path"]
    content_type = r.get("content_type") or "application/pdf"
    filename = r.get("filename") or "document.pdf"

    client_minio = _get_client()
    s_cfg = get_settings()
    for bucket in (s_cfg.PDF_BUCKET, "obsidian-pdfs", s_cfg.MINIO_BUCKET):
        try:
            resp = client_minio.get_object(bucket, minio_path)
            data = resp.read()
            return Response(
                content=data,
                media_type=content_type,
                headers={"Content-Disposition": f'inline; filename="{filename}"'},
            )
        except Exception:
            continue
    raise HTTPException(status_code=404, detail=f"PDF not found in MinIO: {minio_path}")


# -- Filesystem to Database Migration -----------------------------------------

@router.post("/vault/migrate-from-filesystem")
async def migrate_vault_from_filesystem():
    """
    (Legacy) ระบบย้ายมาใช้ database แล้ว — ไม่มี filesystem vault อีกต่อไป.
    ข้อมูลทั้งหมดอยู่ใน obsidian_notes table แล้ว.
    """
    rows = query_db(
        "SELECT COUNT(*) AS total FROM obsidian_notes WHERE vault_id = %s",
        (VAULT_ID,)
    )
    total = rows[0]["total"] if rows else 0
    return {
        "message": f"Vault is already database-native. {total} notes in DB. No filesystem migration needed.",
        "vault_path": "[database]",
        "imported": 0,
        "errors": [],
        "files": [],
    }
