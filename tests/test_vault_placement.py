"""Tests: จัดไฟล์ HDC ลงโดเมนให้ถูก และกัน "/" ทำ path พัง

ที่มา — ผู้ใช้จับได้จากหน้า /fileapa เมื่อ 2026-07-31:
  1. "ร้อยละของผู้ป่วย DM และ/หรือ HT ..." กลายเป็นโฟลเดอร์ซ้อน 3 ชั้น
     เพราะ "/" ในชื่อตัวชี้วัดถูกตีความเป็นตัวคั่นโฟลเดอร์
  2. ไฟล์ไปกองที่ "HDC/" แทนที่จะเข้าโดเมนจริงที่คลังใช้อยู่ (D2/D3/D4)
"""
from src.tools.vault_placement import D2, D3, D4, OTHER, build_vault_path, classify, safe_segment

BAD = "ร้อยละของผู้ป่วย DM และ/หรือ HT ที่ได้รับการค้นหาและคัดกรองโรคไตเรื้อรัง"


class TestSafeSegment:
    def test_สแลชต้องกลายเป็นช่องว่าง_ไม่ใช่ถูกลบทิ้ง(self):
        """ผู้ใช้ระบุเองว่าอยากได้ 'DM และ หรือ HT' — ต้องอ่านออกและยังค้นเจอด้วยคำเดิม"""
        got = safe_segment(BAD)
        assert "/" not in got
        assert "DM และ หรือ HT" in got

    def test_ยุบช่องว่างซ้ำและตัดจุดท้ายชื่อ(self):
        assert safe_segment("  ก   ข  ") == "ก ข"
        assert safe_segment("ชื่อไฟล์.") == "ชื่อไฟล์"

    def test_แบ็กสแลชและอักขระควบคุมต้องหายไปด้วย(self):
        assert "\\" not in safe_segment("ก\\ข")
        assert "\n" not in safe_segment("ก\nข")


class TestClassify:
    def test_ตัวชี้วัดไตเข้า_d3_โรคไต(self):
        assert classify(BAD) == (D3, "โรคไต")

    def test_ใช้ชื่อหมวดช่วยเมื่อชื่อตัวชี้วัดไม่บอกโรค(self):
        """'CKD 2.7 ... ตรวจ serum K' ไม่มีคำว่าไต แต่หมวดบอกว่าสาขาไต"""
        d, sub = classify("CKD 2.7 การชะลอความเสื่อม ผู้ป่วยได้รับการตรวจ serum K",
                          "ข้อมูลเพื่อตอบสนอง Service Plan สาขาไต")
        assert (d, sub) == (D3, "โรคไต")

    def test_สุขภาพจิตเข้า_d2(self):
        assert classify("ร้อยละของผู้ป่วยโรคซึมเศร้าเข้าถึงบริการ")[0] == D2

    def test_โภชนาการแยกตามกลุ่มวัย(self):
        assert classify("ร้อยละของประชากรผู้สูงอายุ 60 ปีขึ้นไป มีรอบเอวปกติ") == (D4, "ผู้สูงอายุ")
        assert classify("ร้อยละหญิงตั้งครรภ์ ได้รับยาเม็ดไอโอดีน") == (D4, "หญิงตั้งครรภ์")
        assert classify("ภาวะโภชนาการของเด็กอายุ 0 - 2 ปี ดัชนีน้ำหนัก") == (D4, "เด็ก 0-2 ปี")

    def test_ที่ไม่เข้าพวกไหนไปโดเมนใหม่_ไม่ใช่กองรวมที่_hdc(self):
        d, _ = classify("รายงานการตรวจเอ็กซเรย์ปอดฟิล์มใหญ่", "อาชีวอนามัย")
        assert d == OTHER


class TestBuildVaultPath:
    def test_path_ที่ได้ต้องไม่มีสแลชเกินและอยู่ใต้โดเมนจริง(self):
        folder, name = build_vault_path(BAD, "ข้อมูลเพื่อตอบสนอง Service Plan สาขาไต")
        assert folder.startswith(f"{D3}/โรคไต/")
        assert folder.count("/") == 2, "ต้องเป็น โดเมน/กลุ่มโรค/ชื่อตัวชี้วัด เท่านั้น"
        assert "/" not in name

    def test_ความยาวรวมต้องไม่เกินลิมิต(self):
        """เกิน 150 แล้ว x-amz-meta-path โดนตัด folder tree พัง ไฟล์หาไม่เจอ"""
        for title in (BAD, "ก" * 300, "ข" * 80):
            folder, name = build_vault_path(title, "สาขาไต")
            assert len(f"{folder}/{name}.csv") <= 150

    def test_ชื่อยาวมากให้ตัดชั้นโฟลเดอร์ทิ้งก่อนตัดชื่อไฟล์จนอ่านไม่ออก(self):
        folder, name = build_vault_path("ก" * 300, "สาขาไต")
        assert folder.count("/") <= 1, "ต้องถอยชั้นโฟลเดอร์เมื่อที่ไม่พอ"
        assert len(name) > 12
