"""Regression tests: การอ่าน YAML frontmatter ของโน้ต

ล็อกบั๊กจริง: ไฟล์ .md 83 จาก 533 ไฟล์ในคลังขึ้นต้นด้วย BOM (U+FEFF) ทำให้
content.startswith("---") เป็น False → frontmatter ไม่ถูกอ่านทั้งบล็อก
→ เสีย title / tags / year และ **source/source_file** ซึ่งเป็นตัวเชื่อมโน้ตกับ PDF ใน MinIO
ผลคือโน้ตเหล่านั้นผูกลิงก์เอกสารไม่ได้เลย ทั้งที่ระบุที่มาไว้ครบในไฟล์
"""
from src.scripts.index_obsidian import _parse_frontmatter

FM = (
    "---\n"
    "title: ULTRA_DETAILED_ตรวจราชการ_รอบ2_2566\n"
    "tags:\n"
    "  - pdf\n"
    'source: "[[PDF/อำนาจเจริญ/รอบที่ 2 ปี 66.pdf]]"\n'
    "---\n"
    "\n# รายงานการตรวจราชการ\nเนื้อหา\n"
)


def _source_text(meta: dict) -> str:
    """source อาจเป็น str หรือ list — ค่า "[[PDF/...]]" ถูก parser มองเป็น inline list
    (นี่คือที่มาของรูปแบบ {"[PDF/จว/x.pdf]"} ที่เห็นในคอลัมน์ source_file ของ DB)"""
    v = meta.get("source") or meta.get("source_file") or ""
    return " ".join(v) if isinstance(v, list) else str(v)


class TestParseFrontmatter:
    def test_อ่าน_frontmatter_ปกติได้(self):
        meta, body = _parse_frontmatter(FM)
        assert meta["title"] == "ULTRA_DETAILED_ตรวจราชการ_รอบ2_2566"
        assert "รอบที่ 2 ปี 66.pdf" in _source_text(meta)
        assert body.startswith("# รายงานการตรวจราชการ")

    def test_ไฟล์ที่ขึ้นต้นด้วย_BOM_ต้องอ่านได้เหมือนกัน(self):
        meta, body = _parse_frontmatter("﻿" + FM)
        assert meta.get("title") == "ULTRA_DETAILED_ตรวจราชการ_รอบ2_2566"
        assert "รอบที่ 2 ปี 66.pdf" in _source_text(meta)
        assert body.startswith("# รายงานการตรวจราชการ")

    def test_BOM_กับไม่มี_BOM_ต้องได้ผลเหมือนกันทุกฟิลด์(self):
        assert _parse_frontmatter(FM) == _parse_frontmatter("﻿" + FM)

    def test_ไม่มี_frontmatter_ต้องคืน_meta_ว่างและเนื้อหาครบ(self):
        text = "# หัวข้อ\nเนื้อหาล้วน"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_BOM_แต่ไม่มี_frontmatter(self):
        meta, body = _parse_frontmatter("﻿# หัวข้อ\nเนื้อหา")
        assert meta == {}
        assert body.startswith("# หัวข้อ")
