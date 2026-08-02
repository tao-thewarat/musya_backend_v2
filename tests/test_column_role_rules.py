"""Tests: กฎการเลือกคอลัมน์ต้องไปถึง AI

ที่มา — รันจริง 2026-08-03 ถาม "ผู้ป่วยความดันควบคุมได้ดี ที่อำเภอคำชะอี":
AI หยิบคอลัมน์ `ได้รับการตรวจวัดความดัน` (role=measure, 3,004 คน) มาเป็นตัวตั้ง
แทน `A2 ควบคุมได้ตามเกณฑ์` (role=numerator, 1,667 คน)
⇒ ตอบ 3004/3153 = 95.27% แทนคำตอบจริง 1667/3153 = 52.87%
แล้วยังชมว่า "สูงกว่าเป้าหมายร้อยละ 60 สะท้อนประสิทธิภาพการจัดการโรค"

ตัวเลขจริงทุกตัว — พจนานุกรมก็รู้ถูกอยู่แล้ว แต่**ไม่ได้บอกออกไปเป็นกฎ**
คอลัมน์ role=measure ถูกกรองทิ้งตั้งแต่ต้น AI จึงไม่มีทางรู้ว่าห้ามใช้
"""
import json

from src.tools import data_dict_lookup as dl


def _dict_with(cols, **extra):
    d = {"file_id": "1", "indicator_th": "ทดสอบ",
         "columns_json": cols, "provinces": [], "years": []}
    d.update(extra)
    return d


COLS = [
    {"name": "B2 ทั้งหมด", "role": "denominator"},
    {"name": "ได้รับการตรวจวัดความดัน", "role": "measure"},
    {"name": "A2 ควบคุมได้ตามเกณฑ์", "role": "numerator"},
    {"name": "ร้อยละ A2/B2", "role": "percentage"},
]


class TestColumnRuleInPrompt:
    def test_บอกว่ามีคอลัมน์ร้อยละแล้วห้ามหารเอง(self, monkeypatch):
        monkeypatch.setattr(dl, "get_dict", lambda fid: _dict_with(COLS))
        out = dl.describe_for_prompt("1")
        assert "ร้อยละ A2/B2" in out
        assert "ห้ามหารเอง" in out, "ต้องบอกเป็นกฎ ไม่ใช่แค่ลิสต์ชื่อคอลัมน์"

    def test_เตือนชื่อคอลัมน์_measure_ออกมาให้ครบ(self, monkeypatch):
        """เดิมกรอง measure ทิ้ง AI จึงไม่รู้ว่ามีคอลัมน์นี้และห้ามใช้"""
        monkeypatch.setattr(dl, "get_dict", lambda fid: _dict_with(COLS))
        out = dl.describe_for_prompt("1")
        assert "ได้รับการตรวจวัดความดัน" in out
        assert "ห้ามใช้เป็นตัวตั้ง" in out

    def test_ไฟล์ที่ไม่มีคอลัมน์ร้อยละต้องไม่ขึ้นกฎนั้น(self, monkeypatch):
        cols = [c for c in COLS if c["role"] != "percentage"]
        monkeypatch.setattr(dl, "get_dict", lambda fid: _dict_with(cols))
        out = dl.describe_for_prompt("1")
        assert "ห้ามหารเอง" not in out, "อย่าสั่งให้ใช้คอลัมน์ที่ไม่มีอยู่"

    def test_รองรับ_columns_json_ที่เป็นสตริง(self, monkeypatch):
        monkeypatch.setattr(dl, "get_dict",
                            lambda fid: _dict_with(json.dumps(COLS, ensure_ascii=False)))
        assert "ห้ามหารเอง" in dl.describe_for_prompt("1")


class TestCodeGeneratorPolicy:
    def test_พรอมต์มีกฎครบทั้งสี่ข้อ(self):
        from src.agents.prompt_profile import CODE_GENERATOR_CORE_POLICY as P

        assert "ห้ามคำนวณร้อยละเอง" in P, "กฎข้อ 6 หาย"
        assert "ค่าวัด" in P and "ห้ามใช้" in P, "กฎข้อ 7 หาย"
        # เดิมกฎข้อ 8 บอกแค่ "เกิน 90% ให้ทวน" ซึ่งอ่อนเกิน — AI ทวนแล้วยังตอบ 3703%
        # ออกไปพร้อมเขียนกำกับว่า "สูงผิดปกติ" ⇒ เปลี่ยนเป็นห้ามพิมพ์ออกไปเลย
        assert "เกิน 100%" in P, "กฎข้อ 8 (ห้ามร้อยละเกิน 100) หาย"
        assert "คนละประชากร" in P, "กฎข้อ 9 (ตรวจตัวชี้วัดตรงคำถาม) หาย"

    def test_อ้างเคสจริงไว้ในพรอมต์(self):
        """ตัวอย่างที่เคยผิดจริงช่วยให้โมเดลเข้าใจกฎมากกว่าคำสั่งลอย ๆ"""
        from src.agents.prompt_profile import CODE_GENERATOR_CORE_POLICY as P

        assert "95.27" in P and "52.87" in P
