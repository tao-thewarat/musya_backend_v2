"""Tests: แกนเชื่อมข้อมูลข้าม CSV — พื้นที่ + ปี

ที่มา (สำรวจคลังจริง 45 ไฟล์ · 706 คอลัมน์ เมื่อ 2026-07-31):

1. **บั๊กเงียบเรื่องปี** — 36 จาก 45 ไฟล์มีมิติเวลา แต่ `_GEO_SYNONYMS` มีแต่คำเกี่ยวกับ
   สถานที่ ไม่มี "ปี" เลย ⇒ merge บนจังหวัดอย่างเดียว ข้อมูล 5 ปีของสองไฟล์จะถูก
   จับคู่ข้ามปีกันเป็น cartesian product ตัวเลขผิดโดยไม่มีสัญญาณเตือนใด ๆ

2. **ชื่อคอลัมน์ปีไม่ตรงกัน 4 แบบ** — `ปี` · `ปี พ.ศ.` · `ปีงบประมาณ` · `Year_BE`

3. **3 ไฟล์เชื่อมไม่ได้เลย** เพราะใช้ชื่อคอลัมน์นอกมาตรฐาน:
   `a_name` (476686 โรคไต, 802827 ตรวจไตในเบาหวาน) และ
   `sub-province` (141988 ผู้ป่วยพยายามฆ่าตัวตาย)
"""
from src.agents.multi_csv_pipeline import (
    _build_merge_recipe,
    _detect_geo_keys,
    _detect_year_keys,
)


def _schema(index, cols, sample=None):
    return {"index": index, "cols": cols, "sample": sample or []}


class TestYearKeyDetection:
    def test_จับคอลัมน์ปีได้ครบทุกแบบที่พบจริงในคลัง(self):
        """ตรวจกับหัวคอลัมน์จริงทั้ง 45 ไฟล์แล้ว — ครอบคลุม 44/45
        (เหลือ 570454 ค่ามาตรฐาน BMI ที่เป็นตารางอ้างอิง ไม่มีมิติเวลา)

        ⚠️ `ปี_พศ` (ไม่มีจุด) เคยตกสำรวจรอบแรก ทำให้ 2 ไฟล์เชื่อมด้วยปีไม่ได้ —
        อย่าตัดออกเพราะคิดว่าซ้ำกับ `ปี พ.ศ.`
        """
        for col in ("ปี", "ปี พ.ศ.", "ปี_พศ", "ปีงบประมาณ", "ปี_ข้อมูล", "Year_BE"):
            keys = _detect_year_keys([_schema(1, ["จังหวัด", col, "ค่า"])])
            assert keys == {"df1": col}, f"จับคอลัมน์ {col!r} ไม่ได้"

    def test_ไม่มีคอลัมน์ปีต้องไม่เดามั่ว(self):
        assert _detect_year_keys([_schema(1, ["จังหวัด", "อำเภอ", "ค่า"])]) == {}

    def test_ห้ามจับคอลัมน์ที่แค่มีคำว่าปีอยู่ข้างใน(self):
        """เทียบตรงทั้งคำ ไม่ใช่ substring — 'ปี' สั้นมาก ถ้าใช้ substring จะไปโดน
        คอลัมน์อย่าง 'ประชากรรายปี' หรือ 'ปีที่ผ่านมา' ที่ไม่ใช่แกนเวลา
        """
        keys = _detect_year_keys([_schema(1, ["จังหวัด", "ประชากรรายปี", "ผู้ป่วยปีที่ผ่านมา"])])
        assert keys == {}


class TestGeoSynonymsExtended:
    """3 ไฟล์ที่เคยเชื่อมไม่ได้ ต้องเชื่อมได้แล้ว"""

    def test_a_name_ถูกจับเป็นแกนพื้นที่(self):
        # ไฟล์ 476686 / 802827 ใช้ a_name แทน 'อำเภอ'
        keys = _detect_geo_keys([_schema(1, ["ปี_ข้อมูล", "a_name", "allstage"])])
        assert keys == {"df1": "a_name"}

    def test_sub_province_ถูกจับเป็นแกนพื้นที่(self):
        # ไฟล์ 141988 ใช้ province/sub-province ตัวพิมพ์เล็ก
        keys = _detect_geo_keys([_schema(1, ["province", "sub-province", "male"])])
        assert keys["df1"] in ("province", "sub-province")


class TestMergeRecipe:
    def test_ทุกไฟล์มีปีต้องเชื่อมสองแกน(self):
        recipe = _build_merge_recipe(
            {"df1": "จังหวัด", "df2": "จังหวัด"},
            {"df1": "ปีงบประมาณ", "df2": "ปีงบประมาณ"},
        )
        assert "on=['จังหวัด', 'ปีงบประมาณ']" in recipe
        assert "เชื่อมแค่พื้นที่จะจับคู่ข้อมูลข้ามปีกัน" in recipe

    def test_ชื่อคอลัมน์ปีต่างกันต้องสั่ง_rename_ก่อน(self):
        recipe = _build_merge_recipe(
            {"df1": "จังหวัด", "df2": "Province"},
            {"df1": "ปีงบประมาณ", "df2": "Year_BE"},
        )
        assert "rename" in recipe.lower()
        assert "Year_BE" in recipe

    def test_มีบางไฟล์ไม่มีปีต้องไม่ใส่ปีเข้า_merge(self):
        """ขาดปีแม้ไฟล์เดียวแล้วยังใส่ปีเข้าไป จะ merge ได้ 0 แถว
        ซึ่งแย่กว่าการเชื่อมด้วยพื้นที่อย่างเดียว
        """
        recipe = _build_merge_recipe(
            {"df1": "จังหวัด", "df2": "จังหวัด"},
            {"df1": "ปีงบประมาณ"},          # df2 ไม่มีปี
        )
        assert "on='จังหวัด'" in recipe
        assert "รวมข้ามปี" in recipe, "ต้องเตือนผู้ใช้ว่าตัวเลขเป็นการรวมข้ามปี"

    def test_สั่งแปลงชนิดข้อมูลของปีให้ตรงกันก่อน_merge(self):
        """ปีเก็บเป็น str บ้าง int บ้าง — ถ้าไม่แปลงก่อน merge จะไม่ตรงกันเลยสักแถว"""
        recipe = _build_merge_recipe(
            {"df1": "จังหวัด", "df2": "จังหวัด"},
            {"df1": "ปี", "df2": "ปี"},
        )
        assert "astype(str)" in recipe

    def test_เชื่อมไม่ได้ต้องสั่งให้บอกผู้ใช้ตรงๆ(self):
        """เดิมคืนแค่คอมเมนต์ว่า 'วิเคราะห์แยกกัน' เงียบ ๆ ผู้ใช้ที่ถามคำถามสองมิติ
        จะได้คำตอบสองก้อนที่ไม่เกี่ยวกัน โดยไม่มีใครบอกว่าเชื่อมไม่สำเร็จ
        """
        recipe = _build_merge_recipe({}, {})
        assert "ไม่สามารถเชื่อมข้อมูล" in recipe
        assert "ห้ามนำเสนอผลเหมือนว่าวิเคราะห์ร่วมกันสำเร็จ" in recipe
