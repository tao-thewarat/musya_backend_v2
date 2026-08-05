"""Tests: PubMed ต้องไม่ค้นด้วยชื่อจังหวัดที่ผู้ใช้ไม่ได้ถาม

ที่มา — ปัญหาเดียวกับ ThaiJo แต่หนักกว่ามาก เพราะวรรณกรรมนานาชาติแทบไม่มี
ชื่อจังหวัดไทยปรากฏ วัดกับ PubMed จริง 2026-08-03:

    suicide prevention AND (Yasothon OR Sisaket)             →  1 บทความ
    suicide prevention AND (Yasothon OR Sisaket OR Thailand) → 10 บทความ
    suicide prevention rural community                        → 10 บทความ

Memory Agent เติม "ของจังหวัดยโสธรและศรีสะเกษ" จากประวัติแชท เข้าคำถามที่ผู้ใช้
ถามลอย ๆ ว่า "มีงานวิจัยอะไรเรื่องการป้องกันการฆ่าตัวตายในชุมชนชนบท"
"""
from src.agents.pubmed_agent import _drop_unasked_provinces, _strip_generic_geo_terms

Q = "มีงานวิจัยอะไรเรื่องการป้องกันการฆ่าตัวตายในชุมชนชนบท"


class TestDropUnaskedProvinces:
    def test_ตัดกลุ่มจังหวัดที่ไม่ได้ถามทิ้งทั้งกลุ่ม(self):
        out = _drop_unasked_provinces(
            "suicide prevention AND (Yasothon OR Sisaket)", Q)
        assert "Yasothon" not in out and "Sisaket" not in out
        assert "suicide prevention" in out

    def test_เหลือ_thailand_ไว้ถ้าอยู่ในกลุ่มเดียวกัน(self):
        """Thailand ยังช่วยให้เจอบทความ ต่างจากชื่อจังหวัดที่แทบไม่มีในวรรณกรรม"""
        out = _drop_unasked_provinces(
            "suicide AND (Yasothon OR Sisaket OR Thailand)", Q)
        assert "Thailand" in out
        assert "Yasothon" not in out and "Sisaket" not in out

    def test_เก็บจังหวัดที่ผู้ใช้ถามเอง(self):
        q = "มีงานวิจัยเรื่องพยาธิใบไม้ตับในอุบลราชธานีไหม"
        out = _drop_unasked_provinces(
            "Opisthorchis viverrini AND Ubon Ratchathani", q)
        assert "Ubon Ratchathani" in out

    def test_เก็บจังหวัดที่ผู้ใช้พิมพ์เป็นอังกฤษ(self):
        q = "research on liver fluke in Sisaket"
        assert "Sisaket" in _drop_unasked_provinces("liver fluke AND Sisaket", q)

    def test_ตัด_and_ที่ห้อยอยู่หลังตัดกลุ่มทิ้ง(self):
        out = _drop_unasked_provinces("suicide prevention AND Yasothon", Q)
        assert out.strip() == "suicide prevention"
        assert not out.strip().endswith("AND")

    def test_ไม่มีจังหวัดก็ไม่เปลี่ยนอะไร(self):
        q = "suicide prevention AND Thailand"
        assert _drop_unasked_provinces(q, Q) == q

    def test_ตัดหมดแล้วว่างต้องคืนของเดิม(self):
        """ค้นด้วย query ว่างยิ่งแย่กว่าค้นด้วยจังหวัด"""
        assert _drop_unasked_provinces("Yasothon", Q) == "Yasothon"


class TestExistingBehaviourUnchanged:
    def test_strip_generic_geo_ยังทำงานเหมือนเดิม(self):
        """ตั้งใจไม่แก้ — สำหรับคำถามที่ถามจังหวัดจริง การเอาบทความท้องถิ่น
        ขึ้นก่อนแล้วค่อยเติมด้วย query กว้างเป็นพฤติกรรมที่ถูกต้องอยู่แล้ว"""
        out = _strip_generic_geo_terms("suicide AND (Yasothon OR Thailand)")
        assert out == "suicide AND Yasothon"
