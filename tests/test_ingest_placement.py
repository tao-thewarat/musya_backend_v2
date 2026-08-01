"""Regression tests: ความยืดหยุ่นของการเลือกที่เก็บตอน ingest

ครอบคลุม 3 เรื่องที่เพิ่มเข้ามา:
  • _sanitize_folder_path — รองรับโฟลเดอร์ซ้อนชั้น แต่ต้องกัน path traversal
  • _resolve_rel_path     — ประกอบ path ปลายทางให้ถูกตามจังหวัด/อำเภอ
  • _ai_suggest_folder_names — เสนอชื่อ 3 ตัวเลือกโดยเห็นชื่อที่มีอยู่แล้วในคลัง
"""
import pytest

from src.routers.pdf_ingest import (
    _sanitize_folder_path,
    _resolve_rel_path,
    _ai_suggest_folder_names,
)


class TestSanitizeFolderPath:
    def test_รองรับหลายชั้น(self):
        got = _sanitize_folder_path("3.1 ข้อมูลอุบัติเหตุทางถนน/R_รายงานอุบัติเหตุ-2567")
        assert got == "3.1 ข้อมูลอุบัติเหตุทางถนน/R_รายงานอุบัติเหตุ-2567"

    def test_ชั้นเดียวก็ยังได้เหมือนเดิม(self):
        assert _sanitize_folder_path("R_รายงานตรวจราชการ-ยโสธร-2568") == "R_รายงานตรวจราชการ-ยโสธร-2568"

    def test_กัน_path_traversal(self):
        assert ".." not in _sanitize_folder_path("../../etc/passwd")
        assert _sanitize_folder_path("../ยโสธร") == "ยโสธร"
        assert _sanitize_folder_path("a/../../b") == "a/b"

    def test_ตัดเซกเมนต์ว่างและ_slash_นำหน้า(self):
        assert _sanitize_folder_path("/ยโสธร//รายงาน/") == "ยโสธร/รายงาน"

    def test_รองรับ_backslash_ของ_windows(self):
        assert _sanitize_folder_path(r"หมวด\เอกสาร") == "หมวด/เอกสาร"

    def test_จำกัดความลึกไม่ให้บานปลาย(self):
        deep = "/".join(f"ชั้น{i}" for i in range(10))
        assert len(_sanitize_folder_path(deep).split("/")) <= 4

    def test_ค่าว่างคืนสตริงว่าง(self):
        assert _sanitize_folder_path("") == ""
        assert _sanitize_folder_path("///") == ""


class TestResolveRelPath:
    def test_จังหวัดอย่างเดียว(self):
        assert _resolve_rel_path("ยโสธร", None, "R_รายงาน-2568") == "เขต10/ยโสธร/R_รายงาน-2568"

    def test_จังหวัดและอำเภอ(self):
        assert _resolve_rel_path("ศรีสะเกษ", "ขุขันธ์", "R_รายงาน") == "เขต10/ศรีสะเกษ/ขุขันธ์/R_รายงาน"

    def test_โฟลเดอร์ซ้อนชั้นต่อท้ายได้(self):
        got = _resolve_rel_path("ยโสธร", None, "3.1 อุบัติเหตุ/R_รายงาน-2567")
        assert got == "เขต10/ยโสธร/3.1 อุบัติเหตุ/R_รายงาน-2567"

    def test_ไม่มีจังหวัดไม่เติม_เขต10(self):
        assert _resolve_rel_path(None, None, "X") == "X"

    def test_ชื่อที่มี_traversal_ต้องไม่หลุดออกนอก_เขต10(self):
        got = _resolve_rel_path("ยโสธร", None, "../../หลุด")
        assert got.startswith("เขต10/ยโสธร/")
        assert ".." not in got


class _FakeResp:
    def __init__(self, text):
        self.text = text


class TestAiSuggestFolderNames:
    def _client(self, text):
        class C:
            class models:
                @staticmethod
                def generate_content(**kw):
                    return _FakeResp(text)
        return C()

    def test_อ่าน_JSON_array_ได้(self):
        out = _ai_suggest_folder_names(
            self._client('["R_รายงาน-ยโสธร-2568","R_เอกสารรับตรวจ-ยโสธร-2568","I_ตรวจราชการ-ยโสธร-2568"]'),
            None, "x.pdf", ["R_รายงาน-ยโสธร-2567"])
        assert len(out) == 3
        assert "R_รายงาน-ยโสธร-2568" in out

    def test_ตัดชื่อซ้ำออก(self):
        out = _ai_suggest_folder_names(
            self._client('["A_ชื่อเดียวกัน-2568","A_ชื่อเดียวกัน-2568","B_อีกชื่อ-2568"]'),
            None, "x.pdf", [])
        assert len(out) == 2

    def test_ตอบไม่ใช่_JSON_ต้องคืนลิสต์ว่างไม่ใช่พัง(self):
        out = _ai_suggest_folder_names(self._client("ขอโทษครับ ไม่เข้าใจ"), None, "x.pdf", [])
        assert out == []

    def test_LLM_ล่มต้องคืนลิสต์ว่าง(self):
        class Boom:
            class models:
                @staticmethod
                def generate_content(**kw):
                    raise RuntimeError("quota")
        assert _ai_suggest_folder_names(Boom(), None, "x.pdf", []) == []
