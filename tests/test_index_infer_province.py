"""Regression tests: การเดาจังหวัด/อำเภอจาก path ของไฟล์ใน vault

ล็อกบั๊กจริง: หลังย้ายโครงสร้าง vault จาก "<จังหวัด>/..." เป็น "เขต10/<จังหวัด>/..."
ตัว re-index คำนวณ province จาก segment แรกของ path จึงได้ค่าเป็น "เขต10" ทุกตัว
→ โน้ต 525 ตัวหลุดออกจากการจัดกลุ่มรายจังหวัดทั้งหมด (หายไปจาก vault tree)
"""
from pathlib import Path

from src.scripts.index_obsidian import _infer_province, _infer_district

ROOT = Path("/vault")


class TestInferProvince:
    def test_โครงสร้างใหม่ต้องข้ามโฟลเดอร์ระดับเขต(self):
        p = ROOT / "เขต10" / "มุกดาหาร" / "สรุปผลรายไฟล์" / "งานวิจัย" / "x.md"
        assert _infer_province(p, ROOT) == "มุกดาหาร"

    def test_โครงสร้างเก่ายังต้องได้ผลเหมือนเดิม(self):
        p = ROOT / "ศรีสะเกษ" / "อ.ขุขันธ์" / "อ.ขุขันธ์.md"
        assert _infer_province(p, ROOT) == "ศรีสะเกษ"

    def test_ไฟล์ที่ราก_vault_ไม่มีจังหวัด(self):
        assert _infer_province(ROOT / "000_MOC.md", ROOT) is None

    def test_ไฟล์ใต้เขต10โดยตรงไม่นับเป็นจังหวัด(self):
        # เขต10/x.md — ใต้เขตแต่ไม่มีชั้นจังหวัด
        assert _infer_province(ROOT / "เขต10" / "x.md", ROOT) is None

    def test_จังหวัดที่ชื่อขึ้นต้นคล้ายเขตต้องไม่โดนข้าม(self):
        p = ROOT / "เขต10" / "ส่วนกลาง" / "แผน" / "x.md"
        assert _infer_province(p, ROOT) == "ส่วนกลาง"


class TestInferDistrict:
    def test_โครงสร้างใหม่หาอำเภอเจอ(self):
        p = ROOT / "เขต10" / "ศรีสะเกษ" / "อ.ขุขันธ์" / "อ.ขุขันธ์.md"
        assert _infer_district(p, ROOT) == "ขุขันธ์"

    def test_โครงสร้างเก่าหาอำเภอเจอ(self):
        p = ROOT / "ศรีสะเกษ" / "อ.ขุขันธ์" / "อ.ขุขันธ์.md"
        assert _infer_district(p, ROOT) == "ขุขันธ์"

    def test_ไม่มีชั้นอำเภอต้องคืน_None(self):
        p = ROOT / "เขต10" / "ยโสธร" / "สรุปผลรายไฟล์" / "x.md"
        assert _infer_district(p, ROOT) is None
