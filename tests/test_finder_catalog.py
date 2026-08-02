"""Tests: File Finder ต้องเห็น metadata ตอนเลือกไฟล์ ไม่ใช่เห็นแค่ชื่อโฟลเดอร์

ที่มา — วัดจริง 2026-08-03 ทั้ง 3 เคสเลือกไฟล์ผิดเพราะดูแค่ชื่อ:
  ถาม "ผู้ป่วยความดันควบคุมได้ดี"   → หยิบไฟล์ **เบาหวาน**
  ถาม "อัตราฆ่าตัวตายสำเร็จ"        → หยิบไฟล์ **ทำร้ายตนเองเข้าถึงบริการ**
  ถาม "ผู้ป่วยซึมเศร้าเข้าถึงบริการ" → หยิบไฟล์ **SMI-V**

ทั้งสามเคส `indicator_th` บอกไว้ถูกอยู่แล้ว แต่ metadata ถูกส่งให้ *หลัง* เลือกไฟล์
⇒ ตัวที่ต้องตัดสินใจกลับเป็นตัวที่ไม่มีข้อมูลประกอบ
"""
from src.tools import data_dict_lookup as dl


ROWS = [
    {"file_id": "111", "indicator_th": "ร้อยละผู้ป่วยโรคความดันโลหิตสูงที่ควบคุมได้ดี",
     "year_min": "2565", "year_max": "2569", "granularity": "อำเภอ",
     "n_prov": 5, "n_cav": 1, "vault_path": "D3_NCDs/x/y.csv"},
    {"file_id": "222", "indicator_th": "ร้อยละของผู้ป่วยเบาหวานที่มีความดันโลหิตควบคุมได้",
     "year_min": "2565", "year_max": "2569", "granularity": "อำเภอ",
     "n_prov": 5, "n_cav": 0, "vault_path": "D3_NCDs/x/z.csv"},
]


class TestCatalogForFinder:
    def test_แสดงชื่อตัวชี้วัดเต็มให้แยกไฟล์ที่ชื่อคล้ายกันออก(self, monkeypatch):
        monkeypatch.setattr("src.db.pool.query_db", lambda *a, **k: ROWS)
        out = dl.catalog_for_finder("d3")
        assert "ผู้ป่วยโรคความดันโลหิตสูงที่ควบคุมได้ดี" in out
        assert "ผู้ป่วยเบาหวานที่มีความดันโลหิตควบคุมได้" in out, (
            "ต้องเห็นทั้งคู่ถึงจะเลือกถูก — นี่คือคู่ที่เคยเลือกผิดจริง"
        )

    def test_บอก_id_ให้อ้างย้อนกลับได้(self, monkeypatch):
        monkeypatch.setattr("src.db.pool.query_db", lambda *a, **k: ROWS)
        out = dl.catalog_for_finder("d3")
        assert "[ID:111]" in out and "[ID:222]" in out

    def test_บอกปี_จังหวัด_ระดับ_เพื่อตัดตัวเลือกที่ไม่ครอบคลุม(self, monkeypatch):
        monkeypatch.setattr("src.db.pool.query_db", lambda *a, **k: ROWS)
        out = dl.catalog_for_finder("d3")
        assert "ปี 2565-2569" in out and "5 จว." in out and "ระดับอำเภอ" in out

    def test_ติดธงไฟล์ที่มีข้อควรระวัง(self, monkeypatch):
        """เห็นตั้งแต่ตอนเลือก จะได้เลี่ยงถ้ามีตัวเลือกอื่นที่ดีกว่า"""
        monkeypatch.setattr("src.db.pool.query_db", lambda *a, **k: ROWS)
        out = dl.catalog_for_finder("d3")
        line = [l for l in out.splitlines() if "[ID:111]" in l][0]
        assert "⚠️1" in line
        assert "⚠️" not in [l for l in out.splitlines() if "[ID:222]" in l][0]

    def test_อ่าน_db_ไม่ได้ต้องคืนค่าว่าง_ไม่ใช่ระเบิด(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("DB ล่ม")

        monkeypatch.setattr("src.db.pool.query_db", boom)
        assert dl.catalog_for_finder("d3") == ""

    def test_ไม่มีข้อมูลคืนค่าว่าง(self, monkeypatch):
        monkeypatch.setattr("src.db.pool.query_db", lambda *a, **k: [])
        assert dl.catalog_for_finder("d3") == ""


class TestFinderPromptUsesCatalog:
    def test_pipeline_ส่งสารบัญเข้าพรอมต์จริง(self):
        import inspect

        from src.agents import csv_pipeline

        src = inspect.getsource(csv_pipeline)
        assert "catalog_for_finder(domain.code)" in src
        assert "รายการชุดข้อมูลที่มีจริง" in src or "{catalog}" in src or "catalog}" in src, (
            "สารบัญต้องถูกแทรกเข้าพรอมต์ ไม่ใช่คำนวณทิ้ง"
        )
