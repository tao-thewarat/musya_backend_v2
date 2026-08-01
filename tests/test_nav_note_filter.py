"""Regression tests: โน้ตนำทาง (INDEX / MOC) ต้องไม่ถูกใช้เป็นแหล่งอ้างอิง

ที่มา: ผู้ใช้เจอบรรณานุกรม APA ในแชทขึ้นรายการแบบนี้
    1. MOC_5ยุทธศาสตร์ [เอกสาร]. (2569). คลังความรู้ MUSYA เขตสุขภาพที่ 10.
    2. MOC_นโยบายผู้บริหาร [เอกสาร]. (ม.ป.ป.). คลังความรู้ MUSYA เขตสุขภาพที่ 10.

MOC (Map of Content) กับ *-INDEX เป็น "สารบัญลิงก์" ไม่ใช่เอกสารต้นทาง —
เนื้อหาจริงมีแค่หลักร้อยตัวอักษรและเป็นรายชื่อลิงก์ล้วน ถ้าถูกเลือกจะ
กินโควตา context ฟรี ๆ แล้วยังโผล่เป็นรายการอ้างอิงที่ตามกลับไปไม่ได้
"""
from src.agents import obsidian_fullcontext as ofc


class TestNavNoteFilter:
    def test_ตัวกรองถูกใส่ในคิวรีค้นหา(self):
        captured = {}

        def fake_query_db(sql, params):
            captured["sql"] = sql
            return []

        original = ofc.query_db
        ofc.query_db = fake_query_db
        try:
            ofc._search_notes("health_region_10", None, ["พยาธิใบไม้ตับ"])
        finally:
            ofc.query_db = original

        sql = captured["sql"]
        assert "is_index" in sql, "ต้องกรองโน้ต INDEX ที่ระบบสร้างเอง"
        assert "'MOC'" in sql, "ต้องกรอง note_type = MOC"
        assert "MOC" in sql and "relative_path" in sql, "ต้องกรองไฟล์ชื่อ MOC_* ด้วย"

    def test_ตัวกรองถูกใส่ในทางสำรองที่โหลดทั้ง_vault(self):
        """ทางสำรอง (_load_all_notes) ใช้ตอนคัดกรองด้วยคำค้นไม่ได้ — ถ้าลืมกรอง
        ตรงนี้ MOC จะไหลกลับเข้ามาทางประตูหลัง
        """
        seen: list[str] = []

        def fake_query_db(sql, params):
            seen.append(sql)
            return []

        original = ofc.query_db
        ofc.query_db = fake_query_db
        try:
            ofc._load_all_notes("health_region_10", "อุบลราชธานี")
        finally:
            ofc.query_db = original

        assert seen, "ต้องมีการคิวรีเกิดขึ้น"
        assert all("is_index" in s for s in seen), "ทุกคิวรีในทางสำรองต้องกรองโน้ตนำทาง"

    def test_ตัวกรองครอบทั้งสามเงื่อนไข(self):
        f = ofc._NAV_NOTE_FILTER
        assert "coalesce(is_index, false) = false" in f
        assert "coalesce(note_type, '') <> 'MOC'" in f
        assert "NOT LIKE" in f


class TestFilterRunsAgainstRealDb:
    r"""เจอจริง: เขียน `NOT LIKE 'MOC\_%'` ด้วย % เดี่ยว ทำให้ psycopg มองว่าเป็น
    placeholder ของพารามิเตอร์ ทุกคิวรีที่ใช้ตัวกรองนี้จึงระเบิดด้วย
    "IndexError: tuple index out of range" = ค้นคลังความรู้พังทั้งระบบแบบเงียบ ๆ

    เทสต์เดิมตรวจแค่ว่า "มีข้อความนี้ใน SQL ไหม" จึงจับไม่ได้เลย
    """

    def test_ต้อง_escape_percent_เป็นสองตัว(self):
        f = ofc._NAV_NOTE_FILTER
        assert "%%" in f, "ต้องเขียน %% ไม่งั้น psycopg จะตีความเป็น placeholder"
        # ห้ามมี % เดี่ยวหลงเหลือ — นับให้ทุกตัวมาเป็นคู่
        assert f.count("%") % 2 == 0, "มี % เดี่ยวหลงเหลือ คิวรีจะพัง"

    def test_คิวรีจริงต้อง_bind_พารามิเตอร์ได้ไม่ระเบิด(self):
        """จำลอง psycopg: นับ placeholder ที่เหลือหลังหัก %% แล้วต้องตรงกับจำนวน params"""
        captured = {}

        def fake_query_db(sql, params):
            # %s ที่เหลือหลังตัด %% ออก = placeholder จริงที่ psycopg จะพยายาม bind
            captured["placeholders"] = sql.replace("%%", "").count("%s")
            captured["n_params"] = len(params)
            return []

        original = ofc.query_db
        ofc.query_db = fake_query_db
        try:
            ofc._search_notes("health_region_10", "อุบลราชธานี", ["พยาธิใบไม้ตับ"])
        finally:
            ofc.query_db = original

        assert captured["placeholders"] == captured["n_params"], (
            f"placeholder {captured['placeholders']} ตัว แต่ส่ง params "
            f"{captured['n_params']} ตัว — psycopg จะ raise IndexError"
        )
