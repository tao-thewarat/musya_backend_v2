"""Regression tests: PDF สไลด์ / ไฟล์ใหญ่ และการไม่ล้มเหลวแบบเงียบ

ที่มา: Gemini ตอบ 400 INVALID_ARGUMENT กับ PDF ขนาด 92–95 MB (สไลด์นำเสนอที่ฝัง
รูปความละเอียดสูง) แล้วโค้ดเดิม "จับ exception แล้วคืนโน้ตเปล่า" ทำให้ job รายงาน
completed ทั้งที่ไม่มีเนื้อหา — เจอโน้ตเปล่า 51 ใบจาก 23 เอกสารกว่าจะรู้ตัว
"""
import pytest

from src.routers import pdf_ingest
from src.routers.pdf_ingest import _ai_convert_to_markdown, _as_contents, _looks_like_slides


class _Resp:
    def __init__(self, text):
        self.text = text


class _Client:
    """Gemini จำลอง — บันทึก contents ที่ถูกส่งไว้ให้ตรวจ"""

    def __init__(self, text="# ok\n\nเนื้อหา", err=None):
        self.text, self.err, self.seen = text, err, []
        self.models = self

    def generate_content(self, model, contents, config):
        self.seen.append(contents)
        if self.err:
            raise self.err
        return _Resp(self.text)


def _convert(client, **kw):
    base = dict(
        client=client, uploaded_file=object(), chunk_index=1, total_chunks=1,
        base_filename="เอกสารทดสอบ", page_start=1, page_end=4,
        prev_link=None, next_link=None, province="มุกดาหาร", district=None,
        location_confidence="manual",
    )
    base.update(kw)
    return _ai_convert_to_markdown(**base)


class TestNoSilentFailure:
    def test_แปลงพังต้อง_raise_ไม่ใช่คืนโน้ตเปล่า(self):
        client = _Client(err=RuntimeError("400 INVALID_ARGUMENT"))
        with pytest.raises(RuntimeError):
            _convert(client)

    def test_ai_คืนข้อความว่างต้องถือว่าล้มเหลว(self):
        """ตอบ 200 แต่เนื้อหาว่าง = โน้ตเปล่าเหมือนกัน ต้องไม่ปล่อยผ่าน"""
        for empty in ("", "   \n  ", None):
            with pytest.raises(RuntimeError):
                _convert(_Client(text=empty))


class TestImageMode:
    def test_ส่งภาพแทน_pdf_เมื่อระบุ_page_images(self):
        client = _Client()
        _convert(client, page_images=[b"\xff\xd8jpeg1", b"\xff\xd8jpeg2"])
        contents = client.seen[0]
        # 2 ภาพ + 1 prompt และต้องไม่มี uploaded_file ปนไปด้วย
        assert len(contents) == 3
        assert isinstance(contents[-1], str)

    def test_ไม่มีภาพให้ใช้_pdf_ทั้งไฟล์ตามเดิม(self):
        client = _Client()
        sentinel = object()
        _convert(client, uploaded_file=sentinel, page_images=None)
        assert client.seen[0][0] is sentinel

    def test_พรอมป์สไลด์สั่งให้อธิบายภาพ(self):
        client = _Client()
        _convert(client, page_images=[b"x"], is_slides=True)
        prompt = client.seen[0][-1]
        assert "สไลด์นำเสนอ" in prompt
        assert "แผนภูมิ" in prompt

        client2 = _Client()
        _convert(client2, page_images=[b"x"], is_slides=False)
        assert "สไลด์นำเสนอ" not in client2.seen[0][-1]

    def test_as_contents_รับได้ทั้งไฟล์และรายการภาพ(self):
        assert _as_contents(None) == []
        f = object()
        assert _as_contents(f) == [f]
        assert len(_as_contents([b"a", b"b"])) == 2


class TestSlideDetection:
    def test_ข้อความแน่นถือว่าเป็นเอกสารไม่ใช่สไลด์(self):
        pages = ["ก" * 3000 for _ in range(10)]
        assert _looks_like_slides(b"%PDF-fake", pages) is False

    def test_สไลด์ข้อความค่อนข้างเยอะยังต้องถูกจับได้(self):
        """สไลด์ตัวอย่างจริงเฉลี่ย 951 ตัวอักษร/หน้า เกณฑ์เดิม 900 ทำให้หลุด"""
        assert pdf_ingest._SLIDE_MAX_CHARS_PER_PAGE > 951

    def test_ไม่มีหน้าเลยต้องไม่พัง(self):
        assert _looks_like_slides(b"%PDF-fake", []) is False

    def test_อ่านขนาดหน้าไม่ได้ต้องตอบ_false_ไม่ใช่ระเบิด(self):
        """ไฟล์เสีย/ไม่ใช่ PDF ต้องตกกลับไปโหมดเอกสารปกติ ไม่ใช่ทำ ingest ล้ม"""
        assert _looks_like_slides(b"not a pdf at all", ["สั้น"]) is False


class TestSlideChunkSize:
    def test_สไลด์ใช้_chunk_เล็กกว่าเอกสาร(self):
        """สไลด์เนื้อหาต่อหน้าแน่น ถ้าใช้ 20 หน้า/ส่วนจะโดน max_output_tokens ตัด"""
        assert pdf_ingest._SLIDE_CHUNK_PAGES < 20


