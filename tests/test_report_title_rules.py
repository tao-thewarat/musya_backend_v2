"""Tests: ชื่อเอกสารต้องสอดคล้องกับชนิดที่ผู้ใช้เลือก

ผู้ใช้รายงาน 2026-08-06: เลือก "นโยบาย" แต่ได้ชื่อว่า
    "สรุปรายงานนโยบาย — จัดทำแผนปฏิบัติงาน 1 ปี ลดอุบัติเหตุ..."
ซึ่งขัดกันเอง · เลือก "ยุทธศาสตร์" ก็ได้
    "แผนยุทธศาสตร์ — แผนปฏิบัติงาน 1 ปี เพื่อลดอุบัติเหตุ..."

ต้นตอ: พรอมต์ยัดคำถามผู้ใช้ลงในชื่อตรง ๆ
    <h2 class="article-title">ชื่อ{doc_label}ภาษาไทย — {query}</h2>

คำถามผู้ใช้เป็น "คำสั่งให้ทำงาน" ไม่ใช่ "ชื่อเอกสาร" — เอกสารราชการจริง
ไม่มีฉบับไหนชื่อขึ้นต้นว่า "จัดทำ..."
"""
from src.agents.thaijo_prompts import (
    DOC_TYPES, _TITLE_RULES, build_prompt, title_rule,
)

Q = "จัดทำแผนปฏิบัติงาน 1 ปี ลดอุบัติเหตุรถจักรยานยนต์กลุ่มอายุ 15-24 ปี จ.อุบลราชธานี"


def _prompt(doc_type: str) -> str:
    return build_prompt(doc_type, Q, "แผนรายงาน", "บทความ", 3, "")


class TestTitleRulesPresent:
    def test_ทุกชนิดเอกสารมีกฎตั้งชื่อ(self):
        for dt in DOC_TYPES:
            assert "กฎการตั้งชื่อเอกสาร" in _prompt(dt), dt

    def test_กฎแตกต่างกันตามชนิด(self):
        """ถ้ากฎเหมือนกันหมด ก็ไม่ได้แก้ปัญหาอะไร"""
        rules = {dt: title_rule(dt) for dt in DOC_TYPES}
        assert len(set(rules.values())) == len(DOC_TYPES)

    def test_ไม่ยัดคำถามผู้ใช้ลงในชื่ออีกแล้ว(self):
        """นี่คือบรรทัดที่เป็นต้นตอ — ห้ามกลับมา"""
        for dt in DOC_TYPES:
            p = _prompt(dt)
            after_title = p.split('article-title')[1][:150] if 'article-title' in p else ""
            assert Q not in after_title, f"{dt}: ยังยัดคำถามลงในชื่อ"


class TestPerTypeGuidance:
    def test_นโยบายห้ามใช้คำว่าแผนปฏิบัติงาน(self):
        r = title_rule("policy")
        assert "ข้อเสนอเชิงนโยบาย" in r
        assert 'ห้ามมีคำว่า "แผนปฏิบัติงาน"' in r

    def test_ยุทธศาสตร์ต้องเป็นระยะยาวไม่ใช่รายปี(self):
        r = title_rule("plan")
        assert "ระยะยาว" in r
        assert "แผนปฏิบัติงาน 1 ปี" in r, "ต้องห้ามคำนี้ไว้ชัด ๆ"

    def test_แผนปฏิบัติงานต้องระบุปีงบประมาณ(self):
        assert "ปีงบประมาณ" in title_rule("workplan")

    def test_ทุกชนิดห้ามลอกคำสั่งผู้ใช้(self):
        for dt in DOC_TYPES:
            r = title_rule(dt)
            assert "ห้ามลอกประโยคคำสั่งของผู้ใช้" in r, dt
            assert 'ห้ามขึ้นต้นด้วย "จัดทำ"' in r, dt

    def test_มีตัวอย่างชื่อที่ดีให้ดู(self):
        """บอกแต่ข้อห้ามไม่พอ ต้องให้เห็นว่าที่ถูกหน้าตาเป็นยังไง"""
        for dt in DOC_TYPES:
            assert "เช่น" in title_rule(dt), dt

    def test_ชนิดที่ไม่รู้จักไม่พัง(self):
        assert title_rule("ไม่มีชนิดนี้") == _TITLE_RULES["policy"]
