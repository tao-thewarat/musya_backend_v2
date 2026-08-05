"""ด่านคัดบทความที่ "คนละเรื่อง" ออกจากผลค้นงานวิจัย (ThaiJo / PubMed)

ที่มา — ผู้ใช้จับได้:
  ถาม "มีหลักฐานเชิงประจักษ์อะไรบ้างว่ามาตรการลดหวานมันเค็มระดับชุมชนได้ผล"
  ผลที่ได้มีบทความ **"การรุกล้ำความเค็มและมาตรการควบคุมความเค็มในแม่น้ำท่าจีน"** ติดมาด้วย
  ⇒ ตรงคำว่า "ความเค็ม" + "มาตรการ" ทางคำ แต่เป็น *ความเค็มของน้ำทะเลหนุน*
    ไม่ใช่ *ความเค็มของอาหาร* — คนละโดเมนโดยสิ้นเชิง

ทำไมกรองที่ชั้นนี้ไม่ใช่ที่คำค้น: ThaiJo/PubMed จับคู่ด้วยคำ ไม่ได้เข้าใจความหมาย
ต่อให้ตั้งคำค้นดีแค่ไหน คำพ้องบริบท (เค็ม/หวาน/มัน/ควบคุม/มาตรการ) ก็ยังลากของ
คนละสาขาเข้ามาได้เสมอ จึงต้องมีด่านอ่าน "ความหมาย" หลังได้ผลลัพธ์แล้ว

⚠️ หลักการเดียวกับ Relevance Verifier ของท่อ CSV (ดู Agents/02 ในคลัง SRS):
**เอนไปทาง "เก็บไว้" เสมอ** — ตัดทิ้งเฉพาะที่ *คนละเรื่องอย่างสิ้นเชิง* เท่านั้น
บทความที่เกี่ยวแบบอ้อม ๆ (คนละพื้นที่ คนละกลุ่มอายุ วิธีวิจัยต่างกัน) ต้องเก็บไว้
เพราะงานวิจัยที่ทำคนละจังหวัดก็ใช้อ้างอิงได้ การตัดเกินคือความเสียหายที่ผู้ใช้เห็นยากกว่า
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import litellm

from src.tools.error_logger import log_agent_error

logger = logging.getLogger(__name__)

MODEL = "gemini/gemini-2.5-flash-lite"

# ตัดบทคัดย่อก่อนส่งเข้าโมเดล — ตัดสิน "คนละเรื่องไหม" ใช้แค่ชื่อเรื่อง + ต้นบทคัดย่อก็พอ
# และกันไม่ให้ 10 บทความยาว ๆ ดันพรอมต์บวมจนโมเดลเริ่มมองข้ามคำสั่ง
_ABSTRACT_CHARS = 400
_MAX_ARTICLES = 20

_SYSTEM = "คุณเป็นบรรณารักษ์วิชาการที่คัดกรองบทความให้ตรงหัวข้อ ตอบเป็น JSON เท่านั้น"

_PROMPT_TMPL = """คำถามของผู้ใช้: "{prompt}"

ด้านล่างคือบทความที่ระบบค้นมาได้ ให้ตัดสินทีละรายการว่า
**เป็นเรื่องเดียวกับที่ผู้ใช้ถามหรือไม่**

{articles}

เกณฑ์ตัดสิน:
- `keep: true`  = เกี่ยวข้อง แม้จะเกี่ยวแบบอ้อม ๆ ก็ตาม
  (คนละจังหวัด คนละกลุ่มอายุ คนละวิธีวิจัย คนละช่วงปี → ยังถือว่าเกี่ยวข้อง เก็บไว้)
- `keep: false` = **คนละเรื่องอย่างสิ้นเชิง** ตรงกันแค่ตัวอักษรของคำ แต่คนละความหมาย/คนละสาขา

ตัวอย่างที่ต้องตัดทิ้ง (เกิดขึ้นจริง):
  ถาม "มาตรการลดหวานมันเค็มระดับชุมชนได้ผลหรือไม่"
  บทความ "การรุกล้ำความเค็มและมาตรการควบคุมความเค็มในแม่น้ำท่าจีน"
  → keep: false เพราะเป็น *ความเค็มของน้ำจากทะเลหนุน* (อุทกวิทยา/สิ่งแวดล้อม)
    ไม่ใช่ *ความเค็มของอาหาร* (โภชนาการ/สาธารณสุข) — ตรงกันแค่คำว่า "เค็ม"

กฎเหล็ก:
1. **ถ้าลังเล ให้ตอบ keep: true** — การตัดบทความที่เกี่ยวข้องทิ้งเสียหายกว่า
2. ตอบให้ครบทุกหมายเลข ห้ามข้าม
3. `reason` เขียนสั้น ๆ ภาษาไทย ไม่เกิน 20 คำ และจำเป็นเฉพาะตอน keep: false

