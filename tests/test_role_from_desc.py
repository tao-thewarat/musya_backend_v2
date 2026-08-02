"""Tests: บทบาทคอลัมน์ต้องมาจากคำอธิบาย ไม่ใช่เดาจากชื่ออย่างเดียว

ที่มา — วัดจริง 2026-08-03 (ผู้ใช้จับได้จากหน้าเว็บ):
ถาม "ผู้ป่วยซึมเศร้าเข้าถึงบริการ เขต 10 ปี 2569" ⇒ AI ตอบ **3703.67%**
  ที่ AI ทำ : pop / target     = 1,219,915 / 32,938  (pop = ประชากร 15 ปีขึ้นไป!)
  ที่ถูก    : result1 / target =    32,054 / 32,938 = 97.32%

สาเหตุ: ไฟล์ HDC ตั้งชื่อคอลัมน์ `pop`/`target`/`result1` ซึ่ง heuristic จากชื่อ
จับไม่ได้เลย ⇒ เป็น `measure` ทั้งหมด ⇒ กฎ "ห้ามใช้ measure เป็นตัวตั้ง"
ห้ามทุกคอลัมน์พร้อมกัน **ก็เท่ากับไม่ได้ห้ามอะไร**
"""
from src.tools.data_dict import _role_of


class TestRoleFromDesc:
    def test_ประชากรต้องเป็น_population_ไม่ใช่_measure(self):
        """ตัวเลขใหญ่ที่ทำให้ร้อยละพุ่งเกิน 100 ต้องเตือนแรงกว่าค่าวัดทั่วไป"""
        assert _role_of("pop", "ประชากรอายุ 15 ปีขึ้นไปในปีที่ใช้คำนวณ") == "population"

    def test_ผู้ป่วยคาดประมาณเป็นตัวหาร(self):
        assert _role_of("target", "จำนวนผู้ป่วยคาดประมาณจากความชุกที่ได้จากการสำรวจ") \
            == "denominator"

    def test_ผู้ป่วยที่ได้รับการรักษาเป็นตัวตั้ง(self):
        assert _role_of("result1",
                        "จำนวนผู้ป่วยสะสมทั้งหมดที่ได้รับการวินิจฉัยและรักษาในจังหวัด") \
            == "numerator"

    def test_คาดประมาณชนะความชุก(self):
        """'ผู้ป่วยคาดประมาณจากความชุก' มีทั้งสองคำ — ต้องเป็นตัวหาร ไม่ใช่ประชากร"""
        assert _role_of("target", "ผู้ป่วยคาดประมาณจากความชุก") == "denominator"

    def test_ชื่อที่กำกับ_a_b_ชัดเจนยังชนะคำอธิบาย(self):
        """ไฟล์อัปโหลดใช้ `A1`/`B1` ซึ่งเชื่อได้กว่าการตีความคำอธิบาย"""
        assert _role_of("B1 ทั้งหมด", "ประชากรเป้าหมาย") == "denominator"
        assert _role_of("A1 ควบคุมได้ตามเกณฑ์", "") == "numerator"

    def test_ร้อยละยังจับจากชื่อได้เหมือนเดิม(self):
        assert _role_of("ร้อยละ A1/B1") == "percentage"
        assert _role_of("ในเขต_ร้อยละ_ควบคุมได้ดี") == "percentage"

    def test_ไม่มีคำอธิบายก็ยังเป็น_measure_เหมือนเดิม(self):
        assert _role_of("result4", "") == "measure"

    def test_คอลัมน์แกนยังเป็น_key(self):
        assert _role_of("จังหวัด") == "key"


class TestPromptShowsFormulaAndPopWarning:
    def test_บอกสูตรพร้อมชื่อคอลัมน์จริง(self, monkeypatch):
        """เดิมบอกแค่นิยาม A/B ไม่บอกว่า A คือคอลัมน์ไหน ⇒ AI เดาเอง และเดาผิด"""
        from src.tools import data_dict_lookup as dl

        monkeypatch.setattr(dl, "get_dict", lambda fid: {
            "file_id": "1", "indicator_th": "x", "provinces": [], "years": [],
            "columns_json": [
                {"name": "pop", "role": "population"},
                {"name": "target", "role": "denominator"},
                {"name": "result1", "role": "numerator"},
            ],
        })
        out = dl.describe_for_prompt("1")
        assert "(result1) ÷ (target) × 100" in out
        assert "ห้ามใช้เป็นตัวตั้งหรือตัวหาร" in out and "pop" in out


class TestPercentOver100Blocked:
    def test_พรอมต์ห้ามพิมพ์ร้อยละเกิน_100(self):
        from src.agents.prompt_profile import CODE_GENERATOR_CORE_POLICY as P

        assert "เกิน 100%" in P and "ห้ามพิมพ์ออกมาเป็นคำตอบ" in P
        assert "3703" in P, "อ้างเคสจริงไว้ให้โมเดลเห็นน้ำหนัก"
