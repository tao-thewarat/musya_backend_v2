"""Tests: ย่อข้อความลิงก์แต่ยังกดไปที่ URL เต็มได้

ที่มา — ผู้ใช้รายงาน 2026-08-03: รายการเอกสารอ้างอิงอ่านไม่รู้เรื่อง เพราะ
URL ภาษาไทยถูกเข้ารหัสเป็น percent-encoding ยาว 300+ ตัวอักษรต่อรายการ
รายการอ้างอิง 8 รายการจึงกลืนทั้งหน้ารายงาน
"""
from src.tools.link_text import MAX_LEN, short_link_text, shorten_urls_markdown

SCRIBD = ("https://www.scribd.com/document/852131003/"
          "%E0%B8%A3%E0%B8%B2%E0%B8%A2%E0%B8%87%E0%B8%B2%E0%B8%99"
          "%E0%B8%9B%E0%B8%A3%E0%B8%B0%E0%B8%88%E0%B8%B3%E0%B8%9B%E0%B8%B5")
FB = ("https://www.facebook.com/Vajiravit/photos/"
      "%EF%B8%8F-%E0%B8%86%E0%B9%88%E0%B8%B2%E0%B8%95%E0%B8%B1%E0%B8%A7"
      "%E0%B8%95%E0%B8%B2%E0%B8%A2" * 4)
PDF = "https://dmh.go.th/intranet/p2567/policyDMH2567.pdf"


class TestShortLinkText:
    def test_ถอดรหัสไทยกลับมาอ่านได้(self):
        out = short_link_text(SCRIBD)
        assert "%E0%B8" not in out, "ต้องไม่เหลือ percent-encoding"
        assert "รายงาน" in out

    def test_ยาวไม่เกินลิมิต(self):
        for u in (SCRIBD, FB, PDF):
            assert len(short_link_text(u)) <= MAX_LEN, u[:40]

    def test_ตัดคำว่า_www_ออก(self):
        assert short_link_text(SCRIBD).startswith("scribd.com")

    def test_เก็บนามสกุลไฟล์ไว้บอกชนิดเอกสาร(self):
        assert short_link_text(PDF).endswith(".pdf")

    def test_url_สั้นอยู่แล้วไม่เพี้ยน(self):
        assert short_link_text("https://dmh.go.th/") == "dmh.go.th"

    def test_url_พังไม่ทำให้ระเบิด(self):
        for bad in ("", "ไม่ใช่ url", "http://"):
            assert isinstance(short_link_text(bad), str)


class TestShortenUrlsMarkdown:
    def test_แปลง_url_เปล่าเป็นลิงก์ที่มีข้อความสั้น(self):
        out = shorten_urls_markdown(f"1. กรมสุขภาพจิต. URL: {SCRIBD}")
        assert f"]({SCRIBD})" in out, "href ต้องเป็น URL เต็มทุกตัวอักษร"
        assert "%E0%B8%A3%E0%B8%B2%E0%B8%A2%E0%B8%87" not in out.split("](")[0]

    def test_ไม่แตะลิงก์ที่มีข้อความอยู่แล้ว(self):
        md = f"ดู [รายงานประจำปี]({SCRIBD}) ประกอบ"
        assert shorten_urls_markdown(md) == md

    def test_ไม่แตะ_url_ที่สั้นพออยู่แล้ว(self):
        md = "ดู https://dmh.go.th/ ประกอบ"
        assert shorten_urls_markdown(md) == md

    def test_หลาย_url_ในข้อความเดียว(self):
        md = f"1. {SCRIBD}\n2. {FB}"
        out = shorten_urls_markdown(md)
        assert out.count("](") == 2
        assert f"]({SCRIBD})" in out and f"]({FB})" in out

    def test_เครื่องหมายวรรคตอนท้ายไม่ถูกกลืนเข้าลิงก์(self):
        out = shorten_urls_markdown(f"ดูที่ {SCRIBD}.")
        assert f"]({SCRIBD})" in out and out.rstrip().endswith(".")
