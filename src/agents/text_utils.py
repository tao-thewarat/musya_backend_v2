"""Shared post-processing for LLM-generated answers.

Gemini (โดยเฉพาะตอนโดน context ยาว/ใกล้ max_tokens) บางครั้งเข้าสู่ภาวะ
"repetition loop" คือเขียนคำตอบทั้งชุดซ้ำอีกรอบ (บางทีก็ paraphrase ไม่เหมือน
เดิมเป๊ะ) หรือวนซ้ำเฉพาะบล็อกท้าย ๆ เช่น "คำถามติดตาม" หลายรอบ — ทำให้ output
ที่ผู้ใช้เห็น "เบิ้ล" กันหลายอัน dedupe ตรงนี้ตัดส่วนที่วนซ้ำทิ้งแบบระมัดระวัง
(เก็บคำตอบรอบแรกที่สมบูรณ์ไว้เสมอ ไม่ตัดเนื้อหาที่ถูกต้อง)
"""
import re

# หัวข้อ "คำถามติดตาม" ที่เป็น "หัวข้อจริง" (ขึ้นต้นบรรทัด, มี #/​** นำหน้าได้)
# — ไม่ใช่คำว่า "คำถามติดตาม" ที่โผล่กลางประโยค (in-prose)
_FOLLOWUP_HEADER_RE = re.compile(
    r"(?m)^[ \t]*(?:#{1,4}[ \t]*)?(?:\*\*)?[ \t]*(?:คำถามติดตาม|Follow-?up)"
)
_NUM_ITEM_RE = re.compile(r"^[ \t]*\d+[\.\)]\s")
_HEADER_RE = re.compile(r"^\**\s*([^\n*]{4,40})")

# หัวข้อส่วนแบบมีเลขกำกับที่ SYSTEM_PROMPT บังคับไว้ เช่น "**1. สรุปคำตอบ**"
# ใช้จับเคส "LLM เขียนคำตอบทั้งชุดใหม่ตั้งแต่ต้น" ซึ่งฟอร์แมตนี้จะมีหัวข้อเลขเดิมโผล่ซ้ำ
_SECTION_HEAD_RE = re.compile(r"(?m)^[ \t]*(?:#{1,4}[ \t]*)?\*{0,2}[ \t]*(\d)[\.\)][ \t]*([^\n*]{2,40})")


def _cut_restarted_answer(text: str) -> str | None:
    """จับเคสที่ LLM "เขียนคำตอบทั้งชุดใหม่ตั้งแต่ต้น" แล้วตัดรอบที่สองทิ้ง

    ทำไมกฎเดิมจับไม่ได้: กฎหลักด้านบนอาศัยหัวข้อ "คำถามติดตาม" เป็นหมุดปิดท้ายคำตอบ
    แต่ระบบเปลี่ยนไปใช้บล็อก <<<FOLLOWUPS>>> ซึ่งถูก _extract_and_strip_followups
    ตัดออกไป **ก่อน** dedupe จะทำงาน หมุดนั้นจึงไม่มีอยู่จริงอีกต่อไป
    ส่วนกฎสำรอง '---' ก็ไม่ทำงานเพราะการวนซ้ำไม่ได้คั่นด้วยเส้น

    หลักการใหม่: ฟอร์แมตที่บังคับไว้มีหัวข้อ "1." เพียงครั้งเดียวต่อคำตอบ ถ้าเจอ "1."
    ซ้ำอีกครั้ง **พร้อมกับ** หัวข้ออื่น (เช่น "2.") ซ้ำด้วย แปลว่าเป็นการเริ่มเขียนใหม่
    ไม่ใช่การใช้เลขข้อบังเอิญ — ตัดตั้งแต่จุดเริ่มรอบสองทิ้ง

    ⚠️ ตั้งใจเข้มงวด (ต้องซ้ำ ≥2 หัวข้อ) เพราะการตัดผิดหมายถึงคำตอบที่ถูกต้องหายไป
    ซึ่งแย่กว่าปล่อยให้เห็นคำตอบซ้ำ
    """
    heads = list(_SECTION_HEAD_RE.finditer(text))
    if len(heads) < 4:
        return None

    first_num = heads[0].group(1)
    # ตำแหน่งที่หัวข้อหมายเลขเดียวกับอันแรก โผล่ซ้ำอีกรอบ
    restarts = [m for m in heads[1:] if m.group(1) == first_num]
    if not restarts:
        return None

    restart = restarts[0]
    before = {m.group(1) for m in heads if m.start() < restart.start()}
    after = {m.group(1) for m in heads if m.start() >= restart.start()}
    # ต้องมีหัวข้ออย่างน้อย 2 หมายเลขที่ปรากฏทั้งก่อนและหลัง จึงถือว่าเป็นการเขียนซ้ำทั้งชุด
    if len(before & after) < 2:
        return None

    kept = text[: restart.start()].rstrip()
    # กันตัดจนเหลือคำตอบกุด — รอบแรกต้องมีเนื้อหาพอสมควรจริง ๆ
    if len(kept) < 200:
        return None
    return kept


