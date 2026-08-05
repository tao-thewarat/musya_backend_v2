"""Tests: อุบัติเหตุ SQL — กันเขียน SQL ผิด และกันแต่งจังหวัดนอกเขต

ที่มา — ผู้ใช้ทดสอบ 2026-08-05 คำถามเดียวกัน 2 รอบ ผิดคนละแบบ:

รอบ 1: `column "province" does not exist`
       ⇒ AI ไปสรุปให้ผู้ใช้ว่า "ควรปรับปรุงโครงสร้างฐานข้อมูลให้มีคอลัมน์จังหวัด"
       ทั้งที่ DB ออกแบบถูกแล้ว (จังหวัดอยู่ dim_geography ต้อง join)

รอบ 2: เครื่องมือคืน **5 จังหวัด รวม 144 ราย**
       แต่ AI แสดง **7 จังหวัด** เพิ่ม สุรินทร์ 67 + บุรีรัมย์ 34 (เขต 9)
       ⇒ ตอบรวม **205 ราย เกินจริง 42%** และเขียนว่า "สุรินทร์สูงสุด"
"""
import json

from src.tools.accident_chat_sql import (
    ZONE10_PROVINCES, _sql_error_hint, find_foreign_provinces,
)


class TestForeignProvinceGuard:
    def test_จับจังหวัดที่_ai_แต่งเพิ่มได้(self):
        answer = "มุกดาหาร 36 · สุรินทร์ 67 · บุรีรัมย์ 34 · รวม 205 ราย"
        found = find_foreign_provinces(answer)
        assert "สุรินทร์" in found and "บุรีรัมย์" in found

    def test_คำตอบที่ถูกต้องต้องไม่ถูกจับ(self):
        answer = ("มุกดาหาร 36 · อุบลราชธานี 36 · ยโสธร 33 · "
                  "ศรีสะเกษ 28 · อำนาจเจริญ 11 · รวม 144 ราย")
        assert find_foreign_provinces(answer) == []

    def test_จังหวัดในเขตสิบต้องไม่อยู่ในบัญชีต้องห้าม(self):
        for p in ZONE10_PROVINCES:
            assert find_foreign_provinces(f"{p} 10 ราย") == [], p

    def test_ข้อความว่างไม่พัง(self):
        assert find_foreign_provinces("") == []
        assert find_foreign_provinces(None) == []


class TestSqlErrorHint:
    def test_บอกวิธีแก้เมื่อเดาคอลัมน์จังหวัดผิด(self):
        hint = _sql_error_hint('column "province" does not exist')
        assert "dim_geography" in hint and "JOIN" in hint.upper()

    def test_ห้ามให้สรุปว่าฐานข้อมูลขาดข้อมูล(self):
        """นี่คือความผิดพลาดที่ทำให้ AI เขียนข้อเสนอแนะนโยบายผิด"""
        hint = _sql_error_hint('column "province" does not exist')
        assert "ห้ามสรุปว่าฐานข้อมูลขาดข้อมูล" in hint

    def test_คอลัมน์อื่นที่เดาผิดบ่อยก็มีคำแนะนำ(self):
        assert "death_count" in _sql_error_hint('column "deaths" does not exist')
        assert "district_name" in _sql_error_hint('column "district" does not exist')
        assert "EXTRACT" in _sql_error_hint('column "year" does not exist')

    def test_คอลัมน์ที่ไม่รู้จักให้ไปดู_schema(self):
        assert "get_accident_schema" in _sql_error_hint('column "foo" does not exist')

    def test_ตารางผิดก็แนะนำ(self):
        assert "ตาราง" in _sql_error_hint('relation "abc" does not exist')

    def test_error_อื่นยังห้ามยอมแพ้(self):
        assert "ห้ามสรุปว่าข้อมูลไม่มี" in _sql_error_hint("syntax error at or near")


class TestSqlToolDocumentsSchema:
    def test_docstring_บอกว่าต้อง_join_หาจังหวัด(self):
        """LLM อ่าน docstring เป็นหลัก — ถ้าไม่บอกไว้ตรงนี้มันจะเดาเอง"""
        from src.tools.accident_chat_sql import execute_accident_sql
        doc = execute_accident_sql.description or ""
        assert "dim_geography" in doc
        assert "NO province" in doc or "ไม่มี" in doc
        assert "death_count" in doc

    def test_docstring_เตือนเรื่องขอบเขตเขตสิบ(self):
        from src.tools.accident_chat_sql import execute_accident_sql
        doc = execute_accident_sql.description or ""
        assert "Region 10" in doc or "เขต 10" in doc
        for p in ("อุบลราชธานี", "มุกดาหาร"):
            assert p in doc


class TestErrorPayloadCarriesHint:
    def test_ผลลัพธ์ที่ผิดพลาดต้องมี_hint(self):
        from src.tools.accident_chat_sql import execute_accident_sql
        out = json.loads(execute_accident_sql.run(sql_query="SELECT province FROM fact_accident_event"))
        assert out["success"] is False
        assert out.get("hint"), "ต้องบอกวิธีแก้ ไม่ใช่โยน error ดิบกลับไป"
        assert "dim_geography" in out["hint"]
