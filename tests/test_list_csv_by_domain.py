"""Tests: กรองไฟล์ตามโดเมนต้องกรองจาก path ไม่ใช่ชื่อ object

กับดักที่เอกสาร `04 - Data Architecture & Schema` เตือนไว้ แต่โค้ดยังทำผิดอยู่จน
2026-08-01: ชื่อ object ใน MinIO เป็นเลขล้วน (`264708`) โครงสร้างโฟลเดอร์อยู่ใน
`x-amz-meta-path` เท่านั้น ⇒ `list_csv_files_impl("D3_NCDs")` คืน "No CSV files found"
เสมอ แล้วผู้เรียกถอยไปดึงทั้ง bucket **ตัวกรองโดเมนหายไปเงียบ ๆ**
"""
from src.tools import minio as m


INDEX = {
    "111": "D3_NCDs/โรคไต/ตัวชี้วัด ก/ก.csv",
    "222": "D3_NCDs/โรคเบาหวาน/ตัวชี้วัด ข/ข.csv",
    "333": "D2_Mental Health/ผู้ป่วยสุขภาพจิต/ตัวชี้วัด ค/ค.csv",
    "444": "D5_Population/โครงสร้างประชากร/ตัวชี้วัด ง/ง.csv",
}


class TestListByDomain:
    def test_กรองตามโฟลเดอร์โดเมนได้จริง(self, monkeypatch):
        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: INDEX)
        out = m.list_csv_files_by_domain("D3_NCDs")
        assert out.count("[ID:") == 2
        assert "111" in out and "222" in out
        assert "333" not in out, "ไฟล์สุขภาพจิตต้องไม่หลุดเข้ามา"

    def test_ไม่ระบุโดเมนได้ทั้งหมด(self, monkeypatch):
        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: INDEX)
        assert m.list_csv_files_by_domain("").count("[ID:") == len(INDEX)

    def test_ชื่อโดเมนที่ไม่มีไฟล์คืนค่าว่าง_ให้ผู้เรียกถอยเอง(self, monkeypatch):
        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: INDEX)
        assert m.list_csv_files_by_domain("D9_NotExist") == ""

    def test_ไม่มี_path_index_ต้องไม่ระเบิด(self, monkeypatch):
        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: {})
        assert m.list_csv_files_by_domain("D3_NCDs") == ""

    def test_ผลลัพธ์มี_path_ให้_agent_อ่านชื่อตัวชี้วัดได้(self, monkeypatch):
        """AI เลือกชุดข้อมูลจากชื่อโฟลเดอร์ ไม่ใช่ชื่อไฟล์ที่มักถูกตัดสั้น"""
        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: INDEX)
        assert "D3_NCDs/โรคไต/ตัวชี้วัด ก" in m.list_csv_files_by_domain("D3_NCDs")
