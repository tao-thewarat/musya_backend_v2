"""ซ่อมโน้ตที่ ingest แล้วได้เนื้อหาเปล่า

ที่มา: `_ai_convert_to_markdown` เดิมจับ exception แล้วเขียน "โน้ต stub" แทน
(มีแต่ frontmatter + ข้อความ "ไม่สามารถแปลงเป็น Markdown ได้สำเร็จ") โดย job
ยังรายงาน completed ทำให้ไม่มีใครรู้ว่าเนื้อหาหาย

สคริปต์นี้ซ่อม "เฉพาะ chunk ที่เสีย" ในที่เดิม — ไม่ ingest ใหม่ทั้งเอกสาร
เพราะบางเล่ม 400 หน้า/25 chunk แต่เสียแค่ chunk เดียว การทำใหม่ทั้งเล่ม
สิ้นเปลืองและยังทำให้ AI ตั้งชื่อโฟลเดอร์ใหม่จนเกิดโน้ตซ้ำอีกชุด

ใช้โหมด "เรนเดอร์หน้าเป็นภาพ" เสมอ เพราะสาเหตุหลักที่พังคือ Gemini ปฏิเสธ
PDF ก้อนใหญ่ด้วย 400 INVALID_ARGUMENT (วัดจริง 90–174 MB พังทั้งเล่ม)

    python -m src.scripts.repair_empty_notes            # ซ่อมทั้งหมด
    python -m src.scripts.repair_empty_notes --dry-run  # ดูรายการเฉย ๆ
    python -m src.scripts.repair_empty_notes --limit 3  # ลองทีละน้อย
"""
import argparse
import io
import logging
import re
import sys

