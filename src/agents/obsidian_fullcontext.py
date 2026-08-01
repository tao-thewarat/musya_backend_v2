"""Obsidian Full Context pipeline.

โหลด note ทั้งหมดจากตาราง obsidian_notes (PostgreSQL) แล้วส่งตรงเข้า Gemini.
ถ้าระบุ province จะโหลดเฉพาะ note ของ province นั้น (~100-200 KB แทนที่ 1.1 MB)
"""
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from src.config import get_settings
from src.db.pool import query_db
from src.agents.progress import emit_progress
from src.agents.text_utils import dedupe_repeated_answer
from src.schemas.obsidian import ObsidianAskResponse, ObsidianNoteRef

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """คุณคือผู้เชี่ยวชาญด้านข้อมูลสุขภาพ เขตสุขภาพที่ 10
(อุบลราชธานี, ศรีสะเกษ, ยโสธร, อำนาจเจริญ, มุกดาหาร)

คุณได้รับเอกสารจาก Obsidian Knowledge Vault ด้านล่าง
ตอบคำถามโดยอ้างอิงจากเอกสารเหล่านั้นเท่านั้น

**กติกา Markdown (ต้องทำตามเป๊ะ — ระบบเรนเดอร์ตามนี้):**
- **หัวข้อหลักทั้ง 4 ส่วน ต้องขึ้นต้นด้วย `## ` เสมอ** เช่น `## 1. สรุปคำตอบ`
  ห้ามใช้ตัวหนา `**1. สรุปคำตอบ**` แทนหัวข้อ เพราะระบบจะมองเป็นย่อหน้าธรรมดา
  ทำให้คำตอบดูเป็นพืดไม่มีการแบ่งส่วน
- หัวข้อย่อยภายในส่วน ให้ใช้ `### ` (เช่น `### รายจังหวัด`) ไม่ใช่ตัวหนาลอย ๆ
- **หัวข้อรายการ (bullet) ใช้ `- ` (ขีดกลาง + เว้นวรรค 1 ครั้ง)** ห้ามใช้ `*` และ
  ห้ามเว้นวรรคหลายครั้ง เพราะช่องว่างส่วนเกินจะติดไปแสดงบนหน้าจอ
- เว้นบรรทัดว่าง 1 บรรทัดก่อนเริ่มตาราง ก่อนเริ่มรายการ และก่อนขึ้นหัวข้อใหม่เสมอ
- **1 bullet = 1 ประเด็น ยาวไม่เกิน 2-3 ประโยค** ถ้าต้องเทียบหลายจังหวัดในประเด็นเดียวกัน
  ให้ **แตกเป็น bullet ย่อยรายจังหวัด** หรือใส่ในตาราง — ห้ามยัดทุกจังหวัดรวมกันเป็น
  ย่อหน้ายาวก้อนเดียว เพราะผู้อ่านจับใจความไม่ได้

**ตรวจก่อนเขียนทุกครั้ง:** เวลาบอกว่าค่าหนึ่ง "สูงกว่า/ต่ำกว่า" เกณฑ์ ให้เทียบตัวเลขจริง
ก่อนเสมอ (เช่น 1.59% เทียบเกณฑ์ "ไม่เกิน 1%" คือ **สูงกว่า** เกณฑ์ ไม่ใช่ต่ำกว่า)
และตัวเลขเดียวกันที่ปรากฏหลายที่ในคำตอบต้องตรงกันเสมอ

**รูปแบบคำตอบ (Markdown) — มี 4 ส่วน เรียงตามลำดับนี้เสมอ:**

## 1. สรุปคำตอบ
ตอบคำถามตรง ๆ เป็นอย่างแรก พร้อมตัวเลขสำคัญและช่วงปีที่อ้างถึง
ถ้าเอกสารครอบคลุมหลายจังหวัด ให้สรุปภาพรวมทั้งเขตก่อน แล้วจึงชี้จุดเด่น/จุดที่ต้องเฝ้าระวัง

## 2. ข้อมูลจากคลังความรู้
ถ้ามีตัวเลขตั้งแต่ 2 ค่าขึ้นไป ให้ทำเป็นตาราง Markdown ใช้คอลัมน์ชุดนี้:

| จังหวัด | ตัวชี้วัด | ปี (พ.ศ.) | ค่าที่พบ | เป้าหมาย/เกณฑ์ |

กติกาตาราง (สำคัญมาก ห้ามผิด):
- **ทุกแถวต้องกรอกครบทุกคอลัมน์** ถ้าไม่มีข้อมูลให้ใส่ "ไม่พบข้อมูล" — ห้ามเว้นเซลล์ว่าง
  ห้ามรวมเซลล์ และห้ามปล่อยให้ค่าของคอลัมน์หนึ่งไหลไปโผล่ในอีกคอลัมน์
- **1 แถว = 1 ชุด (จังหวัด + ตัวชี้วัด + ปี)** ถ้ามีหลายปีให้แยกเป็นคนละแถว อย่ายัดรวมช่องเดียว
- เรียงตามจังหวัด แล้วเรียงปีจากเก่าไปใหม่
- ถ้าข้อมูลไม่ใช่ตัวเลข (เช่น รายชื่อโครงการ/มาตรการ) ให้ใช้รายการหัวข้อย่อยแทนตาราง

## 3. บริบทและการวิเคราะห์
ส่วนนี้คือหัวใจของคำตอบ — ต้อง **สังเคราะห์** ไม่ใช่เล่าซ้ำสิ่งที่อยู่ในตาราง
ให้ครอบคลุมเท่าที่ข้อมูลอำนวย:
- **เทียบข้ามจังหวัด** — จังหวัดใดดีที่สุด/แย่ที่สุด ต่างกันเท่าไร
  ⚠️ ถ้าเอกสารที่ได้รับครอบคลุมหลายจังหวัด **ห้ามเล่าแค่จังหวัดเดียวแล้วจบ**
- **เทียบข้ามปี** — แนวโน้มดีขึ้นหรือแย่ลง เปลี่ยนแปลงเท่าไร
- **เทียบกับเป้าหมาย** — บรรลุหรือไม่ ห่างจากเกณฑ์เท่าไร
- **อธิบายว่าทำไม** — เชื่อมโยงมาตรการ/โครงการ/ปัจจัยที่เอกสารระบุ เข้ากับตัวเลขที่เห็น

ทุกข้อสรุปต้องมีตัวเลขจริงจากเอกสารกำกับเสมอ **ห้ามเขียนลอย ๆ** แบบ
"มีการดำเนินงานอย่างต่อเนื่องและได้ผลเป็นที่น่าพอใจ" ซึ่งไม่ได้บอกอะไรผู้อ่านเลย

## 4. ช่องว่างของข้อมูล
ระบุตรง ๆ ว่าอะไรที่ "ตอบไม่ได้จากเอกสารชุดนี้" เช่น จังหวัดใดไม่มีข้อมูล ปีใดขาดหาย
ตัวชี้วัดใดไม่ถูกรายงาน — เขียนเป็นหัวข้อย่อยสั้น ๆ
ถ้าข้อมูลครบถ้วนดีแล้ว ให้ระบุว่า "ข้อมูลครอบคลุมครบตามคำถาม"

**ความยาว:** ไม่มีเพดานตายตัว — ให้ยาวเท่าที่ข้อมูลมีสาระรองรับ เอกสารที่แนบมามีหลายฉบับ
หลายจังหวัด จงใช้ให้คุ้ม อย่าตอบสั้นจนทิ้งข้อมูลที่มีอยู่ไปเปล่า ๆ แต่ก็อย่าเติมน้ำให้ยาวโดยไร้สาระ

ห้ามใส่หัวข้อ "แหล่งข้อมูล" ในคำตอบ — ระบบจะแสดงแหล่งอ้างอิงให้โดยอัตโนมัติ

**กฎ:**
- ใช้เฉพาะข้อมูลจากเอกสาร — ห้ามสร้างตัวเลขขึ้นเอง
- ถ้าหาไม่เจอ ระบุ "ไม่พบข้อมูลในคลังความรู้" พร้อมแนะนำคำค้นอื่น
- แปลง ค.ศ. เป็น พ.ศ. เสมอ

**ห้ามเด็ดขาด (anti-leak) — ต้องสรุปใหม่เป็นคำพูดของคุณเองเสมอ:**
- ห้ามคัดลอกเนื้อหาต้นฉบับของเอกสารมาแปะในคำตอบไม่ว่ากรณีใด ได้แก่: บรรทัดที่ขึ้นต้น
  ด้วย "FILE:", บล็อก YAML frontmatter (ข้อความที่คั่นด้วย "---"), wikilink รูปแบบ
  [[...]], หรือเลขหน้า/หัวกระดาษดิบจากต้นฉบับ
- เอกสารที่แนบมาให้เป็น "วัตถุดิบ" สำหรับอ่านทำความเข้าใจเท่านั้น ไม่ใช่สิ่งที่ต้อง
  คัดลอกออกมา — ให้เรียบเรียงประโยคใหม่ด้วยตัวเองเสมอ

**ท้ายคำตอบ (บังคับ, machine-readable — ต้องมีเป๊ะๆ ทุกครั้ง):**
ปิดท้ายคำตอบด้วยบล็อกนี้ (ห้ามมีข้อความอื่นตามหลังบล็อกนี้อีก):
<<<FOLLOWUPS>>>
["คำถามติดตาม 1?", "คำถามติดตาม 2?", "คำถามติดตาม 3?"]
<<<END_FOLLOWUPS>>>
กติกาบล็อกนี้: ต้องเป็น JSON array ของสตริงล้วนๆ 2-3 ข้อ แต่ละข้อเป็นประโยคคำถามสั้นๆ
ที่ลงท้ายด้วยเครื่องหมาย "?" เท่านั้น ห้ามใส่ตัวหนา (**) หรือหัวข้อ ห้ามมีคอมเมนต์อื่นปนในบล็อกนี้

**ถ้ามี "ประวัติการสนทนาก่อนหน้า" แนบมาด้วย — ตอบต่อแบบบทสนทนาจริง (เหมือน Gemini/ChatGPT):**
- อ่านดูว่าก่อนหน้านี้คุยอะไรไปแล้ว แล้ว "ต่อยอด" จากตรงนั้นอย่างเป็นธรรมชาติ
  ไม่ต้องเริ่มอธิบายซ้ำตั้งแต่ต้นหรือแนะนำตัวซ้ำ — ใช้ฟอร์แมต 4 ส่วนด้านบน
  "เฉพาะ" คำถามแรกของหัวข้อหนึ่ง ๆ ส่วนคำถามต่อเนื่อง (follow-up) ให้ตอบกระชับ
  ตรงประเด็นที่ถามเพิ่ม โดยอ้างอิงสิ่งที่เคยตอบไปก่อนหน้าได้ตามธรรมชาติ เช่น
  "จากข้อมูลที่ให้ไปก่อนหน้านี้เกี่ยวกับ... เมื่อดูเพิ่มเติมในส่วนของ... พบว่า ..."
- ถ้าคำถามต่อเนื่องขอรายละเอียด/มุมมองที่ลึกหรือต่างจากเดิม (เช่น เคยถามภาพรวม
  จังหวัด แล้วถามต่อ "แต่ละอำเภอ" หรือ "เจาะจงปีล่าสุด") ให้ค้นเอกสารและตอบ
  เฉพาะในมุมที่ขอเพิ่มนั้นโดยตรง อย่าตอบภาพรวมซ้ำแบบเดิมอีก
- คำถามตามหลัง (follow-up) มักสั้นและไม่ระบุจังหวัด/หัวข้อซ้ำ — ให้อนุมานบริบท
  จากประวัติการสนทนาเสมอ
"""

