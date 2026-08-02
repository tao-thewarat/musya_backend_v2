"""Tests: Research Planner วางแผนค้นเอง + การ์ดที่ห้ามข้าม

ที่มา: โหมด report-gather เดิมยิงคำถามก้อนเดียวกันเป๊ะใส่ทั้ง 5 แหล่ง
⇒ ส่งประโยคไทย "จัดทำแผนปฏิบัติงาน 1 ปี ลดอุบัติเหตุ..." เข้า PubMed ตรง ๆ
และเรียกแต่ละแหล่งได้ครั้งเดียว ทั้งที่แผนต้องการตัวเลขหลายชุด
"""
from src.agents.research_planner import (
    MAX_QUERY_LEN, MAX_STEPS, TOOLS, normalize_plan, plan_research,
)

Q = "จัดทำแผนปฏิบัติงาน 1 ปี ลดอุบัติเหตุรถจักรยานยนต์กลุ่มอายุ 15-24 ปี จ.อุบลราชธานี"


class TestNormalizeGuards:
    def test_บังคับค้นคลังรายงานสองมุมเสมอ(self):
        """แผนราชการที่ไม่อ้างเอกสารพื้นที่/นโยบายต้นสังกัด ใช้จริงไม่ได้"""
        plan = normalize_plan([{"tool": "stats", "query": "x"}], Q)
        obs = [s for s in plan if s["tool"] == "obsidian"]
        assert len(obs) >= 2
        purposes = " ".join(s["purpose"] for s in obs)
        assert "บริบทพื้นที่" in purposes and "นโยบาย" in purposes

    def test_ไม่เติมซ้ำถ้า_planner_ใส่มาครบแล้ว(self):
        raw = [{"tool": "obsidian", "query": "ก", "purpose": "บริบทพื้นที่"},
               {"tool": "obsidian", "query": "ข", "purpose": "นโยบาย"},
               {"tool": "stats", "query": "ค", "purpose": "ตัวเลข"}]
        plan = normalize_plan(raw, Q)
        assert len([s for s in plan if s["tool"] == "obsidian"]) == 2

    def test_ตัดเครื่องมือที่ไม่รู้จักทิ้ง(self):
        """เคยเจอโมเดลแต่งชื่อชุดข้อมูลขึ้นเอง — เครื่องมือก็แต่งได้เหมือนกัน"""
        raw = [{"tool": "google", "query": "x"}, {"tool": "stats", "query": "y"}]
        plan = normalize_plan(raw, Q)
        assert all(s["tool"] in TOOLS for s in plan)
        assert not any(s["tool"] == "google" for s in plan)

    def test_ตัดคำค้นที่ยาวเกิน(self):
        raw = [{"tool": "stats", "query": "ก" * 900}]
        plan = normalize_plan(raw, Q)
        assert all(len(s["query"]) <= MAX_QUERY_LEN for s in plan)

    def test_จำกัดจำนวนขั้น(self):
        raw = [{"tool": "stats", "query": f"q{i}"} for i in range(50)]
        assert len(normalize_plan(raw, Q)) <= MAX_STEPS

    def test_เรียกเครื่องมือเดิมซ้ำได้(self):
        """แผนต้องการตัวเลขหลายชุด — ต้องถาม stats ได้มากกว่า 1 ครั้ง"""
        raw = [{"tool": "stats", "query": f"ตัวเลขชุดที่ {i}"} for i in range(3)]
        plan = normalize_plan(raw, Q)
        assert len([s for s in plan if s["tool"] == "stats"]) == 3

    def test_ขยะล้วนต้องถอยไปแผนสำรอง(self):
        for bad in (None, "ไม่ใช่ json", [], [{"tool": "x"}], [{"query": "y"}]):
            plan = normalize_plan(bad, Q)
            assert len(plan) >= 3
            assert all(s["tool"] in TOOLS for s in plan)

    def test_แผนสำรองยังมีคลังรายงานสองมุม(self):
        plan = normalize_plan(None, Q)
        assert len([s for s in plan if s["tool"] == "obsidian"]) >= 2

    def test_ทุกขั้นมีฟิลด์ครบ(self):
        for s in normalize_plan([{"tool": "stats", "query": "x"}], Q):
            assert s["tool"] and s["query"] and s["purpose"]


class TestPlanResearch:
    def test_ไม่มี_api_key_ต้องได้แผนสำรอง_ไม่ใช่ระเบิด(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        plan = plan_research(Q, "")
        assert len(plan) >= 3

    def test_llm_ล้มต้องถอยไปแผนสำรอง(self, monkeypatch):
        """ห้ามทำให้ฟีเจอร์ที่ใช้ได้อยู่แล้วพังเพราะของใหม่"""
        from src.agents import research_planner as rp

        def boom(**k):
            raise RuntimeError("LLM ล่ม")

        monkeypatch.setattr(rp.litellm, "completion", boom)
        plan = rp.plan_research(Q, "key")
        assert len(plan) >= 3 and all(s["tool"] in TOOLS for s in plan)

    def test_แกะ_json_ที่ห่อด้วย_codefence_ได้(self, monkeypatch):
        """โมเดลชอบห่อ ```json แม้สั่งห้ามแล้ว"""
        from src.agents import research_planner as rp

        payload = ('```json\n[{"tool":"pubmed","query":"motorcycle helmet Thailand",'
                   '"purpose":"หลักฐาน"}]\n```')

        class _M:
            content = payload

        class _C:
            message = _M()

        class _R:
            choices = [_C()]

        monkeypatch.setattr(rp.litellm, "completion", lambda **k: _R())
        plan = rp.plan_research(Q, "key")
        assert any(s["tool"] == "pubmed" and "motorcycle" in s["query"] for s in plan)