from src.db.pool import query_db
from src.routers.pdf_ingest import (
    VAULT_ID,
    _ai_convert_to_markdown,
    _get_client,
    _get_gemini_client,
    _is_degenerate,
    _looks_like_slides,
    _pdf_bucket,
    _render_page_images,
    _upsert_note,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("repair")

STUB_MARKER = "ไม่สามารถแปลงเป็น Markdown ได้สำเร็จ"
_PAGES_RE = re.compile(r'source_pages:\s*"หน้า\s*(\d+)[–\-—](\d+)"')
_PART_RE = re.compile(r"part:\s*(\d+)/(\d+)")
_CHUNK_SUFFIX_RE = re.compile(r"-ส่วนที่(\d+)$")


def find_broken() -> list[dict]:
    """โน้ตที่ต้องซ่อม = stub (แปลงไม่สำเร็จ) หรือเนื้อหาที่โมเดลวนซ้ำจนใช้ไม่ได้"""
    rows = query_db(
        """
        SELECT note_id, relative_path, title, province, district,
               source_file, file_id, chunk_index, content, year
        FROM obsidian_notes
        WHERE vault_id = %s AND chunk_index >= 1
        ORDER BY file_id, chunk_index
        """,
        (VAULT_ID,),
    )
    return [
        r for r in rows
        if STUB_MARKER in (r["content"] or "") or _is_degenerate(r["content"] or "")
    ]


def _page_range(note: dict) -> tuple[int, int] | None:
    """อ่านช่วงหน้าจาก frontmatter ของ stub — stub เก็บ source_pages ไว้ให้แล้ว"""
    m = _PAGES_RE.search(note["content"] or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _nav_links(note: dict) -> tuple[str | None, str | None]:
    """สร้างลิงก์ส่วนก่อนหน้า/ถัดไปจากชื่อไฟล์ (…-ส่วนที่NN)"""
    stem = note["relative_path"].rsplit("/", 1)[-1].removesuffix(".md")
    m = _CHUNK_SUFFIX_RE.search(stem)
    part = _PART_RE.search(note["content"] or "")
    if not m or not part:
        return None, None
    cur, total = int(part.group(1)), int(part.group(2))
    base = stem[: m.start()]
    prev = f"{base}-ส่วนที่{cur - 1:02d}" if cur > 1 else None
    nxt = f"{base}-ส่วนที่{cur + 1:02d}" if cur < total else None
    return prev, nxt


def repair_one(note: dict, pdf_cache: dict[str, bytes]) -> str:
    """คืนข้อความสรุปผลของโน้ตนี้ (raise ถ้าซ่อมไม่ได้)"""
    rng = _page_range(note)
    if not rng:
        raise ValueError("อ่านช่วงหน้าจาก frontmatter ไม่ได้")
    page_start, page_end = rng
    file_id = note["file_id"]
    if not file_id:
        raise ValueError("โน้ตนี้ไม่มี file_id จึงหา PDF ต้นฉบับไม่ได้")

    if file_id not in pdf_cache:
        pdf_cache[file_id] = _get_client().get_object(_pdf_bucket(), file_id).read()
    pdf_bytes = pdf_cache[file_id]

    images = _render_page_images(pdf_bytes, page_start, page_end)
    if not images:
        raise ValueError(f"เรนเดอร์หน้า {page_start}–{page_end} ไม่ได้ (PDF อาจมีหน้าน้อยกว่า)")

    part = _PART_RE.search(note["content"] or "")
    chunk_index = int(part.group(1)) if part else (note["chunk_index"] or 1)
    total_chunks = int(part.group(2)) if part else 1
    prev_link, next_link = _nav_links(note)
    base_filename = re.sub(r"\s*\(ส่วนที่.*$", "", note["title"] or "").strip()

    content = _ai_convert_to_markdown(
        client=_get_gemini_client(),
        uploaded_file=None,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        base_filename=base_filename,
        page_start=page_start,
        page_end=page_end,
        prev_link=prev_link,
        next_link=next_link,
        province=note["province"],
        district=note["district"],
        location_confidence="manual",
        page_images=images,
        is_slides=_looks_like_slides(pdf_bytes, [""] * max(1, page_end - page_start + 1)),
    )
    if STUB_MARKER in content or _is_degenerate(content):
        raise ValueError("ยังได้เนื้อหาที่ใช้ไม่ได้อยู่")

    # เขียนทับที่เดิม — note_id/relative_path เดิม ไม่สร้างโน้ตใหม่ให้ซ้ำ
    _upsert_note(
        note_id=note["note_id"],
        relative_path=note["relative_path"],
        title=note["title"],
        province=note["province"],
        district=note["district"],
        note_type="report",
        tags=["pdf-ingest", note["province"] or "ส่วนกลาง"],
        source_file=note["source_file"] or "",
        content=content,
        file_id=file_id,
        chunk_index=note["chunk_index"] or chunk_index,
        is_index=False,
        year=note["year"],
    )
    return f"{len(content):,} ตัวอักษร (หน้า {page_start}–{page_end})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="แสดงรายการที่จะซ่อมโดยไม่แก้จริง")
    ap.add_argument("--limit", type=int, default=0, help="ซ่อมสูงสุดกี่โน้ต (0 = ไม่จำกัด)")
    args = ap.parse_args()

    broken = find_broken()
    if args.limit:
        broken = broken[: args.limit]
    logger.info("พบโน้ตเปล่า %d ใบ จาก %d เอกสาร",
                len(broken), len({n["file_id"] for n in broken}))
    if args.dry_run:
        for n in broken:
            logger.info("  %s  %s", _page_range(n), n["relative_path"])
        return 0

    pdf_cache: dict[str, bytes] = {}
    ok = failed = 0
    for i, note in enumerate(broken, 1):
        label = note["relative_path"].rsplit("/", 1)[-1]
        try:
            result = repair_one(note, pdf_cache)
            ok += 1
            logger.info("[%d/%d] ✅ %s — %s", i, len(broken), label, result)
        except Exception as exc:
            failed += 1
            logger.warning("[%d/%d] ❌ %s — %s", i, len(broken), label, exc)
        # PDF ก้อนใหญ่ 174 MB ถ้าเก็บทุกเล่มไว้พร้อมกันจะกินแรมเกินจำเป็น
        if len(pdf_cache) > 2:
            pdf_cache.pop(next(iter(pdf_cache)))

    logger.info("เสร็จสิ้น — สำเร็จ %d ล้มเหลว %d", ok, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
