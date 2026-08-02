"""Tests: ไฟล์ที่ถูกแทนที่ต้องหายจากสายตา File Finder

ที่มา — รันจริง 2026-08-03 ถาม "ผู้ป่วยความดันควบคุมได้ดี ที่อำเภอคำชะอี"
มี 3 ไฟล์ชื่อตัวชี้วัดแทบเหมือนกัน ให้คำตอบ 20.54% / 6.40% / 54.75% ต่างกัน 8 เท่า
ต้นเหตุ: ตาราง HDC เดียวถูกนำเข้าซ้ำระหว่างพัฒนา (30 ตาราง → 71 ไฟล์)

**ไม่ลบไฟล์ทิ้ง** เพราะบทสนทนาเก่าอ้าง file_id เดิม — แค่ซ่อนจากการค้นหา
"""
from src.tools import minio as m


INDEX = {
    "111": "D3_NCDs/โรคความดัน/ตัวชี้วัด ก/ใหม่.csv",
    "222": "D3_NCDs/โรคความดัน/ตัวชี้วัด ก/เก่า.csv",
    "333": "D3_NCDs/โรคไต/ตัวชี้วัด ข/ข.csv",
}


class TestListingSkipsSuperseded:
    def test_folder_tree_ไม่แสดงไฟล์ที่ถูกแทนที่(self, monkeypatch):
        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: INDEX)
        monkeypatch.setattr(m, "_superseded_ids", lambda: {"222"})
        out = m.list_csv_tree_impl("")
        assert "111" in out and "333" in out
        assert "222" not in out, "ไฟล์เก่าต้องไม่โผล่ให้ File Finder เลือก"

    def test_flat_listing_ไม่แสดงไฟล์ที่ถูกแทนที่(self, monkeypatch):
        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: INDEX)
        monkeypatch.setattr(m, "_superseded_ids", lambda: {"222"})
        out = m.list_csv_files_by_domain("D3_NCDs")
        assert out.count("[ID:") == 2
        assert "222" not in out

    def test_ไม่มีไฟล์ถูกแทนที่ก็แสดงครบ(self, monkeypatch):
        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: INDEX)
        monkeypatch.setattr(m, "_superseded_ids", lambda: set())
        assert m.list_csv_files_by_domain("").count("[ID:") == 3

    def test_อ่าน_db_ไม่ได้ต้องไม่ทำให้ค้นไม่เจออะไรเลย(self, monkeypatch):
        """ยอมให้เห็นไฟล์เกิน ดีกว่าระบบค้นไม่เจอทั้งหมด"""
        def boom(*a, **k):
            raise RuntimeError("DB ล่ม")

        monkeypatch.setattr(m, "_load_path_index", lambda *a, **k: INDEX)
        monkeypatch.setattr("src.db.pool.query_db", boom)
        assert m._superseded_ids() == set()
        assert m.list_csv_files_by_domain("").count("[ID:") == 3


class TestSearchSkipsSuperseded:
    def test_sql_กรอง_superseded_ออก(self):
        import inspect

        from src.tools import data_dict_lookup as dl

        src = inspect.getsource(dl.search_file_ids)
        assert "superseded_by IS NULL" in src, (
            "ถ้าเงื่อนไขนี้หาย File Finder จะเห็นไฟล์ซ้ำหลายเวอร์ชันอีก"
        )


class TestDedupeRule:
    def test_เกณฑ์เลือกไฟล์ที่เก็บ_เรียงตามปี_แถว_เวลา(self):
        """ครอบคลุมปีมากกว่าสำคัญกว่าจำนวนแถว — ไฟล์ที่มีปีครบใช้ทำแนวโน้มได้"""
        import datetime as dt

        g = [
            {"file_id": "a", "yrs": 5, "row_count": 900, "last_sync_at": dt.datetime(2026, 1, 1)},
            {"file_id": "b", "yrs": 10, "row_count": 100, "last_sync_at": dt.datetime(2025, 1, 1)},
            {"file_id": "c", "yrs": 10, "row_count": 100, "last_sync_at": dt.datetime(2026, 6, 1)},
        ]
        best = sorted(g, key=lambda r: (r["yrs"], r["row_count"], r["last_sync_at"]),
                      reverse=True)[0]
        assert best["file_id"] == "c", "ปีเท่ากัน แถวเท่ากัน → เอาตัวที่ซิงก์ล่าสุด"