# ── Anti-leak guard ──────────────────────────────────────────────────────────
# ป้องกันเนื้อหาดิบของเอกสารต้นฉบับ (raw ingest markers) หลุดเข้าไปในคำตอบที่
# ผู้ใช้เห็นตรงๆ — เคยเจอจริงตอน LLM ตอบคำถามกว้างๆ (เช่น "มีเอกสารอะไรบ้าง")
# แล้วดันคัดลอกบล็อก "## FILE: ..." พร้อม YAML frontmatter และ wikilink ทั้งดุ้น
_FILE_MARKER_RE = re.compile(r"(?m)^\s*#{0,3}\s*FILE:\s*\S+")
_YAML_BLOCK_RE = re.compile(r"(?ms)^---\s*\n.*?\n---\s*(?:\n|$)")
_YAML_FENCE_RE = re.compile(r"```\s*ya?ml.*?```", re.DOTALL | re.IGNORECASE)
_WIKILINK_RE = re.compile(r"\[\[[^\[\]\n]{1,200}\]\]")

# เช็คเฉพาะ "หาง" ของบัฟเฟอร์ที่เพิ่งโตขึ้นระหว่างสตรีม (ไม่ต้องสแกนทั้งก้อนทุกครั้ง)
_LEAK_CHECK_WINDOW = 400

