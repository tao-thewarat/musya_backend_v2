"""Tests: เสนอชื่อเอกสารให้ผู้ใช้ตรวจ/แก้ ก่อนกดสร้างจริง

เดิมชื่อเรื่องถูกตั้งตอนสร้างเอกสารเสร็จแล้ว ⇒ ผู้ใช้เห็นชื่อครั้งแรกตอนที่
**แก้อะไรไม่ได้แล้ว** ถ้าชื่อไม่ตรงชนิดเอกสารต้องสร้างใหม่ทั้งฉบับ
ซึ่งกิน quota และเวลาหลายนาที (เอกสาร 10+ หน้า)
"""
import inspect

from src.agents import thaijo_agent as ta


class _M:
    def __init__(self, c): self.content = c


class _C:
    def __init__(self, c): self.message = _M(c)


class _R:
    def __init__(self, c): self.choices = [_C(c)]


class TestSuggestReportTitle:
    def test_คืนชื่อที่โมเดลเสนอ(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ta.litellm, "completion",
                            lambda **k: _R("ข้อเสนอเชิงนโยบายเพื่อลดการบาดเจ็บจากรถจักรยานยนต์"))
        assert ta.suggest_report_title("จัดทำแผน...", "policy") == \
            "ข้อเสนอเชิงนโยบายเพื่อลดการบาดเจ็บจากรถจักรยานยนต์"

    def test_ตัดเครื่องหมายคำพูดที่โมเดลชอบใส่มา(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ta.litellm, "completion",
                            lambda **k: _R('"แผนยุทธศาสตร์ความปลอดภัยทางถนน"'))
        assert ta.suggest_report_title("x", "plan") == "แผนยุทธศาสตร์ความปลอดภัยทางถนน"

    def test_เอาแค่บรรทัดแรก(self, monkeypatch):
        """โมเดลชอบอธิบายต่อท้ายแม้สั่งห้ามแล้ว"""
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ta.litellm, "completion",
                            lambda **k: _R("แผนปฏิบัติงานลดอุบัติเหตุ ปีงบประมาณ 2568\nเหตุผล: ..."))
        assert ta.suggest_report_title("x", "workplan") == \
            "แผนปฏิบัติงานลดอุบัติเหตุ ปีงบประมาณ 2568"

    def test_ยาวเกินไปถือว่าใช้ไม่ได้(self, monkeypatch):
        """ยาวเกิน = โมเดลอธิบายแทนที่จะตั้งชื่อ — ไม่เอาดีกว่าเอาผิด"""
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ta.litellm, "completion", lambda **k: _R("ก" * 300))
        assert ta.suggest_report_title("x", "policy") == ""

    def test_สั้นเกินไปก็ไม่เอา(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(ta.litellm, "completion", lambda **k: _R("แผน"))
        assert ta.suggest_report_title("x", "policy") == ""

    def test_ไม่มี_key_คืนค่าว่างไม่ระเบิด(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert ta.suggest_report_title("x", "policy") == ""

    def test_llm_ล้มคืนค่าว่าง(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")

        def boom(**k):
            raise RuntimeError("ล่ม")

        monkeypatch.setattr(ta.litellm, "completion", boom)
        assert ta.suggest_report_title("x", "policy") == ""

    def test_ใช้กฎตั้งชื่อชุดเดียวกับตอนสร้างจริง(self):
        """ถ้าใช้คนละกฎ ชื่อที่เสนอกับชื่อที่ได้จริงจะเป็นคนละแนว"""
        src = inspect.getsource(ta.suggest_report_title)
        assert "title_rule" in src


class TestUserTitleWins:
    def test_ชื่อที่ผู้ใช้ยืนยันต้องถูกบังคับใช้(self):
        src = inspect.getsource(ta.run_thaijo_report_pipeline)
        assert "report_title" in src
        assert "ห้ามเปลี่ยนแม้แต่ตัวอักษรเดียว" in src

    def test_ไม่ทับชื่อผู้ใช้ด้วยชื่อที่ดึงจาก_html(self):
        src = inspect.getsource(ta.run_thaijo_report_pipeline)
        i = src.index("title_match")
        assert "if report_title.strip():" in src[:i], \
            "ต้องเช็คชื่อผู้ใช้ก่อนดึงจาก HTML"

    def test_ไม่ส่งชื่อมาก็ยังทำงานเหมือนเดิม(self):
        import src.schemas.thaijo as sch
        assert sch.ThaiJoGenerateRequest(query="q", articles_text="a").report_title == ""


class TestTopicsEndpointReturnsTitle:
    def test_endpoint_คืน_title_มาด้วย(self):
        from src.routers import thaijo
        src = inspect.getsource(thaijo.thaijo_plan_topics)
        assert '"title": title' in src
        assert "suggest_report_title" in src

    def test_ตั้งชื่อไม่ได้ต้องไม่ทำให้หัวข้อพัง(self):
        from src.routers import thaijo
        src = inspect.getsource(thaijo.thaijo_plan_topics)
        assert "except Exception:" in src and 'title = ""' in src
