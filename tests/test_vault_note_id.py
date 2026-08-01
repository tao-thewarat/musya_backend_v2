"""Regression tests: การหา note_id จาก path ของ vault

ล็อกบั๊กจริง: หน้า /pdf-upload กดดูโน้ตแล้วขึ้น "ไม่มีเนื้อหา" ทั้งที่ในฐานข้อมูลมี
เนื้อหาอยู่จริง (เช่น มุกดาหาร/มุกดาหาร.md มี 1,998 ตัวอักษร)

สาเหตุ: คลังมี note_id 2 รูปแบบปนกัน
  • จาก ingest PDF (754 ตัว) : "health_region_10::เขต10/.../ชื่อ"       ← ไม่มี .md
  • เขียนเองบนดิสก์ (526 ตัว): "health_region_10::มุกดาหาร/มุกดาหาร.md"  ← มี .md
แต่ _note_id_from_path() ตัด .md ทิ้งเสมอ → หาโน้ตตระกูลที่สองไม่เจอเลยสักตัว
กระทบทั้ง ดู/แก้ไข/ลบ/เปลี่ยนชื่อ เพราะทั้ง 4 endpoint ใช้ฟังก์ชันเดียวกัน
"""
import pytest

from src.routers import pdf_ingest
from src.routers.pdf_ingest import _strip_md, _note_id_from_path, VAULT_ID


class TestStripMd:
    """ตัด .md ต้องตัดเฉพาะท้ายสุด ไม่ใช่แทนที่ทุกตำแหน่ง"""

    def test_ตัดนามสกุลท้ายสุด(self):
        assert _strip_md("มุกดาหาร/มุกดาหาร.md") == "มุกดาหาร/มุกดาหาร"

    def test_ไม่มีนามสกุลก็ไม่เปลี่ยน(self):
        assert _strip_md("เขต10/ยโสธร/รายงาน") == "เขต10/ยโสธร/รายงาน"

    def test_ห้ามตัด_md_ที่อยู่กลางทาง(self):
        # เคสที่ .replace(".md","") ของเดิมทำ path เพี้ยน
        assert _strip_md("รายงาน.md-สรุป/ก.md") == "รายงาน.md-สรุป/ก"
        assert _strip_md("a.mdx/b.md") == "a.mdx/b"

    def test_ชื่อที่มีคำว่า_md_ปนต้องไม่โดนตัด(self):
        assert _strip_md("แผน.mdต้นฉบับ/x") == "แผน.mdต้นฉบับ/x"


class TestResolveNoteId:
    """ต้องหาเจอทั้ง note_id แบบมี .md และแบบไม่มี"""

    def _patch(self, monkeypatch, existing_ids, by_relpath=()):
        """จำลอง DB: คืนแถวเมื่อ note_id (หรือ relative_path) ตรงกับที่กำหนด"""
        def fake_query_db(sql, params):
            if "relative_path IN" in sql:
                _vault, rel_a, rel_b = params
                for rel in (rel_a, rel_b):
                    if rel in by_relpath:
                        return [{"note_id": by_relpath[rel]}]
                return []
            note_id = params[0]
            return [{"note_id": note_id}] if note_id in existing_ids else []
        monkeypatch.setattr(pdf_ingest, "query_db", fake_query_db)

    def test_เจอโน้ตที่_note_id_มี_md(self, monkeypatch):
        want = f"{VAULT_ID}::มุกดาหาร/มุกดาหาร.md"
        self._patch(monkeypatch, {want})
        # frontend ส่ง path มาแบบไม่มี .md (เพราะ list_vault_files ตัดออกไปแล้ว)
        assert pdf_ingest._resolve_note_id("มุกดาหาร/มุกดาหาร") == want

    def test_เจอโน้ตที่_note_id_ไม่มี_md(self, monkeypatch):
        want = f"{VAULT_ID}::เขต10/ยโสธร/รายงาน/รายงาน"
        self._patch(monkeypatch, {want})
        assert pdf_ingest._resolve_note_id("เขต10/ยโสธร/รายงาน/รายงาน") == want

    def test_ส่ง_path_มาพร้อม_md_ก็ยังต้องเจอ(self, monkeypatch):
        want = f"{VAULT_ID}::มุกดาหาร/มุกดาหาร.md"
        self._patch(monkeypatch, {want})
        assert pdf_ingest._resolve_note_id("มุกดาหาร/มุกดาหาร.md") == want

    def test_แบบมี_md_ต้องถูกลองก่อน(self, monkeypatch):
        """ถ้ามีทั้งสองแบบในคลัง ต้องได้แบบมี .md ตามลำดับที่กำหนด"""
        with_md = f"{VAULT_ID}::x/y.md"
        without = f"{VAULT_ID}::x/y"
        self._patch(monkeypatch, {with_md, without})
        assert pdf_ingest._resolve_note_id("x/y") == with_md

    def test_ทางสำรองค้นด้วย_relative_path(self, monkeypatch):
        """note_id ที่ไม่ได้ตั้งตามสูตร VAULT_ID::relative_path ก็ยังต้องหาเจอ"""
        odd_id = "legacy-id-12345"
        self._patch(monkeypatch, set(), by_relpath={"มุกดาหาร/มุกดาหาร.md": odd_id})
        assert pdf_ingest._resolve_note_id("มุกดาหาร/มุกดาหาร") == odd_id

    def test_ไม่มีจริงต้องคืน_None_ไม่ใช่โยน_Error(self, monkeypatch):
        self._patch(monkeypatch, set())
        assert pdf_ingest._resolve_note_id("ไม่มี/อยู่จริง") is None


class TestNoteIdFromPath:
    """ตัวสร้าง note_id สำหรับโน้ตใหม่ ยังต้องได้รูปแบบเดิม (ไม่มี .md)"""

    def test_รูปแบบมาตรฐานสำหรับโน้ตใหม่(self):
        assert _note_id_from_path("a/b.md") == f"{VAULT_ID}::a/b"
        assert _note_id_from_path("a/b") == f"{VAULT_ID}::a/b"