ตอบเป็น JSON array เท่านั้น ห้ามมี markdown หรือ ```:
[{{"i": 1, "keep": true, "reason": ""}}, {{"i": 2, "keep": false, "reason": "..."}}]"""


def _first_line(text: str) -> str:
    """ดึงชื่อเรื่องออกจาก summary ของ ThaiJo ที่เก็บเป็น '**ชื่อเรื่อง**\\n\\nบทคัดย่อ'."""
    for line in (text or "").splitlines():
        stripped = line.strip().strip("*").strip()
        if stripped:
            return stripped
    return ""


def article_brief(article: dict, index: int) -> str:
    """ย่อบทความให้เหลือเท่าที่ใช้ตัดสิน — รองรับทั้งรูปแบบ ThaiJo และ PubMed

    ThaiJo เก็บชื่อเรื่องปนอยู่ใน `summary` ส่วน PubMed แยก `title`/`abstract` ชัดเจน
    ฟังก์ชันนี้จึงต้องรับได้ทั้งสองแบบ ไม่งั้นต้องเขียนตัวกรองซ้ำสองชุด
    """
    title = (article.get("title") or _first_line(article.get("summary", "")) or "").strip()
    abstract = (article.get("abstract") or "").strip()
    if not abstract:
        summary = (article.get("summary") or "").strip()
        # ตัดบรรทัดชื่อเรื่องออก เหลือเฉพาะเนื้อบทคัดย่อ
        abstract = summary[len(_first_line(summary)):].strip(" *\n")
    abstract = re.sub(r"\s+", " ", abstract)[:_ABSTRACT_CHARS]
    return f"[{index}] ชื่อเรื่อง: {title or '(ไม่มีชื่อเรื่อง)'}\n    บทคัดย่อ: {abstract or '(ไม่มีบทคัดย่อ)'}"


def _extract_json_array(text: str) -> Any:
    """ดึง JSON array ออกจากคำตอบโมเดล (มักห่อมาด้วย ``` หรือมีคำอธิบายนำหน้า)."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def parse_verdicts(raw: Any, total: int) -> dict[int, str]:
    """แปลงคำตอบโมเดลเป็น {index (เริ่มที่ 1): เหตุผลที่ตัดทิ้ง}

    คืนเฉพาะรายการที่ "สั่งตัดทิ้งอย่างชัดเจน" — ทุกกรณีที่ตีความไม่ได้แปลว่าเก็บไว้:
      - ไม่ใช่ list           ⇒ ไม่ตัดใคร
      - index เพี้ยน/นอกช่วง  ⇒ ข้าม
      - keep ไม่ใช่ boolean   ⇒ ข้าม (โมเดลเคยตอบ "no" เป็น string มาแล้ว จึงรับ str ด้วย)
    """
    dropped: dict[int, str] = {}
    if not isinstance(raw, list):
        return dropped

    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("i"))
        except (TypeError, ValueError):
            continue
        if not 1 <= idx <= total:
            continue

        keep = item.get("keep")
        if isinstance(keep, str):
            keep = keep.strip().lower() not in ("false", "no", "0")
        if keep is not False:
            continue

        reason = str(item.get("reason", "")).strip()[:120] or "ไม่ตรงกับหัวข้อที่ถาม"
        dropped[idx] = reason

    return dropped


def _judge(prompt: str, articles: list[dict], api_key: str) -> dict[int, str]:
    briefs = "\n\n".join(article_brief(a, i) for i, a in enumerate(articles, 1))
    resp = litellm.completion(
        model=MODEL,
        api_key=api_key,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _PROMPT_TMPL.format(prompt=prompt, articles=briefs)},
        ],
        temperature=0,
    )
    text = resp.choices[0].message.content or ""
    return parse_verdicts(_extract_json_array(text), len(articles))


def filter_relevant_articles(
    prompt: str,
    articles: list[dict],
    source: str = "thaijo",
    api_key: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """คัดบทความที่คนละเรื่องออก — คืน (ที่เก็บไว้, ที่ตัดทิ้งพร้อมเหตุผล)

    บทความที่ถูกตัดจะได้คีย์ `drop_reason` เพิ่มเข้ามา เพื่อให้ผู้ใช้เห็นว่า
    ระบบตัดอะไรไปเพราะอะไร — ตัดเงียบ ๆ แล้วผู้ใช้เห็นแค่ตัวเลขบทความหายไป
    จะกลายเป็นบั๊กที่ debug ไม่ได้

    ล้มเมื่อไหร่ = คืนของเดิมทั้งหมด ฟีเจอร์ที่ใช้ได้อยู่แล้วต้องไม่พังเพราะของใหม่
    """
    if not articles or not prompt.strip():
        return articles, []

    key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return articles, []

    subset = articles[:_MAX_ARTICLES]
    try:
        dropped = _judge(prompt, subset, key)
        # ── safety net: ตัดหมดเกลี้ยง = น่าสงสัยว่าด่านเพี้ยน ไม่ใช่ผลค้นห่วย ───────
        # ถามซ้ำอีกรอบ (แบบเดียวกับ Relevance Verifier ของท่อ CSV) แล้วเชื่อผลที่
        # "ตัดน้อยกว่า" — ถ้ารอบสองยังตัดหมดจริง ค่อยยอมรับว่าไม่มีบทความตรงหัวข้อ
        if dropped and len(dropped) == len(subset):
            second = _judge(prompt, subset, key)
            if len(second) < len(dropped):
                dropped = second
    except Exception as exc:
        log_agent_error(str(exc), agent_name="Relevance Filter",
                        step="relevance", domain=source, prompt=prompt)
        return articles, []

    if not dropped:
        return articles, []

    kept: list[dict] = []
    removed: list[dict] = []
    for i, article in enumerate(articles, 1):
        reason = dropped.get(i)
        if reason:
            removed.append({**article, "drop_reason": reason})
        else:
            kept.append(article)

    return kept, removed


def summarize_drop(removed: list[dict]) -> str:
    """ข้อความบอกผู้ใช้ว่าตัดอะไรออกไปบ้าง (ว่างถ้าไม่ได้ตัดอะไร)."""
    if not removed:
        return ""
    lines = [f"🚫 คัดออก {len(removed)} บทความที่ไม่ตรงหัวข้อ:"]
    for article in removed:
        title = article.get("title") or _first_line(article.get("summary", "")) or "(ไม่มีชื่อเรื่อง)"
        lines.append(f"- {title} — {article.get('drop_reason', '')}")
    return "\n".join(lines)
