"""กันชื่อโฟลเดอร์ผิดโดเมนและ HTML ที่หลุดเข้าไปในชื่อ

ทั้งสองอย่างเจอจริงเมื่อ 2 ส.ค. 2569 บนหน้า /fileapa:

  1. ไฟล์ข้อมูลประชากรถูกวางใน `D5_Other` ซึ่งชนกับโดเมน d5 (ประชากร) ที่เพิ่งเพิ่ม
     ⇒ prefix `D5` ทำให้ไฟล์ยาเสพติดกลายเป็นข้อมูลประชากรในสายตาระบบ
  2. โฟลเดอร์ชื่อ `ประชากร<font color=red>ทะเบียนราษฏร์< font> ย้อนหลัง 3 ปี`
     เพราะ `title_th` ถูกดึงมาดิบ ๆ โดยไม่ล้าง HTML
"""
from src.domains import DOMAINS, FOLDER_PREFIX_TO_DOMAIN
from src.tools import vault_placement as vp
from src.tools.hdc_opendata import clean_html, clean_title


class Testชื่อโฟลเดอร์ตรงกับโดเมน:
    def test_ทุกโฟลเดอร์ที่ใช้วางไฟล์ต้องมีโดเมนรับผิดชอบ(self):
        """ไม่มีโดเมนรับ = ไฟล์อยู่ในคลังจริงแต่ AI ค้นไม่เจอ โดยไม่มีอะไรฟ้อง"""
        for folder in (vp.D1, vp.D2, vp.D3, vp.D4, vp.D5, vp.OTHER):
            code = FOLDER_PREFIX_TO_DOMAIN.get(folder[:2].upper(), "")
            assert code, f"โฟลเดอร์ {folder} ไม่มีโดเมนไหนรับผิดชอบ"
            assert DOMAINS[code].folder_prefix[:2].upper() == folder[:2].upper()

    def test_ตกถังต้องเป็น_d6_ไม่ใช่_d5(self):
        """d5 คือประชากร — ถ้าตกถังไป D5 ไฟล์ที่ไม่เข้าพวกจะกลายเป็นข้อมูลประชากร"""
        assert vp.OTHER.startswith("D6"), f"ตกถังอยู่ที่ {vp.OTHER} ซึ่งชนกับโดเมนอื่น"
        assert vp.D5.startswith("D5_Population")

    def test_ทุกโฟลเดอร์ต้องขึ้นต้นไม่ซ้ํากัน(self):
        prefixes = [f[:2].upper() for f in (vp.D1, vp.D2, vp.D3, vp.D4, vp.D5, vp.OTHER)]
        assert len(prefixes) == len(set(prefixes)), f"prefix ซ้ํากัน: {prefixes}"


class Testจัดโดเมนจากชื่อตัวชี้วัดAndหมวด:
    def test_ข้อมูลประชากรเข้า_d5(self):
        for title in ("ประชากรจำแนกเพศ กลุ่มอายุรายปี",
                      "ประชากรต่างด้าว จำแนกเพศ กลุ่มอายุรายปี",
                      "ประชากรแยกตามหน่วยบริการและชนิดการอยู่อาศัย TYPEAREA",
                      "ประชากรทะเบียนราษฏร์ ย้อนหลัง 3 ปี",
                      "ปิรามิดประชากรจำแนกเพศ กลุ่มอายุ"):
            folder, _ = vp.classify(title, "ประชากร")
            assert folder == vp.D5, f"{title!r} ไปอยู่ {folder}"

    def test_คำว่าประชากรในตัวชี้วัดอื่นต้องไม่ถูกดูดเข้า_d5(self):
        """คำว่า "ประชากร" โผล่ในตัวชี้วัดของทุกโดเมน — กฎ d5 ต้องอยู่ท้ายสุด

        ถ้าวางกฎ d5 ไว้ก่อน ตัวชี้วัดโภชนาการ/NCD ที่มีคำว่าประชากรจะถูกกวาดไปหมด
        """
        cases = [
            ("ร้อยละของประชากรวัยทำงานอายุ 19-59 ปี มีค่าดัชนีมวลกายปกติ", "งานโภชนาการ", vp.D4),
            ("ร้อยละของประชากรผู้สูงอายุ 60 ปีขึ้นไป มีรอบเอวปกติ", "งานโภชนาการ", vp.D4),
            ("ประชากรอายุ 15 ปีขึ้นไปได้รับการคัดกรองพฤติกรรมการดื่มเครื่องดื่มแอลกอฮอล์",
             "Service Plan สาขายาเสพติด", vp.OTHER),
        ]
        for title, cat, want in cases:
            folder, _ = vp.classify(title, cat)
            assert folder == want, f"{title[:40]!r} ไปอยู่ {folder} ควรเป็น {want}"


class Testล้างHTMLในชื่อ:
    def test_ตัดแท็ก_font_ออกจากชื่อรายงาน(self):
        got = clean_title("ประชากร<font color=red>ทะเบียนราษฏร์</font> ย้อนหลัง 3 ปี")
        assert got == "ประชากรทะเบียนราษฏร์ ย้อนหลัง 3 ปี"
        assert "<" not in got and ">" not in got

    def test_ชื่อต้องเป็นบรรทัดเดียวเสมอ(self):
        """ชื่อจะกลายเป็นชื่อโฟลเดอร์ — ขึ้นบรรทัดใหม่ทำ path พัง"""
        got = clean_title("ประชากร<br>ทะเบียนราษฎร์<br/>ย้อนหลัง")
        assert "\n" not in got
        assert got == "ประชากร ทะเบียนราษฎร์ ย้อนหลัง"

    def test_เกณฑ์ตัวเลขต้องไม่ถูกกินไปกับแท็ก(self):
        """`< 100 mg%` ไม่ใช่แท็ก — เคยถูกลบทั้งบรรทัดจนเกณฑ์ผิดความหมาย"""
        src = "ปกติ หมายถึง ระดับน้ำตาล >=70 ถึง < 100 mg%\nเสี่ยง หมายถึง => 100 ถึง < 126 mg%"
        got = clean_html(src)
        assert "< 100 mg%" in got
        assert "< 126 mg%" in got
        assert "เสี่ยง" in got

    def test_ชื่อที่ล้างแล้วยังจัดโดเมนได้ถูก(self):
        title = clean_title("ประชากร<font color=red>ทะเบียนราษฏร์</font> ย้อนหลัง 3 ปี")
        folder, _ = vp.classify(title, "ประชากร")
        assert folder == vp.D5
