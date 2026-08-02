"""Research Planner — ให้ Agent วางแผนค้นข้อมูลเอง ก่อนลงมือค้น

ปัญหาเดิม: โหมด `report-gather` ยิงคำถาม **ก้อนเดียวกันเป๊ะ** ใส่ทั้ง 5 แหล่ง
⇒ ส่งประโยคไทย *"จัดทำแผนปฏิบัติงาน 1 ปี ลดอุบัติเหตุ..."* เข้า PubMed ตรง ๆ
ซึ่งเป็น **คำสั่งสร้างเอกสาร ไม่ใช่คำค้นงานวิจัย** และเรียกแต่ละแหล่งได้ครั้งเดียว
ทั้งที่แผนปฏิบัติงานต้องการตัวเลขหลายชุด

ตัวนี้ให้ LLM อ่านโจทย์แล้วคืน "แผนค้น" เป็น JSON — เลือกเครื่องมือเอง ตั้งคำค้นเอง
และ **เรียกเครื่องมือเดิมซ้ำได้หลายครั้ง** ด้วยคำค้นต่างกัน

⚠️ ห้ามเชื่อ JSON จาก LLM ตรง ๆ — ต้องผ่าน `normalize_plan()` เสมอ
ตลอดงานรอบนี้พบว่าโมเดล**แต่งชื่อชุดข้อมูล**และ**หยิบคอลัมน์ผิด**มาแล้ว
สมมติฐานว่า "โมเดลทำตามที่สั่ง" พิสูจน์แล้วว่าเชื่อไม่ได้
"""
from __future__ import annotations

import json
import logging
import os
import re

import litellm

from src.tools.error_logger import log_agent_error

logger = logging.getLogger(__name__)

TOOLS = ("obsidian", "stats", "thaijo", "pubmed", "tavily")

MIN_STEPS, MAX_STEPS = 3, 8
MAX_QUERY_LEN = 200

# คลังรายงานต้องถูกค้นทุกครั้ง 2 มุม — ถ้า planner ไม่ใส่มา ระบบเติมให้เอง
_REQUIRED_OBSIDIAN = 2

_SYSTEM = "คุณคือนักวางแผนค้นคว้าข้อมูลสาธารณสุข ตอบเป็น JSON เท่านั้น"

_PROMPT = """โจทย์: "{prompt}"

คุณมีเครื่องมือค้นข้อมูล 5 ตัว:
- `obsidian` = คลังรายงานของเขตสุขภาพที่ 10 (รายงานตรวจราชการ แผนงาน นโยบายพื้นที่ 1,545 โน้ต)
- `stats`    = ข้อมูลสถิติ (CSV 300+ ไฟล์ + ฐานข้อมูลอุบัติเหตุ) — ถามเป็นภาษาไทย เจาะจงพื้นที่/ปี
- `thaijo`   = งานวิจัยไทย — คำค้นภาษาไทย สั้น เน้นคำสำคัญ
- `pubmed`   = งานวิจัยสากล — **คำค้นภาษาอังกฤษเท่านั้น** ใช้ศัพท์ทางการแพทย์
- `tavily`   = ค้นเว็บ — สำหรับนโยบายล่าสุด กฎหมาย งบประมาณ ที่ระบบไม่มี

วางแผนค้นข้อมูล {min_steps}-{max_steps} ขั้น เพื่อให้ได้หลักฐานครบสำหรับตอบโจทย์นี้

กฎ:
1. **ต้องมี `obsidian` อย่างน้อย 2 ขั้นเสมอ** — หนึ่งขั้นหาบริบทพื้นที่ (จังหวัด/อำเภอในโจทย์)
   อีกขั้นหานโยบายระดับเขตสุขภาพที่ 10 และระดับประเทศ
   เพราะแผนราชการต้องสอดคล้องกับสิ่งที่พื้นที่ทำอยู่จริงและนโยบายต้นสังกัด
2. **เรียกเครื่องมือเดิมซ้ำได้** ถ้าต้องการข้อมูลหลายชุด เช่น `stats` 3 ขั้นสำหรับตัวเลขคนละเรื่อง
3. **ตั้งคำค้นให้เหมาะกับแต่ละเครื่องมือ** ห้ามคัดลอกโจทย์ทั้งก้อนไปใส่ทุกช่อง
   - `stats` ต้องเป็นคำถามที่ตอบด้วยตัวเลขได้ ระบุพื้นที่และปี
   - `pubmed` ต้องเป็นภาษาอังกฤษ
4. คำค้นแต่ละขั้นสั้น ไม่เกิน 150 ตัวอักษร
5. ไม่ต้องใช้ครบทุกเครื่องมือ ถ้าตัวไหนไม่เกี่ยวก็ข้ามได้

ตอบเป็น JSON array เท่านั้น ห้ามมีข้อความอื่น:
[{{"tool":"obsidian","query":"...","purpose":"บริบทพื้นที่"}}, ...]"""


