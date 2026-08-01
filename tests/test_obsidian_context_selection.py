"""Regression tests สำหรับการคัดกรอง + แพ็กโน้ตใน src/agents/obsidian_fullcontext.py

ล็อกบั๊กจริงที่เจอ: คำถามภาษาไทยที่ไม่ระบุจังหวัด ("ขอข้อมูลการควบคุมโรค") ทำให้ระบบ
ส่งโน้ตของ **จังหวัดเดียว** ให้ AI (มุกดาหาร 46 จาก 47 โน้ต) แล้วตอบว่า "ไม่พบข้อมูล"
ทั้งที่คลังมีเอกสารเรื่องนี้อยู่ 57 โน้ตกระจายครบทั้ง 5 จังหวัด

สาเหตุ 2 ชั้น:
  1. ตัดคำด้วยช่องว่าง → ภาษาไทยไม่เว้นวรรค → ได้คีย์เวิร์ดก้อนเดียว → ทุกโน้ตคะแนน 0
  2. คะแนนเท่ากันหมด → sorted() แบบ stable คงลำดับ ORDER BY relative_path (เรียงตัวอักษร)
     → จังหวัดที่ขึ้นต้นด้วย "ม" กินเพดาน context หมดคนเดียว
"""
import pytest

from src.agents.obsidian_fullcontext import (
    _extract_search_terms,
    _pack_by_province,
    _STOPWORD_TERMS,
    _MAX_SEARCH_TERMS,
)


class FakeSettings:
    GEMINI_MODEL = "gemini-2.5-flash-lite"
    GEMINI_API_KEY = "fake-key"


def _note(province: str, size: int, score: int, name: str = "n") -> dict:
    return {
        "relative_path": f"เขต10/{province}/{name}-{score}-{size}/x.md",
        "content": "x" * size,
        "file_id": None,
        "province": province,
        "sz": size,
        "score": score,
    }


class TestPackByProvince:
    """แพ็กต้องไม่ปล่อยให้จังหวัดเดียวกินเพดานหมด"""

    def test_จังหวัดเดียวต้องไม่กินเพดานหมด(self):
        # จำลองของจริง: มุกดาหารมาก่อนตามตัวอักษรและมีโน้ตเยอะพอจะกินเพดานคนเดียว
        rows = [_note("มุกดาหาร", 40_000, 1, f"m{i}") for i in range(20)]
        rows += [_note("ศรีสะเกษ", 40_000, 3, f"s{i}") for i in range(5)]
        rows += [_note("ยโสธร", 40_000, 3, f"y{i}") for i in range(5)]

        picked = _pack_by_province(rows, max_chars=400_000)
        provs = {r["province"] for r in picked}

        assert len(provs) == 3, f"ต้องได้ครบทุกจังหวัดที่มีผลลัพธ์ ได้ {provs}"
        # และจังหวัดที่คะแนนสูงกว่าต้องได้ส่วนแบ่งไม่น้อยกว่าจังหวัดคะแนนต่ำ
        by = {p: sum(r["sz"] for r in picked if r["province"] == p) for p in provs}
        assert by["ศรีสะเกษ"] >= by["มุกดาหาร"] * 0.5

    def test_ไม่เกินเพดานที่กำหนด(self):
        rows = [_note("ยโสธร", 30_000, 2, f"y{i}") for i in range(50)]
        picked = _pack_by_province(rows, max_chars=200_000)
        assert sum(r["sz"] for r in picked) <= 200_000

    def test_จังหวัดเดียวล้วนก็ยังต้องแพ็กได้(self):
        rows = [_note("ศรีสะเกษ", 30_000, 5, f"s{i}") for i in range(10)]
        picked = _pack_by_province(rows, max_chars=100_000)
        assert picked
        assert sum(r["sz"] for r in picked) <= 100_000

    def test_โน้ตใหญ่เกินเพดานต้องได้อย่างน้อย1ตัวไม่ใช่คืนว่าง(self):
        rows = [_note("ยโสธร", 900_000, 5, "big")]
        picked = _pack_by_province(rows, max_chars=500_000)
        assert len(picked) == 1, "จังหวัดเดียวและใหญ่เกินเพดาน ต้องยังส่งไปให้ AI ได้"

    def test_ไม่มีโน้ตเลยต้องคืนลิสต์ว่าง(self):
        assert _pack_by_province([], max_chars=500_000) == []


class TestExtractSearchTerms:
    """การสกัดคำค้นต้องกันคำสามัญ และห้ามพังทั้งระบบเมื่อ LLM ล่ม"""

    def test_กรองคำสามัญออก(self, monkeypatch):
        payload = '["การควบคุมโรค", "ข้อมูล", "ระบาดวิทยา", "รายงาน", "โรคติดต่อ"]'
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": payload})()})()]})(),
        )
        terms = _extract_search_terms("ขอข้อมูลการควบคุมโรค", FakeSettings())
        assert "การควบคุมโรค" in terms
        assert "ระบาดวิทยา" in terms
        # คำสามัญที่ทำให้ตัวกรองเลิกกรอง ต้องไม่หลุดเข้ามา
        assert "ข้อมูล" not in terms
        assert "รายงาน" not in terms
        assert not (set(terms) & _STOPWORD_TERMS)

    def test_จำกัดจำนวนคำไม่ให้ตัวกรองเลิกกรอง(self, monkeypatch):
        payload = "[" + ",".join(f'"คำค้นที่{i:02d}"' for i in range(20)) + "]"
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": payload})()})()]})(),
        )
        terms = _extract_search_terms("คำถามอะไรสักอย่าง", FakeSettings())
        assert len(terms) <= _MAX_SEARCH_TERMS

    def test_LLMล่มต้องคืนลิสต์ว่างไม่ใช่โยนError(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("quota exceeded")
        monkeypatch.setattr("litellm.completion", boom)
        assert _extract_search_terms("ขอข้อมูลการควบคุมโรค", FakeSettings()) == []

    def test_ตอบไม่ใช่JSONต้องคืนลิสต์ว่าง(self, monkeypatch):
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": "ขอโทษครับ ผมไม่เข้าใจคำถาม"})()})()]})(),
        )
        assert _extract_search_terms("อะไรสักอย่าง", FakeSettings()) == []

    def test_คำถามว่างไม่ต้องเรียกLLM(self, monkeypatch):
        def boom(**kw):
            raise AssertionError("ไม่ควรเรียก LLM เมื่อคำถามว่าง")
        monkeypatch.setattr("litellm.completion", boom)
        assert _extract_search_terms("   ", FakeSettings()) == []