def dedupe_repeated_answer(text: str) -> str:
    """ตัดคำตอบที่ถูกเขียนซ้ำจาก repetition loop ของ LLM.

    หลักการหลัก: บล็อก "คำถามติดตาม" คือ "ส่วนท้ายสุด" ของคำตอบเสมอ (ตาม
    SYSTEM_PROMPT) — ดังนั้นเก็บเนื้อหาถึง "บล็อกคำถามติดตามอันแรก" ให้ครบ แล้ว
    ตัดทุกอย่างหลังจากนั้นทิ้ง (เพราะเป็นการวนซ้ำ/เขียนคำตอบใหม่ทั้งชุด)

    ครอบคลุมทุกรูปแบบที่เจอจริง:
      • คำตอบทั้งชุด (structured 4 ส่วน) วนซ้ำ 3-5 รอบ แต่ละรอบจบด้วย
        "**คำถามติดตาม**"  → ตัดหลังบล็อกแรก
      • คำตอบแบบสนทนา (paraphrase) วนซ้ำ 2 รอบ, บล็อกคำถามติดตามซ้ำ → ตัดหลังบล็อกแรก
      • เฉพาะบล็อกคำถามติดตามวนซ้ำ 5 รอบ (ตัวคำตอบไม่ซ้ำ) → ตัดหลังบล็อกแรก

    ปลอดภัยกับคำตอบปกติ (บล็อกคำถามติดตามอันเดียว, ไม่มีอะไรต่อท้าย → no-op)
    และมี guard `len < 200` กันไม่ให้ไปยุ่งกับคำตอบสั้น ๆ
    """
    if not text or len(text) < 200:
        return text

    # ── Rule ใหม่: คำตอบทั้งชุดถูกเขียนใหม่ตั้งแต่หัวข้อ "1." ─────────────────
    # ต้องมาก่อนกฎอื่น เพราะเป็นรูปแบบการวนซ้ำที่เจอจริงกับฟอร์แมต 4 ส่วนปัจจุบัน
    # (กฎเดิมอาศัยหัวข้อ "คำถามติดตาม" ซึ่งถูกตัดออกไปก่อนแล้ว จึงไม่เคยทำงาน)
    restarted = _cut_restarted_answer(text)
    if restarted is not None:
        return restarted

    # ── Rule หลัก: ตัดหลัง "บล็อกคำถามติดตามอันแรก" ──────────────────────────
    hdrs = list(_FOLLOWUP_HEADER_RE.finditer(text))
    if hdrs:
        h = hdrs[0]
        lines = text[h.start():].split("\n")
        kept = [lines[0]]           # บรรทัดหัวข้อ "คำถามติดตาม"
        i = 1
        # ข้ามบรรทัดว่างหลังหัวข้อ (บางฟอร์แมตเว้นบรรทัด)
        while i < len(lines) and lines[i].strip() == "":
            kept.append(lines[i]); i += 1
        # เก็บรายการคำถามที่เป็นเลขข้อ (1. 2. 3. ...) ติดกัน
        n_items = 0
        while i < len(lines) and _NUM_ITEM_RE.match(lines[i]):
            kept.append(lines[i]); i += 1; n_items += 1
        # ตัด "restart" ที่ถูก glue ท้ายข้อสุดท้าย (เช่น "...ครับ?สวัสดีครับ ...")
        # ให้เหลือถึงเครื่องหมาย "?" ตัวแรกของข้อนั้น
        if n_items and "?" in kept[-1]:
            kept[-1] = kept[-1][: kept[-1].index("?") + 1]

        # ใช้กฎนี้เฉพาะเมื่อจับรายการคำถามได้จริง (กันเผลอตัดคำถามทิ้งถ้าฟอร์แมตแปลก)
        if n_items:
            candidate = (text[: h.start()] + "\n".join(kept)).rstrip()
            if len(candidate) < len(text.rstrip()):
                return candidate
            return text
        # ไม่มีรายการเลขข้อ → ตกไปใช้ Rule '---' ด้านล่างแทน

    # ── Rule สำรอง: คำตอบซ้ำคั่นด้วย '---' และ segment หลังขึ้นหัวข้อเดียวกับแรก ──
    dash = list(re.finditer(r"\n-{3,}\n", text))
    if dash:
        fh = _HEADER_RE.match(text[: dash[0].start()].strip())
        if fh:
            key = fh.group(1).strip()
            for m in dash:
                sh = _HEADER_RE.match(text[m.end():].lstrip())
                if sh and sh.group(1).strip() == key:
                    return text[: m.start()].rstrip()
    return text