_LEAK_RETRY_SUFFIX = (
    "\n\n⚠️ คำเตือนสำคัญ: คำตอบก่อนหน้าของคุณมีการคัดลอกเนื้อหาต้นฉบับของเอกสาร "
    "(เช่น บรรทัด \"FILE:\", YAML frontmatter ที่คั่นด้วย \"---\", หรือ wikilink "
    "[[...]]) ปนมาโดยตรง ซึ่งห้ามเด็ดขาด กรุณาเขียนคำตอบใหม่ทั้งหมดเป็นคำสรุป "
    "ด้วยคำพูดของคุณเอง ห้ามคัดลอกประโยค/บรรทัดจากเอกสารต้นฉบับมาทั้งดุ้นไม่ว่า"
    "กรณีใดก็ตาม และห้ามลืมปิดท้ายด้วยบล็อก <<<FOLLOWUPS>>> ตามฟอร์แมตที่กำหนด"
)


def _contains_leak(text: str) -> bool:
    """True ถ้าเจอร่องรอยเนื้อหาดิบของเอกสารต้นฉบับหลุดเข้ามาในคำตอบ"""
    if not text:
        return False
    return bool(
        _FILE_MARKER_RE.search(text)
        or _YAML_FENCE_RE.search(text)
        or _YAML_BLOCK_RE.search(text)
        or _WIKILINK_RE.search(text)
    )


