"""Tests: ด่านคัดบทความ "คนละเรื่อง" ออกจากผลค้นงานวิจัย

ที่มา — ผู้ใช้จับได้:
  ถาม "มาตรการลดหวานมันเค็มระดับชุมชนได้ผลไหม"
  ผลลัพธ์มี "การรุกล้ำความเค็มและมาตรการควบคุมความเค็มในแม่น้ำท่าจีน" ปนมาด้วย
  ⇒ ตรงกันแค่คำว่า "เค็ม" + "มาตรการ" แต่เป็นความเค็มของน้ำ ไม่ใช่ของอาหาร

เทสต์นี้จับเฉพาะ "ตรรกะล้วน" (parse_verdicts / article_brief / summarize_drop)
ไม่ยิง LLM จริง — เพราะสิ่งที่ต้องกันคือ **การตัดบทความทิ้งเกินจำเป็น**
ซึ่งเกิดจากการตีความคำตอบโมเดลผิด ไม่ใช่จากตัวโมเดลเอง
"""
from src.agents.research_relevance import article_brief, parse_verdicts, summarize_drop

RIVER = {
    "summary": "**การรุกล้ำความเค็มและมาตรการควบคุมความเค็มในแม่น้ำท่าจีน**\n\n"
               "ศึกษาการรุกล้ำของน้ำเค็มจากทะเลเข้าสู่แม่น้ำท่าจีน...",
    "pdf_url": "https://example.org/a",
}
FOOD = {
    "summary": "**ผลของโปรแกรมลดการบริโภคเกลือในชุมชน**\n\nศึกษาผลของมาตรการลดเค็มระดับชุมชน...",
    "pdf_url": "https://example.org/b",
}


class TestParseVerdicts:
    def test_ตัดเฉพาะรายการที่โมเดลสั่งตัดชัดเจน(self):
        raw = [{"i": 1, "keep": False, "reason": "ความเค็มของน้ำ ไม่ใช่ของอาหาร"},
               {"i": 2, "keep": True, "reason": ""}]
        assert parse_verdicts(raw, 2) == {1: "ความเค็มของน้ำ ไม่ใช่ของอาหาร"}

    def test_โมเดลตอบ_false_เป็นสตริงก็ต้องตัดได้(self):
        """เจอจริงกับโมเดล flash — ตอบ "false"/"no" เป็น string ไม่ใช่ boolean"""
        assert 1 in parse_verdicts([{"i": 1, "keep": "no"}], 1)
        assert 1 in parse_verdicts([{"i": 1, "keep": "false"}], 1)

    def test_ไม่ใช่_list_ต้องไม่ตัดใครเลย(self):
        for raw in (None, {}, "ตัดทิ้งหมดเลย", 42):
            assert parse_verdicts(raw, 3) == {}

    def test_index_นอกช่วงต้องถูกข้าม_ไม่ใช่ตัดมั่ว(self):
        assert parse_verdicts([{"i": 0, "keep": False}, {"i": 9, "keep": False}], 3) == {}

    def test_keep_ที่อ่านไม่ออกให้ถือว่าเก็บไว้(self):
        """เอนไปทางเก็บไว้เสมอ — ตัดบทความที่เกี่ยวข้องทิ้งเสียหายกว่า"""
        assert parse_verdicts([{"i": 1, "keep": None}, {"i": 2}], 2) == {}

    def test_ไม่มีเหตุผลก็ยังตัดได้_แต่ต้องมีข้อความกำกับ(self):
        assert parse_verdicts([{"i": 1, "keep": False}], 1)[1] != ""


class TestArticleBrief:
    def test_ดึงชื่อเรื่องจาก_summary_ของ_thaijo(self):
        brief = article_brief(RIVER, 1)
        assert "[1]" in brief
        assert "แม่น้ำท่าจีน" in brief

    def test_รองรับรูปแบบ_pubmed_ที่แยก_title_abstract(self):
        brief = article_brief({"title": "Community salt reduction", "abstract": "We assessed..."}, 2)
        assert "Community salt reduction" in brief and "We assessed" in brief

    def test_ไม่มีข้อมูลก็ต้องไม่ระเบิด(self):
        assert "[3]" in article_brief({}, 3)


class TestSummarizeDrop:
    def test_ไม่ได้ตัดอะไรก็ไม่ต้องมีข้อความ(self):
        assert summarize_drop([]) == ""

    def test_บอกผู้ใช้ว่าตัดอะไรเพราะอะไร(self):
        note = summarize_drop([{**RIVER, "drop_reason": "ความเค็มของน้ำ ไม่ใช่ของอาหาร"}])
        assert "แม่น้ำท่าจีน" in note
        assert "ความเค็มของน้ำ" in note