class TestRenderGuards:
    def test_จำกัดด้านยาวของภาพ(self):
        """สไลด์แผ่นใหญ่เรนเดอร์เต็ม DPI จะได้ภาพหลายพันพิกเซลและกินแรม ~370MB/หน้า"""
        assert pdf_ingest._MAX_RENDER_PX <= 2000

    def test_เรนเดอร์ทีละหน้าทั้งโปรเซส(self):
        """chunk รันขนานอยู่แล้ว ถ้าเรนเดอร์พร้อมกันด้วยแรมจะทวีคูณ"""
        assert pdf_ingest._render_lock is not None
        assert pdf_ingest._render_lock.acquire(blocking=False)
        pdf_ingest._render_lock.release()


class TestDegenerateOutput:
    """เจอจริง: โน้ตหนึ่งได้ 128,067 ตัวอักษรจาก 15 หน้า เพราะโมเดลพ่นจุดยาวเป็นหมื่นตัว
    เนื้อหาแบบนี้ "ไม่ว่าง" จึงผ่านการเช็คเดิม แต่ก็ใช้อ้างอิงอะไรไม่ได้เลย
    """

    def test_จับเนื้อหาที่วนซ้ำได้(self):
        assert pdf_ingest._is_degenerate("# หัวข้อ\n\n" + "." * 500)
        assert pdf_ingest._is_degenerate("-" * 200)

    def test_เนื้อหาปกติต้องไม่ถูกตัดสินว่าวนซ้ำ(self):
        ok = "# รายงาน\n\n| ก | ข |\n|---|---|\n| 1 | 2 |\n\n" + "เนื้อหาภาษาไทยปกติ " * 200
        assert not pdf_ingest._is_degenerate(ok)

    def test_เส้นคั่นตารางยาวปกติต้องผ่าน(self):
        """ตาราง Markdown มี --- ติดกันได้ แต่ไม่ควรยาวถึงหลักร้อย"""
        assert not pdf_ingest._is_degenerate("|" + "-" * 40 + "|")

    def test_แปลงแล้ววนซ้ำต้อง_raise(self):
        client = _Client(text="# ok\n\n" + "." * 300)
        with pytest.raises(RuntimeError):
            _convert(client)
        # ต้องไล่ temperature ครบทุกขั้นก่อนยอมแพ้ ไม่ใช่ยิงครั้งเดียวแล้วเลิก
        assert len(client.seen) == len(pdf_ingest._CONVERT_TEMPERATURES)


class TestRetryEscapesLoop:
    """หน้าสแกนล้วนทำให้โมเดลวนลูป และที่ temperature ต่ำจะวนซ้ำเดิมทุกครั้ง"""

    def test_ครั้งแรกวนซ้ำครั้งต่อมาดีต้องได้ผลลัพธ์(self):
        class _Flaky(_Client):
            def generate_content(self, model, contents, config):
                self.seen.append(contents)
                if len(self.seen) == 1:
                    return _Resp("# ok\n\n" + "." * 300)
                return _Resp("# หัวข้อจริง\n\nเนื้อหาที่ใช้ได้")

        client = _Flaky()
        out = _convert(client)
        assert "เนื้อหาที่ใช้ได้" in out
        assert len(client.seen) == 2

    def test_temperature_ต้องไล่ขึ้นไม่ใช่ค่าเดิม(self):
        assert pdf_ingest._CONVERT_TEMPERATURES[0] < pdf_ingest._CONVERT_TEMPERATURES[-1]


class TestSplitOnPersistentLoop:
    """วัดจริง: 15 หน้าสแกนวนลูปทุก temperature แต่ 6 หน้าผ่านสบาย
    ⇒ ต้นเหตุคือจำนวนหน้าต่อครั้ง ไม่ใช่ temperature
    """

    def test_วนซ้ำทุกรอบแล้วต้องแบ่งช่วงหน้าย่อย(self):
        n_temps = len(pdf_ingest._CONVERT_TEMPERATURES)

        class _LoopsOnManyPages(_Client):
            def generate_content(self, model, contents, config):
                self.seen.append(contents)
                n_images = len(contents) - 1
                if n_images > pdf_ingest._SPLIT_MAX_PAGES:
                    return _Resp("." * 300)          # ช่วงยาวเกิน → วนลูป
                return _Resp(f"# เนื้อหา {n_images} หน้า")

        client = _LoopsOnManyPages()
        out = _convert(client, page_images=[b"x"] * 15, page_start=261, page_end=275)

        # ต้องลองเต็มช่วงครบทุก temperature ก่อน แล้วค่อยแบ่ง 15 → 6+6+3
        assert len(client.seen) == n_temps + 3
        assert out.count("# เนื้อหา") == 3
        assert "source_pages" in out, "ส่วนหัวต้องถูกประกอบเองเมื่อแบ่งช่วง"
        assert not pdf_ingest._is_degenerate(out)

    def test_ช่วงสั้นอยู่แล้วไม่ต้องแบ่ง_ให้_raise_ตามเดิม(self):
        client = _Client(text="." * 300)
        with pytest.raises(RuntimeError):
            _convert(client, page_images=[b"x"] * 3)