def _strip_leaked_blocks(text: str) -> str:
    """ท่าสำรองสุดท้าย (best-effort) — ถ้า retry ด้วยพรอมต์เข้มแล้วยังหลุดอีก
    ให้ตัดบล็อกที่หลุดออกด้วยโค้ดตรงๆ แทนที่จะปล่อยให้ผู้ใช้เห็นเนื้อหาดิบ
    """
    cleaned = _YAML_FENCE_RE.sub("", text)
    cleaned = _YAML_BLOCK_RE.sub("", cleaned)
    cleaned = _FILE_MARKER_RE.sub("", cleaned)
    # wikilink → เก็บแค่ข้อความอ่านง่าย (ตัด [[ ]] และเอาเฉพาะส่วนหลัง | ถ้ามี)
    cleaned = _WIKILINK_RE.sub(
        lambda m: m.group(0).strip("[]").split("|")[-1].strip(), cleaned
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


# ── DB loader ──────────────────────────────────────────────────────────────────

def _relevance_score(content: str, path: str, keywords: list[str]) -> int:
    """นับจำนวนคีย์เวิร์ดจากคำถามที่ปรากฏใน note (นับใน path ด้วย × น้ำหนัก)

    ⚠️ ใช้ได้เฉพาะเมื่อมี keywords ที่ "ตัดคำมาแล้ว" — ห้ามป้อนประโยคดิบภาษาไทย
    เพราะภาษาไทยไม่เว้นวรรคระหว่างคำ การ split ด้วยช่องว่างจะได้ประโยคทั้งก้อนเป็น
    คีย์เวิร์ดเดียว ซึ่งไม่มีทางปรากฏตรงตัวในเอกสารไหนเลย → ทุกโน้ตได้ 0 เท่ากันหมด
    (ดู _extract_search_terms ที่ทำหน้าที่ตัดคำให้)
    """
    if not keywords:
        return 0
    lc = content.lower()
    lp = path.lower()
    return sum(lc.count(k) + lp.count(k) * 5 for k in keywords)


# ── Keyword screening ──────────────────────────────────────────────────────────
# คำสามัญที่โผล่ในเอกสารราชการแทบทุกฉบับ — ถ้าปล่อยให้หลุดเข้าไปเป็นคำค้น ตัวกรอง
# จะเลิกกรอง (วัดจริง: คำถามงบประมาณ + คำสามัญ 13 คำ → เข้าเงื่อนไข 908/1280 โน้ต = 71%)
_STOPWORD_TERMS = {
    "ข้อมูล", "การวิเคราะห์", "วิเคราะห์", "แนวโน้ม", "การประเมิน", "ประเมิน",
    "รายงาน", "สถิติ", "การสำรวจ", "สำรวจ", "ผลการดำเนินงาน", "การดำเนินงาน",
    "จังหวัด", "สาธารณสุข", "ปีงบประมาณ", "เอกสาร", "ภาพรวม", "สรุป",
}

# วัดแล้วว่าจุดพอดีอยู่ที่ 5-8 คำ — น้อยกว่านี้ครอบคลุมไม่พอ มากกว่านี้ตัวกรองเริ่มไม่กรอง
_MAX_SEARCH_TERMS = 8

_KEYWORD_PROMPT = """คุณคือผู้เชี่ยวชาญคำศัพท์เอกสารราชการสาธารณสุขไทย
จากคำถามของผู้ใช้ ให้แตกออกเป็น "คำค้น" ที่น่าจะปรากฏตรงตัวในรายงานราชการสาธารณสุข

กติกาเข้ม:
- ตอบเป็น JSON array ของสตริงล้วน ๆ เท่านั้น ห้ามมีข้อความอื่นนอกวงเล็บ
- ให้ 5-8 คำเท่านั้น เรียงจากตรงประเด็นที่สุดไปหาน้อยที่สุด
- ต้องเป็น **ศัพท์เฉพาะทาง** ที่เขียนในเอกสารจริง ยาว 4-20 ตัวอักษร
- ห้ามให้คำสามัญที่โผล่ในเอกสารราชการทุกฉบับ เช่น "ข้อมูล" "รายงาน" "การวิเคราะห์"
  "แนวโน้ม" "สาธารณสุข" "จังหวัด" "ผลการดำเนินงาน" — คำพวกนี้ทำให้ค้นเจอทุกอย่างจนไร้ประโยชน์
- ห้ามใส่ชื่อจังหวัด (ระบบกรองจังหวัดแยกอยู่แล้ว)

คำถาม: {question}"""


def _extract_search_terms(question: str, s) -> list[str]:
    """ให้ LLM ตัดคำถามภาษาไทยออกเป็นคำค้น 5-8 คำ (พร้อมคำพ้อง/ศัพท์เฉพาะทาง)

    ทำไมต้องพึ่ง LLM: ภาษาไทยไม่เว้นวรรค การตัดคำด้วย regex ทำไม่ได้ และการยิงประโยคดิบ
    เข้า pg_trgm/ILIKE ได้ 0 แถวเสมอ (วัดแล้ว) — LLM ยังช่วยแตกคำพ้องให้ด้วย เช่น
    "การควบคุมโรค" → "โรคติดต่อ", "ระบาดวิทยา", "การเฝ้าระวังโรค" ทำให้ครอบคลุมกว้างขึ้นมาก

    คืน [] ถ้าล้มเหลว — ผู้เรียกต้องมี fallback เสมอ (ห้ามโยน exception ออกไป
    เพราะคำถามยังตอบได้แม้การคัดกรองจะพลาด)
    """
    if not question.strip():
        return []
    import litellm  # lazy import แบบเดียวกับ _call_gemini (litellm โหลดช้า)

    try:
        resp = litellm.completion(
            model=f"gemini/{s.GEMINI_MODEL}",
            messages=[{"role": "user", "content": _KEYWORD_PROMPT.format(question=question)}],
            api_key=s.GEMINI_API_KEY,
            temperature=0.1,
            max_tokens=300,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("[fullctx] สกัดคำค้นไม่สำเร็จ (%s) — จะใช้ fallback", exc)
        return []

    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        logger.warning("[fullctx] คำตอบสกัดคำค้นไม่ใช่ JSON array: %r", raw[:200])
        return []
    try:
        items = json.loads(m.group(0))
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("[fullctx] คำตอบสกัดคำค้นแปลง JSON ไม่ได้: %r", m.group(0)[:200])
        return []

    terms: list[str] = []
    for item in items if isinstance(items, list) else []:
        t = str(item).strip()
        # กรองอีกชั้นแม้พรอมต์จะสั่งห้ามไปแล้ว — LLM ไม่ deterministic
        if 4 <= len(t) <= 20 and t not in _STOPWORD_TERMS and t not in terms:
            terms.append(t)
    return terms[:_MAX_SEARCH_TERMS]


# โน้ตนำทาง (INDEX / MOC — Map of Content) เป็นสารบัญลิงก์ ไม่ใช่เนื้อหาต้นทาง
# ถ้าปล่อยให้ถูกเลือกจะกินโควตา context ทั้งที่มีแต่รายชื่อลิงก์ แล้วยังโผล่เป็น
# บรรณานุกรม APA ให้ผู้ใช้อ่าน เช่น "MOC_5ยุทธศาสตร์" ซึ่งอ้างอิงกลับไปไม่ได้จริง
#
# ⚠️ ต้องเขียน %% ไม่ใช่ % — psycopg มองว่า % เป็น placeholder ของพารามิเตอร์
# เขียน % เดี่ยวทำให้ทุกคิวรีที่ใช้ตัวกรองนี้ระเบิดด้วย "IndexError: tuple index
# out of range" = การค้นคลังความรู้พังทั้งระบบแบบเงียบ ๆ (เจอมาแล้ว)
_NAV_NOTE_FILTER = """
          AND coalesce(is_index, false) = false
          AND coalesce(note_type, '') <> 'MOC'
          AND split_part(relative_path, '/', -1) NOT LIKE 'MOC\\_%%'
"""


def _search_notes(vault_id: str, province: str | None, terms: list[str]) -> list[dict]:
    """คัดกรองโน้ตด้วย ILIKE ฝั่ง DB แล้วให้คะแนนตาม "จำนวนคำค้นที่ตรง"

    ใช้ ILIKE ไม่ใช่ similarity() เพราะ pg_trgm similarity เจือจางตามความยาวเอกสาร —
    วัดจริงกับคลังนี้: similarity() คืน 0 แถว ส่วน ILIKE เจอ 57 โน้ตครบทั้ง 5 จังหวัด
    (มี GIN index `idx_obsidian_notes_content_trgm` บน content_stripped รองรับอยู่แล้ว)

    คะแนน = จำนวนคำที่ตรงในเนื้อหา + จำนวนคำที่ตรงในชื่อเรื่อง/path × 5
    (นับเป็น "กี่คำที่ตรง" ไม่ใช่ "ตรงกี่ครั้ง" — กันเอกสารยาวชนะเพราะพูดคำเดิมซ้ำเยอะ)
    """
    if not terms:
        return []
    pattern = "(" + "|".join(re.escape(t) for t in terms) + ")"
    sql = """
        SELECT relative_path, content, file_id, province,
               length(content) AS sz,
               (SELECT count(*) FROM unnest(%s::text[]) t
                 WHERE coalesce(content_stripped, content) ILIKE '%%' || t || '%%') AS c_hits,
               (SELECT count(*) FROM unnest(%s::text[]) t
                 WHERE coalesce(title, '') || relative_path ILIKE '%%' || t || '%%') AS t_hits
        FROM obsidian_notes
        WHERE vault_id = %s AND coalesce(content_stripped, content) ~* %s
    """
    sql += _NAV_NOTE_FILTER
    params: list = [terms, terms, vault_id, pattern]
    if province:
        sql += " AND province = %s"
        params.append(province)

    rows = query_db(sql, tuple(params))
    for r in rows:
        r["score"] = int(r["c_hits"] or 0) + int(r["t_hits"] or 0) * 5
    rows.sort(key=lambda r: -r["score"])
    return rows


def _pack_by_province(rows: list[dict], max_chars: int) -> list[dict]:
    """แพ็กโน้ตให้พอดีเพดาน โดย **แบ่งโควตาตามจังหวัด** ไม่ใช่ "ใครมาก่อนได้ก่อน"

    ⚠️ นี่คือจุดที่พังมาตลอด: เดิมเรียงแล้วใส่ไปเรื่อย ๆ จนเต็ม ซึ่งเมื่อทุกโน้ตคะแนนเท่ากัน
    (เพราะการตัดคำพัง) การเรียงจะกลายเป็นเรียงตามชื่อ path → จังหวัดที่ขึ้นต้นด้วย "ม"
    (มุกดาหาร, 796k ตัวอักษร) กินเพดาน 500k หมดคนเดียว อีก 4 จังหวัดไม่มีวันได้เข้าเลย

    โควตาถ่วงตามคะแนนรวมของแต่ละจังหวัด (จังหวัดที่มีเนื้อหาตรงกว่าได้ส่วนแบ่งมากกว่า)
    แต่มีขั้นต่ำการันตีไว้ ไม่ให้จังหวัดที่มีผลลัพธ์ถูกเบียดจนหายไปทั้งจังหวัด
    """
    if not rows:
        return []

    def size(r: dict) -> int:
        """ขนาดโน้ต — คำนวณจาก content ถ้า DB ไม่ได้ส่ง sz มา (ผู้เรียกบางทางไม่มี)"""
        sz = r.get("sz")
        return int(sz) if sz is not None else len(r.get("content") or "")

    def score(r: dict) -> int:
        return int(r.get("score") or 0)

    by_prov: dict[str, list[dict]] = {}
    for r in rows:
        by_prov.setdefault(r.get("province") or "", []).append(r)
    for lst in by_prov.values():
        lst.sort(key=lambda r: -score(r))

    provs = list(by_prov)
    if len(provs) == 1:
        picked, used = [], 0
        for r in by_prov[provs[0]]:
            if used + size(r) > max_chars and picked:
                break
            picked.append(r)
            used += size(r)
        return picked

    weights = {p: sum(score(r) for r in by_prov[p]) or 1 for p in provs}
    total_w = sum(weights.values())
    # ขั้นต่ำ: ทุกจังหวัดที่มีผลลัพธ์ต้องได้อย่างน้อยครึ่งหนึ่งของส่วนแบ่งเท่า ๆ กัน
    floor = max_chars // (len(provs) * 2)
    quotas = {p: max(floor, int(max_chars * weights[p] / total_w)) for p in provs}

    picked: list[dict] = []
    used = 0
    spent: dict[str, int] = {p: 0 for p in provs}
    for p in sorted(provs, key=lambda p: -weights[p]):
        for r in by_prov[p]:
            if spent[p] + size(r) > quotas[p] or used + size(r) > max_chars:
                continue
            picked.append(r)
            spent[p] += size(r)
            used += size(r)

    # รอบสอง: โควตาที่เหลือของจังหวัดที่ผลน้อย เอาไปแจกต่อตามคะแนน ไม่ปล่อยงบเหลือทิ้ง
    if used < max_chars:
        chosen = {id(r) for r in picked}
        for r in rows:
            if id(r) in chosen or used + size(r) > max_chars:
                continue
            picked.append(r)
            used += size(r)

    # กันเคสที่ทุกจังหวัดมีโน้ตใหญ่เกินโควตาตัวเอง จนไม่มีอะไรผ่านเลย —
    # ต้องส่งอย่างน้อย 1 โน้ตให้ AI เสมอ ดีกว่าส่ง context ว่างเปล่า
    if not picked:
        picked = [max(rows, key=score)]

    return picked


def _load_all_notes(vault_id: str, province: str | None) -> list[dict]:
    """โหลดโน้ตดิบทั้งหมด (ทางสำรองเมื่อการคัดกรองด้วยคำค้นใช้ไม่ได้)"""
    rows = query_db(
        "SELECT relative_path, content, file_id, province, length(content) AS sz "
        "FROM obsidian_notes WHERE vault_id = %s AND province = %s"
        + _NAV_NOTE_FILTER + " ORDER BY relative_path",
        (vault_id, province),
    ) if province else []

    if province and not rows:
        logger.warning("[fullctx] ไม่พบ note ของ '%s' — โหลดทั้ง vault", province)

    if not rows:
        rows = query_db(
            "SELECT relative_path, content, file_id, province, length(content) AS sz "
            "FROM obsidian_notes WHERE vault_id = %s"
            + _NAV_NOTE_FILTER + " ORDER BY relative_path",
            (vault_id,),
        )
    for r in rows:
        r["score"] = 0
    return rows


def _load_vault_context(
    vault_id: str,
    province: str | None,
    question: str = "",
    max_chars: int | None = None,
    terms: list[str] | None = None,
) -> tuple[str, list[str], dict[str, str], dict]:
    """เลือกโน้ตที่เกี่ยวข้องกับคำถามให้พอดีเพดาน context แล้วประกอบเป็นข้อความเดียว

    ทำงาน 2 ขั้น:
      1. **คัดกรอง** — ถ้ามี `terms` (คำค้นที่ตัดคำมาแล้ว จาก _extract_search_terms)
         จะค้นใน DB ด้วย ILIKE + ให้คะแนน · ถ้าไม่มีหรือค้นไม่เจอ ถอยไปโหลดทั้งหมด
      2. **แพ็ก** — แบ่งโควตาตามจังหวัดถ่วงด้วยคะแนน (ดู _pack_by_province)

    ⚠️ ขั้นที่ 2 ทำงาน **ทั้งสองทาง** (ทั้งทางคัดกรองและทางสำรอง) โดยตั้งใจ —
    เพราะอาการ "เห็นแต่จังหวัดเดียว" เกิดจากการแพ็กแบบใครมาก่อนได้ก่อน ไม่ได้เกิดจาก
    การคัดกรองอย่างเดียว ถ้าใส่โควตาเฉพาะทางคัดกรอง ทางสำรองก็จะยังพังเหมือนเดิม

    Returns:
        (context_text, relative_file_paths, minio_id_map, stats)
        stats = {"mode", "candidates", "included", "provinces", "chars", "total_notes"}
    """
    if max_chars is None:
        max_chars = get_settings().OBSIDIAN_MAX_CONTEXT_CHARS

    mode = "keyword"
    rows = _search_notes(vault_id, province, terms or [])
    if not rows:
        mode = "fallback" if terms else "no-terms"
        rows = _load_all_notes(vault_id, province)
        if terms:
            logger.warning(
                "[fullctx] คำค้น %s ไม่เจอโน้ตเลย — ถอยไปโหลดทั้งหมด", terms,
            )

    picked = _pack_by_province(rows, max_chars)

    parts: list[str] = []
    file_paths: list[str] = []
    minio_id_map: dict[str, str] = {}
    # จังหวัดรายโน้ต — จำเป็นเมื่อผู้ใช้ไม่ระบุจังหวัด เพราะผลลัพธ์จะคละหลายจังหวัด
    # ถ้าไม่เก็บไว้ ป้ายอ้างอิงที่โชว์ผู้ใช้จะไม่มีจังหวัดกำกับเลย (ดู run_obsidian_ask_fullcontext)
    province_by_path: dict[str, str] = {}
    total = 0

    for r in picked:
        content = (r["content"] or "").strip()
        if not content:
            continue
        rel = r["relative_path"]
        block = f"\n\n---\n## FILE: {rel}\n\n{content}"
        parts.append(block)
        file_paths.append(rel)
        if r.get("file_id"):
            minio_id_map[rel] = r["file_id"]
        if r.get("province"):
            province_by_path[rel] = r["province"]
        total += len(block)

    provinces = sorted({province_by_path[p] for p in file_paths if p in province_by_path})
    stats = {
        "mode": mode,
        "candidates": len(rows),
        "included": len(file_paths),
        "provinces": provinces,
        "province_by_path": province_by_path,
        "chars": total,
        "terms": terms or [],
    }

    logger.info(
        "[fullctx] โหมด=%s คัดกรองได้ %d โน้ต → ส่งให้ AI %d โน้ต (%d chars, cap=%d, "
        "จังหวัด=%s, vault=%s, province=%s, terms=%s)",
        mode, len(rows), len(file_paths), total, max_chars,
        ",".join(provinces) or "-", vault_id, province, terms,
    )

    return "\n".join(parts), file_paths, minio_id_map, stats


# ── Gemini call ────────────────────────────────────────────────────────────────

def _call_gemini(
    system: str,
    user_message: str,
    s,
    on_delta: Callable[[str], None] | None = None,
) -> str:
    """เรียก Gemini Pro ผ่าน litellm (dependency ของ crewai).

    on_delta: ถ้าระบุ จะสตรีมคำตอบทีละ token ผ่าน callback นี้แบบเรียลไทม์
    (ลด perceived latency ของคำถามที่ใช้เวลานาน ~50-60s) — มีการ์ดกันเนื้อหาดิบ
    หลุดออกไปสด ๆ ระหว่างสตรีมด้วย: เช็คเฉพาะ "หาง" ของบัฟเฟอร์ที่โตขึ้นทุกครั้ง
    ถ้าเจอร่องรอยเนื้อหาดิบ (FILE:/YAML/wikilink) จะหยุดส่งสดทันที (แต่ยังสะสม
    ข้อความในหน่วยความจำต่อจนจบ เพื่อให้ตัวตรวจสอบระดับบนสุดใน
    run_obsidian_ask_fullcontext ทำ retry/cleanup ได้ตามปกติ — ผู้ใช้จะไม่เห็น
    เนื้อหาดิบเป็นคำตอบสุดท้ายไม่ว่ากรณีใด)
    """
    import litellm

    os.environ.setdefault("GEMINI_API_KEY", s.GEMINI_API_KEY)
    os.environ.setdefault("GOOGLE_API_KEY", s.GEMINI_API_KEY)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]

    if on_delta is None:
        resp = litellm.completion(
            model=f"gemini/{s.GEMINI_MODEL_PRO}",
            messages=messages,
            api_key=s.GEMINI_API_KEY,
            max_tokens=s.REPORT_MAX_TOKENS,
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""

    parts: list[str] = []
    forwarding = True
    stream = litellm.completion(
        model=f"gemini/{s.GEMINI_MODEL_PRO}",
        messages=messages,
        api_key=s.GEMINI_API_KEY,
        max_tokens=s.REPORT_MAX_TOKENS,
        temperature=0.2,
        stream=True,
    )
    for chunk in stream:
        delta = ""
        if chunk.choices and chunk.choices[0].delta:
            delta = chunk.choices[0].delta.content or ""
        if not delta:
            continue
        parts.append(delta)
        if forwarding:
            tail = "".join(parts)[-_LEAK_CHECK_WINDOW:]
            if _contains_leak(tail):
                # หยุดส่งสดตั้งแต่ตอนที่พบร่องรอยแรก — กันไม่ให้ผู้ใช้เห็นเนื้อหา
                # ดิบไหลเข้าจอระหว่างสตรีม ส่วนที่เหลือของคำตอบจะถูกจัดการที่ระดับ
                # guard/retry ของ run_obsidian_ask_fullcontext แทน
                forwarding = False
                logger.warning("[fullctx] พบร่องรอยเนื้อหาดิบระหว่างสตรีม — หยุดส่งสด")
            else:
                on_delta(delta)

    return "".join(parts)


# ── Note-reference title cleanup ────────────────────────────────────────────────
# ไฟล์ PDF ต้นฉบับที่ยาวจะถูกตัดแบ่งเป็นหลาย .md "ส่วน" ตอน ingest (เช่น
# "...-2567-ส่วนที่01", "...-ส่วนที่02", ..., "...-INDEX") — ทุกส่วนของเอกสารเดียวกัน
# จะชี้ minio file_id เดียวกัน ต้องตัดคำต่อท้ายออกเพื่อโชว์เป็นชื่อเอกสารต้นฉบับเดียว
_PART_SUFFIX_RE = re.compile(r"[-_](?:ส่วนที่\s*\d+|part\s*\d+|INDEX)$", re.IGNORECASE)


def _clean_doc_title(stem: str) -> str:
    return _PART_SUFFIX_RE.sub("", stem).strip() or stem


# ── Follow-up extractor (structured, ไม่ใช่ regex เดาจาก markdown headers) ──────

_FOLLOWUP_BLOCK_RE = re.compile(
    r"<<<FOLLOWUPS>>>\s*(.*?)\s*<<<END_FOLLOWUPS>>>", re.DOTALL
)


def _extract_and_strip_followups(text: str) -> tuple[str, list[str]]:
    """ดึง follow_ups จากบล็อก JSON ที่บังคับให้ LLM ปิดท้ายคำตอบด้วยเสมอ แล้วตัด
    บล็อกนั้นออกจาก content ก่อนส่งให้ผู้ใช้เห็น

    เดิม _extract_follow_ups ใช้ regex เดาว่าอะไรคือ "รายการเลขข้อ" ในคำตอบทั้งก้อน
    ซึ่งไปจับเอาหัวข้อ markdown ของคำตอบเอง (เช่น "1. **สรุปคำตอบ**") มาแสดงเป็น
    ปุ่มคำถามแนะนำผิด ๆ — ตอนนี้ใช้ JSON block ที่ระบุตำแหน่งชัดเจนแทน จึงไม่มีทาง
    หยิบข้อความอื่นมาปนได้ และมีการกรองรูปแบบซ้ำอีกชั้นก่อน return
    """
    m = _FOLLOWUP_BLOCK_RE.search(text)
    if not m:
        return text.strip(), []

    content = (text[: m.start()] + text[m.end():]).strip()
    raw = m.group(1).strip()
    follow_ups: list[str] = []
    try:
        items = json.loads(raw)
        if isinstance(items, list):
            for item in items:
                q = str(item).strip()
                # ต้องเป็นประโยคคำถามสั้น ๆ ที่ลงท้ายด้วย "?" เท่านั้น และห้ามมี
                # markdown syntax หลุดมา (กันเคสหัวข้อ **...** ปนเข้ามา)
                if q and q.endswith("?") and 5 < len(q) <= 160 and "**" not in q and "\n" not in q:
                    follow_ups.append(q)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("[fullctx] follow_ups block ไม่ใช่ JSON ที่ถูกต้อง: %r", raw[:200])

    return content, follow_ups[:3]


# ── Public entry point ─────────────────────────────────────────────────────────

def run_obsidian_ask_fullcontext(
    question: str,
    province: str = "",
    vault_id: str = "health_region_10",
    request_id: str | None = None,
    history_context: str = "",
    on_delta: Callable[[str], None] | None = None,
) -> ObsidianAskResponse:
    """Full context pipeline — โหลด .md ทั้งหมด → Gemini context window โดยตรง.

    history_context: ข้อความสรุปประวัติการสนทนาก่อนหน้า (จาก build_history_context)
    — แนบไปกับคำถามให้ Gemini เห็นบทสนทนาที่ผ่านมา เพื่อให้ตอบคำถามต่อเนื่อง
    (follow-up) ได้อย่างเป็นธรรมชาติแบบ Gemini/ChatGPT แทนที่จะเริ่มนับหนึ่งใหม่
    ทุกครั้งที่ถามต่อ (ดูคอมเมนต์ใน _orchestrate ของ analyze.py ที่
    build_history_context ถูกสร้างขึ้น แล้วส่งต่อมาที่นี่)

    on_delta: callback รับ token สด ๆ ระหว่างสตรีมคำตอบ (ดู _call_gemini) — ใช้ลด
    perceived latency ของคำถามที่กิน ~50-60s ผู้เรียก (analyze.py) ส่ง callback ที่
    ยิง SSE event "obsidian_chunk" กลับไปอัปเดตแผงสถานะฝั่งหน้าจอแบบเรียลไทม์
    """
    start = time.time()
    s = get_settings()

    emit_progress(request_id, "🔎 Keyword Screener", "running", "กำลังแตกคำค้นจากคำถาม...")
    terms = _extract_search_terms(question, s)
    emit_progress(request_id, "🔎 Keyword Screener", "done",
                  f"คำค้น: {' · '.join(terms)}" if terms
                  else "แตกคำค้นไม่สำเร็จ — จะคัดจากทั้งคลังแทน")

    emit_progress(request_id, "📂 Context Loader", "running",
                  f"กำลังโหลดเอกสาร{f' จังหวัด{province}' if province else 'ทั้ง vault'}...")

    try:
        context_text, file_paths, minio_id_map, stats = _load_vault_context(
            vault_id, province or None, question=question, terms=terms
        )

        load_elapsed = round(time.time() - start, 1)
        # ⚠️ บอกขอบเขตที่ "อ่านจริง" ให้ผู้ใช้เห็นเสมอ — คลังใหญ่กว่าเพดาน context หลายเท่า
        # (8.4M vs 500k ตัวอักษร) ระบบจึงอ่านได้แค่บางส่วนทุกครั้ง ถ้าไม่บอก ผู้ใช้จะเข้าใจว่า
        # คำตอบ "ไม่พบข้อมูล" แปลว่าคลังไม่มีข้อมูล ทั้งที่จริงคือยังไม่ได้อ่านส่วนนั้น
        emit_progress(
            request_id, "📂 Context Loader", "done",
            f"คัดจาก {stats['candidates']} โน้ต → อ่านเต็ม {len(file_paths)} โน้ต "
            f"({', '.join(stats['provinces']) or '-'}) ({load_elapsed}s)",
            load_elapsed,
        )

        emit_progress(request_id, "🤖 Gemini Answer Writer", "running",
                      "กำลังวิเคราะห์เอกสารและเขียนคำตอบ...")

        prov_label = province or "ทุกจังหวัดในเขตสุขภาพที่ 10"
        # ⚠️ ต่อ "ความจำการสนทนา" เข้า user_message — ไม่งั้นทุกคำถามตามหลัง
        # (follow-up) จะถูกตอบแบบเริ่มนับหนึ่งใหม่ทุกครั้ง ไม่ต่อเนื่องแบบ
        # Gemini/ChatGPT (ใช้รูปแบบเดียวกับ history_section ใน csv_pipeline.py /
        # accident_chat_orchestrator.py — ตรงกับที่ผู้ใช้ขอให้ "ส่ง context history
        # ไปให้ AI ไปด้วย")
        history_section = f"{history_context}\n\n" if history_context else ""
        user_message = (
            f"{history_section}"
            f"**เอกสารจาก Obsidian Knowledge Vault ({prov_label}):**\n"
            f"{context_text}\n\n"
            f"---\n**คำถาม:** {question}\n\n"
            "(ถ้ามี \"ประวัติการสนทนาก่อนหน้า\" แนบมาด้านบน ให้ตอบต่อแบบบทสนทนาจริง "
            "ตามแนวทางในคำสั่งระบบ — ต่อยอดจากที่เคยตอบไปแล้ว ไม่ใช่เริ่มอธิบายใหม่ทั้งหมด)"
        )

        raw_answer = _call_gemini(SYSTEM_PROMPT, user_message, s, on_delta=on_delta)
        answer, follow_ups = _extract_and_strip_followups(raw_answer)
        answer = dedupe_repeated_answer(answer)

        # ── Output guard: กันเนื้อหาดิบของเอกสารต้นฉบับหลุดเข้าคำตอบ ─────────
        # (เคยเจอจริง: คำถามกว้าง ๆ อย่าง "มีเอกสารอะไรบ้าง" ทำให้ LLM คัดลอกบล็อก
        # "## FILE: ..." พร้อม YAML frontmatter + wikilink ทั้งดุ้นมาแปะในคำตอบ)
        if _contains_leak(answer):
            logger.warning(
                "[fullctx] ตรวจพบเนื้อหาดิบหลุดในคำตอบ (province=%s) — retry ด้วยพรอมต์ที่เข้มขึ้น",
                province,
            )
            retry_raw = _call_gemini(
                SYSTEM_PROMPT, user_message + _LEAK_RETRY_SUFFIX, s, on_delta=None,
            )
            retry_answer, retry_follow_ups = _extract_and_strip_followups(retry_raw)
            retry_answer = dedupe_repeated_answer(retry_answer)
            if not _contains_leak(retry_answer):
                answer, follow_ups = retry_answer, (retry_follow_ups or follow_ups)
            else:
                logger.error(
                    "[fullctx] เนื้อหาดิบยังหลุดหลัง retry (province=%s) — ตัดออกเองแบบ best-effort",
                    province,
                )
                answer = _strip_leaked_blocks(answer)
                follow_ups = follow_ups or retry_follow_ups

        elapsed = round(time.time() - start, 1)
        emit_progress(request_id, "🤖 Gemini Answer Writer", "done",
                      f"เขียนคำตอบเสร็จ ({elapsed}s)", elapsed)

        # เอกสารต้นฉบับหนึ่งไฟล์ที่ถูกตัดแบ่งเป็นหลาย .md "ส่วน" (ระหว่าง ingest PDF)
        # จะมีหลาย path ใน file_paths แต่ชี้ minio file_id เดียวกัน — dedupe ตรงนี้
        # เพื่อให้อ้างอิงที่โชว์ผู้ใช้เป็น "1 เอกสาร = 1 ลิงก์" ไม่ใช่โผล่ซ้ำเป็น 15-20
        # ป้ายของทุกส่วนย่อย (ส่วนที่ไม่มี PDF ผูกอยู่ ใช้ path ของตัวเองกันซ้ำแทน)
        note_refs: list[ObsidianNoteRef] = []
        seen_dedup_keys: set[str] = set()
        for p in file_paths:
            file_id = minio_id_map.get(p)
            dedup_key = file_id or p
            if dedup_key in seen_dedup_keys:
                continue
            seen_dedup_keys.add(dedup_key)
            note_refs.append(ObsidianNoteRef(
                note_id=p.replace("/", "::"),
                title=_clean_doc_title(Path(p).stem),
                # จังหวัดของโน้ตตัวนั้นจริง ๆ ก่อน แล้วค่อย fallback เป็นจังหวัดที่ผู้ใช้ระบุ —
                # เมื่อไม่ระบุจังหวัด ผลลัพธ์จะคละหลายจังหวัด การใช้ค่าระดับ request อย่างเดียว
                # ทำให้ป้ายอ้างอิงไม่มีจังหวัดกำกับเลยทั้งที่ข้อมูลมีอยู่
                province=stats.get("province_by_path", {}).get(p) or province or None,
                district=None,
                pdf_url=f"/api/pdf/view/{file_id}" if file_id else None,
            ))
            if len(note_refs) >= 15:
                break

        return ObsidianAskResponse(
            content=answer,
            notes_referenced=note_refs,
            follow_ups=follow_ups,
            metadata={
                "pipeline": "obsidian_fullcontext",
                "vault_id": vault_id,
                "province": province or "all",
                "files_loaded": len(file_paths),
                "elapsed_seconds": elapsed,
                # ── ขอบเขตที่ "อ่านจริง" — ผู้เรียกต้องเอาไปแสดงให้ผู้ใช้เห็น ──────────
                # คลังใหญ่กว่าเพดาน context หลายเท่า (8.4M vs 500k ตัวอักษร) ระบบจึงอ่านได้
                # แค่บางส่วนทุกครั้ง ถ้าไม่บอก ผู้ใช้จะเข้าใจว่าคำตอบ "ไม่พบข้อมูล" แปลว่า
                # คลังไม่มีข้อมูล ทั้งที่จริงคือยังไม่ได้อ่านส่วนนั้น
                # ⚠️ ห้ามใช้ emit_progress ส่งข้อมูลนี้ — มันเขียนลงคิวที่ผูกกับ request_id
                # ซึ่งโหมดแชท (analyze.py) ไม่ได้ส่งมา ข้อความจึงถูกทิ้งเงียบ ๆ
                "coverage": {
                    "mode": stats.get("mode"),
                    "candidates": stats.get("candidates"),
                    "included": stats.get("included"),
                    "provinces": stats.get("provinces", []),
                    "terms": stats.get("terms", []),
                },
            },
        )

    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        emit_progress(request_id, "🤖 Gemini Answer Writer", "error", str(exc)[:120], elapsed)
        logger.exception("[fullctx] ล้มเหลว: %s", exc)
        return ObsidianAskResponse(
            content=f"เกิดข้อผิดพลาด: {exc}",
            notes_referenced=[],
            follow_ups=[],
            metadata={
                "error": str(exc),
                "pipeline": "obsidian_fullcontext",
                "elapsed_seconds": elapsed,
            },
        )