def _fallback_plan(prompt: str) -> list[dict]:
    """แผนสำรอง = พฤติกรรมเดิม (ยิงทุกแหล่งด้วยโจทย์เดียว) + คลังรายงาน 2 มุม

    ใช้เมื่อ planner ล้ม — ห้ามทำให้ฟีเจอร์ที่ใช้ได้อยู่แล้วพังเพราะของใหม่
    """
    return [
        {"tool": "obsidian", "query": prompt, "purpose": "บริบทพื้นที่"},
        {"tool": "obsidian", "query": f"นโยบายและแผนระดับเขตสุขภาพที่ 10 {prompt[:60]}",
         "purpose": "นโยบายเขต/ประเทศ"},
        {"tool": "stats", "query": prompt, "purpose": "ข้อมูลสถิติ"},
        {"tool": "thaijo", "query": prompt, "purpose": "งานวิจัย"},
        {"tool": "pubmed", "query": prompt, "purpose": "งานวิจัยสากล"},
        {"tool": "tavily", "query": prompt, "purpose": "ข้อมูลจากเว็บ"},
    ]


def normalize_plan(raw: object, prompt: str) -> list[dict]:
    """ทำให้แผนใช้งานได้จริง — **ห้ามข้ามขั้นนี้ ไม่ว่าแผนจะมาจากไหน**

    การ์ดทุกข้อมาจากพฤติกรรมที่เจอจริงของโมเดลระหว่างงานรอบนี้:
      - แต่งชื่อเครื่องมือที่ไม่มีอยู่  ⇒ กรองด้วย TOOLS
      - คัดลอกโจทย์ทั้งก้อนมาเป็นคำค้น ⇒ ตัดที่ MAX_QUERY_LEN
      - ลืมกฎที่สั่งไว้ในพรอมต์        ⇒ เติม obsidian ให้เองถ้าขาด
    """
    steps: list[dict] = []
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            tool = str(it.get("tool", "")).strip().lower()
            query = str(it.get("query", "")).strip()
            if tool not in TOOLS or not query:
                continue
            steps.append({
                "tool": tool,
                "query": query[:MAX_QUERY_LEN],
                "purpose": str(it.get("purpose", "")).strip()[:60] or tool,
            })

    # ⚠️ ต้องเช็ค "ว่างหรือไม่" ก่อนเติม obsidian — ไม่งั้นการเติมจะทำให้ steps
    # ไม่ว่างเสมอ แล้วแผนสำรองจะไม่มีวันทำงาน (เจอตอนเขียนเทสต์)
    if not steps:
        return _fallback_plan(prompt)

    # ── บังคับค้นคลังรายงาน 2 มุมเสมอ ────────────────────────────────────
    # แผนราชการที่ไม่อ้างเอกสารพื้นที่/นโยบายต้นสังกัด ใช้จริงไม่ได้
    # ต่อให้เนื้อหาดีแค่ไหน ⇒ บังคับด้วยโค้ด ไม่ใช่แค่พรอมต์
    n_obs = sum(1 for s in steps if s["tool"] == "obsidian")
    if n_obs < _REQUIRED_OBSIDIAN:
        need = [
            {"tool": "obsidian", "query": prompt[:MAX_QUERY_LEN],
             "purpose": "บริบทพื้นที่"},
            {"tool": "obsidian",
             "query": f"นโยบายและแผนระดับเขตสุขภาพที่ 10 และระดับประเทศ {prompt[:80]}",
             "purpose": "นโยบายเขต/ประเทศ"},
        ]
        steps = need[: _REQUIRED_OBSIDIAN - n_obs] + steps

    return steps[:MAX_STEPS]


def plan_research(prompt: str, gemini_key: str = "") -> list[dict]:
    """คืนแผนค้นข้อมูล — ล้มเมื่อไรก็ถอยไปใช้แผนสำรอง ไม่โยน exception"""
    key = gemini_key or os.getenv("GEMINI_API_KEY", "")
    if not key or not prompt.strip():
        return normalize_plan([], prompt)
    try:
        resp = litellm.completion(
            model="gemini/gemini-2.5-flash-lite",
            api_key=key,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _PROMPT.format(
                    prompt=prompt, min_steps=MIN_STEPS, max_steps=MAX_STEPS)},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        text = (resp.choices[0].message.content or "").strip()
        # โมเดลชอบห่อ JSON ด้วย ```json ... ``` แม้สั่งห้ามแล้ว
        m = re.search(r"\[.*\]", text, re.S)
        raw = json.loads(m.group(0)) if m else []
        return normalize_plan(raw, prompt)
    except Exception as exc:
        log_agent_error(str(exc), agent_name="Research Planner",
                        step="plan", domain="", prompt=prompt[:120])
        return normalize_plan([], prompt)
