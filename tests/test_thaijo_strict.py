"""Tests: ThaiJo ต้องค้นแบบ strict — ไม่งั้นได้บทความคนละเรื่อง

ที่มา — ผู้ใช้รายงาน 2026-08-03 ว่าผลค้น ThaiJo ไม่สอดคล้องกับหัวข้อ
ตรวจ API จริงพบว่า `strict=false` คืน **10,000 ผลทุกคำค้น** (ชนเพดาน)
= แทบไม่กรองอะไรเลย แล้ว pipeline หยิบ 5 อันแรกมา ⇒ บทความไม่ตรงหัวข้อ

    "ปัจจัยเสี่ยง อุบัติเหตุ จักรยานยนต์ วัยรุ่น"  strict=8    loose=10000
    "อุบัติเหตุจักรยานยนต์ วัยรุ่น"               strict=18   loose=10000
    "ซึมเศร้า ผู้สูงอายุ"                        strict=845  loose=10000

แต่พรอมต์ Keyword Extractor เขียนตัวอย่าง JSON ไว้ว่า `"strict": false`
โมเดลจึงลอกมาแทบทุกครั้ง
"""
from src.agents import thaijo_agent as tj


class TestStrictForced:
    def test_บังคับ_strict_แม้โมเดลตอบ_false(self, monkeypatch):
        class _M:
            content = '{"term":"อุบัติเหตุ จักรยานยนต์","strict":false,"size":5}'

        class _C:
            message = _M()

        class _R:
            choices = [_C()]

        monkeypatch.setattr(tj.litellm, "completion", lambda **k: _R())
        out = tj._extract_search_payload("อุบัติเหตุจักรยานยนต์วัยรุ่น", "key")
        assert out["strict"] is True, "loose คืน 10,000 ผลทุกคำค้น ใช้ไม่ได้"

    def test_พรอมต์ไม่แนะนำ_strict_false_อีกแล้ว(self):
        assert '"strict": false' not in tj._KEYWORD_PROMPT_TMPL
        assert '"strict": true' in tj._KEYWORD_PROMPT_TMPL

    def test_fallback_ยังใช้_strict(self):
        """ไม่มี API key ⇒ ใช้ค่าเริ่มต้น ซึ่งต้อง strict เหมือนกัน"""
        assert tj._extract_search_payload("อะไรก็ได้", "")["strict"] is True


class TestFallbackLadder:
    """strict ก่อน → ตัดคำให้สั้นลง → loose เป็นทางสุดท้าย"""

    def _fake_http(self, monkeypatch, script):
        calls = []

        class _Resp:
            def __init__(self, payload):
                self._p = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._p

        def _post(url, json=None, **k):
            calls.append(json)
            return _Resp(script[len(calls) - 1])

        monkeypatch.setattr(tj.httpx, "post", _post)
        return calls

    def test_เจอผลตั้งแต่_strict_ต้องไม่ยิงซ้ำ(self, monkeypatch):
        calls = self._fake_http(monkeypatch, [{"total": 8, "result": [{}]}])
        tj.fetch_thaijo_articles({"term": "ก ข ค ง", "size": 3})
        assert len(calls) == 1 and calls[0]["strict"] is True

    def test_strict_ว่างแล้วลองตัดคำให้สั้นก่อน(self, monkeypatch):
        calls = self._fake_http(monkeypatch, [
            {"total": 0, "result": []}, {"total": 5, "result": [{}]}])
        tj.fetch_thaijo_articles({"term": "ปัจจัยเสี่ยง อุบัติเหตุ จักรยานยนต์ วัยรุ่น",
                                  "size": 3})
        assert len(calls) == 2
        assert calls[1]["term"] == "ปัจจัยเสี่ยง อุบัติเหตุ", "ตัดเหลือ 2 คำแรก"
        assert calls[1]["strict"] is True, "ยังต้อง strict อยู่"

    def test_loose_เป็นทางสุดท้ายจริง(self, monkeypatch):
        calls = self._fake_http(monkeypatch, [
            {"total": 0, "result": []}, {"total": 0, "result": []},
            {"total": 9, "result": [{}]}])
        tj.fetch_thaijo_articles({"term": "ก ข ค ง", "size": 3})
        assert len(calls) == 3 and calls[2]["strict"] is False
