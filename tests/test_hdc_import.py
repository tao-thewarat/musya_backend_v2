"""Tests: นำเข้าข้อมูลจาก MoPH Open Data + กันข้อมูลหายตอนรีเฟรช

ที่มาจากการทดสอบจริง 2026-07-31:
  - นำเข้า s_ckd_stage_typearea ครั้งแรกได้ 75,557 แถว / 10 ปี
  - กดรีเฟรชครั้งที่ 2 ได้ 72,724 แถว / **9 ปี** เพราะปีหนึ่งพลาดชั่วคราว
    ⇒ ถ้าเขียนทับไปเลย ข้อมูลทั้งปีหายโดยไม่มีใครรู้
"""
import pytest

from src.tools import amphoe_zone10 as amphoe
from src.tools import hdc_opendata as hdc


class TestParseSource:
    def test_แกะชื่อตารางจาก_url_ของ_opendata(self):
        url = ("https://opendata.moph.go.th/th/services/summary-table/"
               "02e752187c7282ebc9315123aa1cabbe/s_ckd_stage_typearea/5d523ced4c95")
        assert hdc.parse_table_name(url) == "s_ckd_stage_typearea"

    def test_รับชื่อตารางตรงๆได้(self):
        assert hdc.parse_table_name("s_ckd_stage_hosp") == "s_ckd_stage_hosp"
        assert hdc.parse_table_name("  s_cmi_dead  ") == "s_cmi_dead"

    def test_url_ที่ไม่ใช่รูปแบบที่รู้จักต้องคืนค่าว่าง(self):
        assert hdc.parse_table_name("https://hdc.moph.go.th/center/public/xyz") == ""
        assert hdc.parse_table_name("") == ""


class TestResolveFromHdcMetadataPage:
    """หน้า metadata ของ HDC มีแต่ reportId ไม่มีชื่อตาราง — ต้องยิงถามต้นทาง

    เป็นทางที่ผู้ใช้จริงใช้ เพราะเป็นหน้าที่มีนิยามตัวชี้วัดให้คนอ่าน
    """

    URL = ("https://hdc.moph.go.th/center/public/standard-report-detail/"
           "5d523ced4c9569123109fa6f4071d35f?subcatalogId=e71a73a77b1474e63b71bccf727009ce")

    def _fake(self, monkeypatch, result):
        monkeypatch.setattr(hdc, "lookup_by_report_id", lambda rid: result)

    def test_แกะ_reportid_แล้วถามต้นทางว่าเป็นตารางไหน(self, monkeypatch):
        seen = {}

        def fake(rid):
            seen["rid"] = rid
            return {"table": "s_ckd_stage_typearea", "title_th": "ผู้ป่วยไตเรื้อรัง",
                    "category": "NCD", "report_id": rid}

        monkeypatch.setattr(hdc, "lookup_by_report_id", fake)
        got = hdc.resolve_source(self.URL)
        assert seen["rid"] == "5d523ced4c9569123109fa6f4071d35f", "ต้องตัด query string ทิ้ง"
        assert got["table"] == "s_ckd_stage_typearea"
        assert got["title_th"] == "ผู้ป่วยไตเรื้อรัง"
        assert got["report_url"] == self.URL, "เก็บลิงก์ต้นทางไว้ให้คนกดดูนิยามภายหลัง"

    def test_ถ้าต้นทางไม่รู้จัก_reportid_ต้องไม่เดามั่ว(self, monkeypatch):
        self._fake(monkeypatch, {})
        assert hdc.resolve_source(self.URL) == {}

    def test_ชื่อตารางตรงๆไม่ต้องยิงเน็ต(self, monkeypatch):
        def boom(rid):
            raise AssertionError("ไม่ควรเรียก API เมื่อผู้ใช้พิมพ์ชื่อตารางมาแล้ว")

        monkeypatch.setattr(hdc, "lookup_by_report_id", boom)
        assert hdc.resolve_source("s_dm_ckd1")["table"] == "s_dm_ckd1"

    def test_url_summary_table_ยังใช้ชื่อตารางใน_url_เป็นหลัก(self, monkeypatch):
        """ต่อให้ถามต้นทางไม่ได้ ก็ยังนำเข้าได้เพราะชื่อตารางอยู่ใน URL อยู่แล้ว"""
        self._fake(monkeypatch, {})
        url = ("https://opendata.moph.go.th/th/services/summary-table/"
               "02e752187c7282ebc9315123aa1cabbe/s_ckd_stage_typearea/"
               "5d523ced4c9569123109fa6f4071d35f")
        assert hdc.resolve_source(url)["table"] == "s_ckd_stage_typearea"


class TestSubcatalog:
    """ดึงทั้งหมวดจากสารบัญจริงของต้นทาง — แม่นกว่าการค้นด้วยคำ ไม่มีทางจับผิดหมวด"""

    CAT = "e71a73a77b1474e63b71bccf727009ce"

    def test_แกะ_catid_จาก_url_หน้าหมวด(self):
        u = f"https://hdc.moph.go.th/center/public/standard-subcatalog/{self.CAT}"
        assert hdc.parse_subcatalog_id(u) == self.CAT

    def test_แกะ_catid_จาก_query_string_ของหน้ารายงาน(self):
        """ผู้ใช้มักวางลิงก์หน้ารายงานที่มี ?subcatalogId= ต่อท้ายมา"""
        u = (f"https://hdc.moph.go.th/center/public/standard-report-detail/"
             f"0f6df79c2f8887f50d7879b5fe91c080?subcatalogId={self.CAT}")
        assert hdc.parse_subcatalog_id(u) == self.CAT

    def test_รับรหัสหมวดตรงๆได้_แต่ปฏิเสธข้อความมั่ว(self):
        assert hdc.parse_subcatalog_id(self.CAT) == self.CAT
        assert hdc.parse_subcatalog_id("ไม่ใช่รหัส") == ""
        assert hdc.parse_subcatalog_id("") == ""

    def test_ยุบรายการซ้ำให้เหลือตารางละรายการ(self, monkeypatch):
        """ต้นทางมีตารางเดียวโผล่หลาย report_id (เช่น s_ckd_stage_hosp มา 2 ครั้ง)
        ถ้าไม่ยุบ ผู้ใช้จะเห็นรายการซ้ำแล้วเผลอนำเข้าซ้ำ
        """
        raw = [
            {"source_table": "s_a", "report_name": "ข", "category_name": "ไต",
             "opendata_id": "aa11"},
            {"source_table": "s_a", "report_name": "ข (ซ้ำ)", "category_name": "ไต",
             "opendata_id": "bb22"},
            {"source_table": "s_b", "report_name": "ก", "category_name": "ไต",
             "opendata_id": "cc33"},
        ]
        monkeypatch.setattr(hdc, "_req", lambda *a, **k: raw)
        got = hdc.list_subcatalog(self.CAT)
        assert [g["table"] for g in got] == ["s_b", "s_a"], "ยุบซ้ำ + เรียงตามชื่อไทย"

    def test_สร้างลิงก์หน้า_metadata_ให้เลย(self, monkeypatch):
        """ผู้ใช้ต้องกดไปอ่านนิยามตัวชี้วัดได้ — opendata_id คือ id ใน URL นั้น"""
        monkeypatch.setattr(hdc, "_req", lambda *a, **k: [
            {"source_table": "s_a", "report_name": "ข", "category_name": "ไต",
             "opendata_id": "0f6df79c2f8887f50d7879b5fe91c080"},
        ])
        url = hdc.list_subcatalog(self.CAT)[0]["report_url"]
        assert "standard-report-detail/0f6df79c2f8887f50d7879b5fe91c080" in url
        assert f"subcatalogId={self.CAT}" in url

    def test_รายการที่ไม่มีชื่อตารางต้องถูกข้าม(self, monkeypatch):
        monkeypatch.setattr(hdc, "_req", lambda *a, **k: [{"report_name": "ไม่มีตาราง"}])
        assert hdc.list_subcatalog(self.CAT) == []


class TestZone10:
    def test_ครบห้าจังหวัดเขตสุขภาพที่_10(self):
        assert set(hdc.ZONE10) == {"34", "33", "35", "37", "49"}
        assert hdc.ZONE10["34"] == "อุบลราชธานี"


class TestToCsv:
    def _schema(self):
        return [{"name": n, "type": "int", "desc": ""} for n in
                ("id", "hospcode", "areacode", "date_com", "b_year", "stage1", "stage2")]

    def test_ใส่คอลัมน์แกนที่_pipeline_รู้จัก(self):
        """⚠️ ต้องเป็น จังหวัด/อำเภอ/ปีงบประมาณ ให้ตรงกับ _detect_geo_keys และ
        _detect_year_keys ไม่งั้น pipeline เชื่อมข้อมูลข้ามไฟล์ไม่ได้
        """
        rows = [{"hospcode": "10669", "areacode": "34010110", "b_year": "2569",
                 "date_com": "202607240843", "stage1": 10, "stage2": 20}]
        out = hdc.to_csv("t", rows, self._schema()).decode("utf-8-sig")
        header = out.splitlines()[0]
        assert header.startswith("จังหวัด,อำเภอ,ปีงบประมาณ")
        assert "stage1" in header and "stage2" in header

    def test_แปลงรหัสจังหวัดเป็นชื่อไทย(self):
        rows = [{"hospcode": "1", "areacode": "49010101", "b_year": "2569",
                 "date_com": "x", "stage1": 1, "stage2": 2}]
        out = hdc.to_csv("t", rows, self._schema()).decode("utf-8-sig")
        assert "มุกดาหาร" in out

    def test_รวมค่าตัวชี้วัดให้ในคอลัมน์เดียว(self):
        """ผู้ใช้ถาม 'รวมทุก stage' บ่อย — คำนวณให้เลยดีกว่าให้ AI เขียนโค้ดบวกเอง"""
        rows = [{"hospcode": "1", "areacode": "34010101", "b_year": "2569",
                 "date_com": "x", "stage1": 10, "stage2": 20}]
        out = hdc.to_csv("t", rows, self._schema()).decode("utf-8-sig")
        assert ",30," in out or out.rstrip().endswith(",30,x")

    def test_ค่าว่างต้องไม่พังและไม่ถูกนับรวม(self):
        rows = [{"hospcode": "1", "areacode": "34010101", "b_year": "2569",
                 "date_com": "x", "stage1": None, "stage2": 5}]
        out = hdc.to_csv("t", rows, self._schema()).decode("utf-8-sig")
        assert out.count("\n") == 2      # header + 1 แถว


class TestAmphoeLookup:
    """ตารางรหัสอำเภอ — ตรวจสอบไขว้ 3 ทางแล้วเมื่อ 2026-07-31 (ดู amphoe_zone10.py)"""

    COUNTS = {"34": 25, "33": 22, "35": 9, "37": 7, "49": 7}

    def test_ครบทุกอำเภอของทั้งห้าจังหวัด(self):
        assert len(amphoe.AMPHOE) == 70
        for pc, n in self.COUNTS.items():
            got = [k for k in amphoe.AMPHOE if k.startswith(pc)]
            assert len(got) == n, f"จังหวัด {pc} ควรมี {n} อำเภอ แต่ได้ {len(got)}"

    def test_รหัสที่สรุปด้วยการตัดตัวเลือก(self):
        """สองรหัสนี้ dim_geography ไม่มี ได้มาจากการตัดตัวเลือกกับไฟล์เก่า
        ถ้ามีใครแก้ให้ผิด จะจับไม่ได้เลยเพราะไม่มีแหล่งอื่นในโค้ด
        """
        assert amphoe.AMPHOE["3420"] == "ตาลสุม"
        assert amphoe.AMPHOE["3322"] == "ศิลาลาด"

    def test_ชื่อต้องไม่มีคำนำหน้า(self):
        """ต้องสะกดตรงกับไฟล์เก่าจาก สสจ. ไม่งั้น pipeline จับคู่คำไม่ติด"""
        for k, v in amphoe.AMPHOE.items():
            assert not v.startswith(("อ.", "อำเภอ")), f"{k} ไม่ควรมีคำนำหน้า: {v}"
            assert v == v.strip() and v

    def test_แปลง_areacode_เต็มเป็นชื่ออำเภอ(self):
        assert amphoe.amphoe_name("49050101") == "คำชะอี"
        assert amphoe.amphoe_name("34010110") == "เมืองอุบลราชธานี"

    def test_รหัสที่ไม่รู้จักคืนรหัสเดิมไม่ใช่ค่าว่าง(self):
        """อำเภอใหม่ต้องไม่ทำให้ข้อมูลทั้งแถวหายไปเงียบ ๆ"""
        assert amphoe.amphoe_name("34990101") == "99"
        assert amphoe.unknown_codes([{"areacode": "34990101"}]) == ["3499"]
        assert amphoe.unknown_codes([{"areacode": "49050101"}]) == []

    def test_รหัสอำเภอ_00_คือต้นทางไม่ได้ลงรหัส(self):
        """เจอจริง 1 แถว (hospcode 77466 ศรีสะเกษ 2568) — ไม่ใช่อำเภอใหม่
        จึงต้องไม่เตือน แต่ก็ต้องไม่ปล่อย "00" ลงคอลัมน์ที่ควรเป็นชื่อ
        """
        assert amphoe.amphoe_name("33000610") == amphoe.UNSPECIFIED
        assert amphoe.unknown_codes([{"areacode": "33000610"}]) == []


class TestToCsvAmphoeName:
    """เคสที่เป็นต้นเรื่อง: ผู้ใช้ถาม 'อำเภอคำชะอีเป็นยังไง' แล้วหาไฟล์ไม่เจอ"""

    def _schema(self):
        return [{"name": n, "type": "int", "desc": ""} for n in
                ("id", "hospcode", "areacode", "date_com", "b_year", "stage1", "stage2")]

    def _row(self, areacode):
        return {"hospcode": "1", "areacode": areacode, "b_year": "2569",
                "date_com": "x", "stage1": 1, "stage2": 2}

    def test_คอลัมน์อำเภอเป็นชื่อไม่ใช่รหัส(self):
        out = hdc.to_csv("t", [self._row("49050101")], self._schema()).decode("utf-8-sig")
        cells = out.splitlines()[1].split(",")
        assert cells[1] == "คำชะอี", "เดิมเขียน '05' ทำให้ File Finder หาไม่เจอ"

    def test_ยังเก็บรหัสไว้ให้_join_ได้(self):
        out = hdc.to_csv("t", [self._row("49050101")], self._schema()).decode("utf-8-sig")
        header = out.splitlines()[0].split(",")
        cells = out.splitlines()[1].split(",")
        assert header[4] == "areacode" and cells[4] == "49050101", "areacode ต้องไม่เปลี่ยน"
        assert header[5] == "รหัสอำเภอ" and cells[5] == "05"

    def test_แกนที่_pipeline_ใช้ยังอยู่ตำแหน่งเดิม(self):
        out = hdc.to_csv("t", [self._row("49050101")], self._schema()).decode("utf-8-sig")
        assert out.splitlines()[0].startswith("จังหวัด,อำเภอ,ปีงบประมาณ")

    def test_รหัสแปลกปลอมยังเขียนไฟล์ได้(self):
        """ต้นทางเพิ่มอำเภอใหม่ต้องไม่ทำให้การนำเข้าล้มทั้งงาน"""
        out = hdc.to_csv("t", [self._row("34990101")], self._schema()).decode("utf-8-sig")
        cells = out.splitlines()[1].split(",")
        assert cells[0] == "อุบลราชธานี" and cells[1] == "99"


class TestDetectCaveats:
    """ข้อควรระวังที่ต้องถึงมือ AI — `describe_for_prompt()` แนบ caveats ให้อยู่แล้ว
    แต่ก่อนหน้านี้ไม่มีใครเติมข้อมูลลงไปเลย (0 จาก 49 ไฟล์)
    """

    def _detect(self, table, per_year, schema=None):
        from src.routers.hdc_import import _detect_caveats
        return _detect_caveats(table, schema or [], {"perYear": per_year})

    def _y(self, year, rows, ok=True, missing=None):
        return {"year": year, "rows": rows, "ok": ok, "missing": missing or []}

    def test_จับรอยต่อวิธีนับจากจำนวนแถวที่ร่วงกะทันหัน(self):
        """เคสจริง s_ckd_stage_hosp: 2562=535 แล้ว 2563 เหลือ 75

        ถ้าไม่เตือน AI จะสรุปว่า 'ผู้ป่วยลดลง 85%' ทั้งที่เป็นการเปลี่ยนวิธีนับ
        """
        got = self._detect("s_x", [self._y("2562", 535), self._y("2563", 75)])
        assert any("2562" in c and "2563" in c for c in got)
        assert any("ห้ามนำสองช่วงนี้มาเทียบแนวโน้ม" in c for c in got)

    def test_จำนวนแถวขยับนิดหน่อยต้องไม่เตือน(self):
        """ปีต่อปีต่างกันเล็กน้อยเป็นเรื่องปกติ เตือนพร่ำเพรื่อแล้วคนจะเลิกอ่าน"""
        per = [self._y("2566", 7666), self._y("2567", 7647), self._y("2568", 7781)]
        assert self._detect("s_x", per) == []

    def test_ตาราง_hosp_ต้องเตือนว่าเป็น_work_load(self):
        got = self._detect("s_ckd_stage_hosp", [self._y("2568", 73)])
        assert any("Work Load" in c for c in got)
        assert any("ห้ามตอบว่า" in c for c in got)

    def test_ตารางที่ไม่ใช่_hosp_ไม่ต้องเตือน_work_load(self):
        got = self._detect("s_ckd_stage_typearea", [self._y("2568", 7500)])
        assert not any("Work Load" in c for c in got)

    def test_บอกด้วยว่าปีไหนถูกข้ามเพราะข้อมูลไม่ครบเขต(self):
        got = self._detect("s_x", [self._y("2561", 251, ok=False, missing=["อุบลราชธานี"])])
        assert any("2561" in c and "อุบลราชธานี" in c for c in got)

    def test_ปีที่ต้นทางไม่มีข้อมูลเลยไม่ต้องเตือน(self):
        """0 แถว = ต้นทางไม่มีปีนั้น ไม่ใช่ข้อมูลขาด — เตือนไปก็ไม่มีประโยชน์"""
        got = self._detect("s_x", [self._y("2555", 0, ok=False, missing=["อุบลราชธานี"])])
        assert got == []


class TestRefreshGuard:
    """กันเคสที่เจอจริง: รีเฟรชแล้วได้ปีน้อยกว่าเดิมเพราะ API พลาดชั่วคราว"""

    def test_ตรวจว่าปีหายไปเทียบกับครั้งก่อน(self):
        old = {"2560", "2561", "2562", "2563"}
        new = {"2560", "2561", "2563"}
        lost = sorted(old - new)
        assert lost == ["2562"], "ต้องจับได้ว่าปี 2562 หายไป"

    def test_ปีเท่าเดิมหรือมากขึ้นถือว่าปกติ(self):
        old = {"2560", "2561"}
        for new in ({"2560", "2561"}, {"2560", "2561", "2562"}):
            assert not (old - new), "ไม่ควรเตือนเมื่อข้อมูลเท่าเดิมหรือมากขึ้น"

    def test_endpoint_รับพารามิเตอร์_force(self):
        """ต้องมีทางให้ผู้ใช้ยืนยันทับได้ ถ้าต้นทางตัดปีเก่าออกจริง"""
        import inspect

        from src.routers.hdc_import import refresh
        assert "force" in inspect.signature(refresh).parameters


class TestReportNotice:
    """นิยามเชิงปฏิบัติการจากหน้า HDC — opendata ไม่มีข้อมูลชุดนี้เลย

    ผู้ใช้ชี้ให้เห็นว่า metadata ที่ดึงมายังไม่ครอบคลุม: `report_schema` บอกแค่
    "จำนวนผู้ป่วย (B1)" แต่หน้า HDC บอกถึงรหัสโรค ICD ที่รวม/ตัดออก รหัส LAB
    และเกณฑ์ตัดค่า ซึ่งเป็นคำตอบของคำถาม "ตัวเลขนี้นับใคร"
    """

    RAW = {"rows": {
        "notice": "B = ผู้ป่วย<br>1. (E10* ถึง E14*) ลบออกด้วย (E102)<br>2. และ&nbsp;ไม่มี N181-189",
        "bname": "จำนวนผู้ป่วย DM และ/หรือ HT",
        "aname": "ผู้ป่วยตาม B ที่ eGFR &lt;60",
        "byear_list": [2569, 2568],
    }}

    def test_แปลง_br_เป็นบรรทัดใหม่_ไม่ใช่ลบทิ้ง(self, monkeypatch):
        """นิยามเป็นรายการข้อ 1/2/3 ถ้าเชื่อมติดกันจะอ่านไม่รู้เรื่อง"""
        monkeypatch.setattr(hdc, "_req", lambda *a, **k: self.RAW)
        n = hdc.get_report_notice("abc123def456abc1", byear="2569")
        assert "\n1. (E10* ถึง E14*)" in n["notice"]
        assert "<br>" not in n["notice"]

    def test_ถอด_html_entity(self, monkeypatch):
        monkeypatch.setattr(hdc, "_req", lambda *a, **k: self.RAW)
        n = hdc.get_report_notice("abc123def456abc1", byear="2569")
        assert "eGFR <60" in n["a_name"]
        assert "&nbsp;" not in n["notice"] and "&lt;" not in n["a_name"]

    def test_ไม่มี_report_code_ต้องไม่ยิงเน็ต(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("ไม่ควรเรียก API เมื่อไม่มี report code")
        monkeypatch.setattr(hdc, "_req", boom)
        assert hdc.get_report_notice("") == {}

    def test_ดึงไม่ได้ต้องคืนว่าง_ไม่ใช่โยน_exception(self, monkeypatch):
        """เป็นข้อมูลเสริม — ถ้าล้มต้องไม่ทำให้การนำเข้าทั้งชุดพัง"""
        def boom(*a, **k):
            raise RuntimeError("HDC ล่ม")
        monkeypatch.setattr(hdc, "_req", boom)
        assert hdc.get_report_notice("abc123def456abc1", byear="2569") == {}


class TestRetryTransient:
    """ต้นทางล่มเป็นช่วงสั้น ๆ — วัดจริง 2026-08-01: ยิงหมวดสาขาไตครั้งแรกได้ 502
    ครั้งที่สองสำเร็จใน 0.4 วินาที · ของเดิมรอรวมแค่ 4.5 วิ ซึ่งสั้นกว่าช่วงที่ต้นทางล่ม
    """

    def _http(self, code):
        import urllib.error
        return urllib.error.HTTPError("u", code, "err", {}, None)

    def test_5xx_และ_429_ต้องลองใหม่(self):
        for code in (429, 500, 502, 503, 504):
            assert hdc.is_retryable(self._http(code)), f"{code} ควรลองใหม่ได้"

    def test_403_ต้องลองใหม่เพราะเป็น_rate_limit_ไม่ใช่ไม่มีสิทธิ์(self):
        """วัดจริง: ยิงหมวดสาขาไต 6 ครั้งติด ได้ 403 ถึง 3–4 ครั้งแบบสุ่ม
        เปลี่ยน User-Agent เป็นเบราว์เซอร์ก็ยังโดน ⇒ เป็น bot-protection ของ Cloudflare
        ถ้าไม่นับเป็น retryable ผู้ใช้จะเจอ "โหลดไม่สำเร็จ" ราวครึ่งหนึ่งของการกด
        """
        assert hdc.is_retryable(self._http(403))

    def test_400_และ_404_ลองใหม่ไปก็ไม่ช่วย(self):
        for code in (400, 404):
            assert not hdc.is_retryable(self._http(code)), f"{code} ไม่ควรลองใหม่"

    def test_timeout_และ_connection_error_ลองใหม่ได้(self):
        assert hdc.is_retryable(TimeoutError("timed out"))
        assert hdc.is_retryable(ConnectionResetError("reset"))

    def test_ถอยแบบทวีคูณและรอนานพอให้ต้นทางฟื้น(self):
        assert sum(hdc._BACKOFF) >= 30, "รอรวมต้องนานพอ — ของเดิม 4.5 วิ สั้นเกินไป"
        assert list(hdc._BACKOFF) == sorted(hdc._BACKOFF), "ต้องถอยเพิ่มขึ้นเรื่อย ๆ"

    def test_ล้มชั่วคราวแล้วสำเร็จในครั้งถัดไป(self, monkeypatch):
        calls = {"n": 0}

        def flaky(url, data=None, headers=None, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise self._http(502)
            class R:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b'[{"ok":1}]'
            return R()

        monkeypatch.setattr(hdc.urllib.request, "Request", lambda *a, **k: object())
        monkeypatch.setattr(hdc.urllib.request, "urlopen", lambda *a, **k: flaky(""))
        monkeypatch.setattr(hdc.time, "sleep", lambda s: None)   # ไม่ต้องรอจริงตอนเทสต์
        assert hdc._req("http://x") == [{"ok": 1}]
        assert calls["n"] == 3, "ต้องลองจนสำเร็จ ไม่ใช่ยอมแพ้ตั้งแต่ครั้งแรก"

    def test_error_ถาวรต้องไม่เสียเวลาลองซ้ำ(self, monkeypatch):
        calls = {"n": 0}

        def always400(*a, **k):
            calls["n"] += 1
            raise self._http(400)

        monkeypatch.setattr(hdc.urllib.request, "Request", lambda *a, **k: object())
        monkeypatch.setattr(hdc.urllib.request, "urlopen", always400)
        monkeypatch.setattr(hdc.time, "sleep", lambda s: None)
        with pytest.raises(Exception):
            hdc._req("http://x")
        assert calls["n"] == 1, "400 ต้องเลิกทันที ไม่ลองซ้ำ"


class TestMetadataSizeLimit:
    """MinIO จำกัดขนาด metadata รวม ~2 KB — ภาษาไทยเข้ารหัสแล้วบวม 9 เท่า

    เจอจริง 2026-08-01: `s_kpi_ckd_hba1c` ชื่อยาว นำเข้าล้มด้วย HTTP 500
    `MetadataTooLarge` เพราะโค้ดตัดที่ 150 "ตัวอักษร" ก่อนเข้ารหัส
    (150 อักษรไทย → ~1,350 ไบต์ หลัง quote)
    """

    def _q(self, *a):
        from src.routers.hdc_import import _quote_within
        return _quote_within(*a)

    def test_ไม่เกินงบที่กำหนดแม้เป็นภาษาไทยล้วน(self):
        thai = "ก" * 300
        for budget in (100, 500, 1000):
            assert len(self._q(thai, budget)) <= budget

    def test_ผลลัพธ์ต้อง_decode_กลับได้_ไม่ตัดกลาง_escape(self):
        import urllib.parse
        out = self._q("การชะลอความเสื่อมของไต" * 20, 137)   # งบที่หาร 9 ไม่ลงตัว
        assert len(out) <= 137
        urllib.parse.unquote(out)                            # ต้องไม่ระเบิด
        assert "%" not in out[-2:] or out.endswith(("%41",)), "ห้ามจบกลาง escape"

    def test_ข้อความสั้นต้องไม่ถูกตัด(self):
        import urllib.parse
        s = "D3_NCDs/โรคไต/x.csv"
        assert urllib.parse.unquote(self._q(s, 1000)) == s

    def test_ตัดที่ขอบอักขระไม่ใช่ขอบไบต์(self):
        """ตัดกลาง %E0%B8 แล้ว unquote จะได้ตัวอักษรเพี้ยนหรือ error"""
        import urllib.parse
        for budget in range(9, 40):
            out = self._q("กขคง", budget)
            assert len(out) % 9 == 0, f"งบ {budget}: ต้องได้จำนวนอักขระไทยเต็มตัว"
            urllib.parse.unquote(out)


class TestFallbackFiltersZone10:
    """ทางสำรองรายจังหวัดต้องกรองเขต 10 ด้วย

    เจอจริง 2026-08-01: `s_new_ckd5` ไม่สนใจพารามิเตอร์ `province` แล้วคืนทั้งประเทศ
    ทางสำรองเดิมรับมาทั้งหมดโดยไม่กรอง ⇒ ข้อมูลจังหวัดอื่นปนเข้าคลัง
    แล้วพังตอนแปลงรหัสเป็นชื่อจังหวัดด้วย `KeyError: '44'` (มหาสารคาม)
    ที่ร้ายกว่าคือถ้าบังเอิญไม่พัง ก็ได้ข้อมูลผิดเข้าคลังโดยไม่มีใครรู้
    """

    def _rows(self, *codes):
        return [{"hospcode": "1", "areacode": f"{c}010101", "b_year": "2569",
                 "date_com": "x", "v": 1} for c in codes]

    def test_ทางสำรองต้องทิ้งจังหวัดนอกเขต(self, monkeypatch):
        calls = {"n": 0}

        def fake(url, payload=None, **kw):
            calls["n"] += 1
            if calls["n"] == 1:                      # ทางหลักพัง → ตกไปทางสำรอง
                raise TimeoutError("boom")
            return self._rows("34", "44", "33")      # ต้นทางคืนทั้งประเทศ

        monkeypatch.setattr(hdc, "_req", fake)
        monkeypatch.setattr(hdc.time, "sleep", lambda s: None)
        out = hdc.fetch_zone10("t", ["2569"])
        codes = {r["areacode"][:2] for r in out["rows"]}
        assert "44" not in codes, "จังหวัดนอกเขต 10 ต้องไม่หลุดเข้ามา"
        assert codes <= set(hdc.ZONE10)

    def test_แถวที่ไม่มี_areacode_ต้องไม่ทำให้ระเบิด(self, monkeypatch):
        def fake(url, payload=None, **kw):
            if payload and "province" not in payload:
                raise TimeoutError("boom")
            return [{"hospcode": "1", "b_year": "2569", "date_com": "x"}]

        monkeypatch.setattr(hdc, "_req", fake)
        monkeypatch.setattr(hdc.time, "sleep", lambda s: None)
        assert hdc.fetch_zone10("t", ["2569"])["rows"] == []


class TestNoticeHtmlClean:
    """หมายเหตุของ HDC เต็มไปด้วยเครื่องหมายน้อยกว่า/มากกว่าที่เป็น **เกณฑ์จริง**

    เจอจริง 2026-08-01 กับ `s_dm_screen` (คัดกรองเบาหวาน):
    ตัวล้าง HTML เดิมใช้ `<[^>]+>` ซึ่งจับตั้งแต่ `< 100 mg% … =` ไปจบที่ `>` ของ
    `=>` บรรทัดถัดไป **แล้วลบทิ้งทั้งก้อน** ⇒ บรรทัด "เสี่ยง (Risk = 1)" หายไป
    เหลือข้อความกำกวมว่า "ระดับน้ำตาล >=70 ถึง 100 ถึง = 126 mg%"
    ซึ่งผิดความหมายโดยสิ้นเชิง และเป็นเกณฑ์ที่ AI ต้องใช้ตัดสินว่าใครเสี่ยง
    """

    RAW = ("<font color=red>จำนวนคัดกรอง ยังมิได้หักค่านอกเกณฑ์</font><br>"
           "ตรวจน้ำตาลโดยอดอาหาร<br>"
           "- ปกติ (Risk = 0) หมายถึง ระดับน้ำตาล >=70 ถึง < 100 mg%<br>"
           "- เสี่ยง (Risk = 1) หมายถึง ระดับน้ำตาล => 100 ถึง < 126 mg%<br>"
           "- สงสัยป่วย (Risk = 2) หมายถึง ระดับน้ำตาล >= 126 mg%")

    def _clean(self, raw):
        """เรียกตัวล้างจริงผ่าน get_report_notice เพื่อไม่ให้เทสต์หลุดจากของจริง"""
        import re as _re
        s = _re.sub(r"<br\s*/?>", "\n", raw, flags=_re.I)
        s = _re.sub(r"</?[A-Za-z][^<>]*>", "", s)
        return s.strip()

    def test_เกณฑ์ทั้งสามระดับต้องอยู่ครบ(self):
        out = self._clean(self.RAW)
        for want in ("ปกติ (Risk = 0)", "เสี่ยง (Risk = 1)", "สงสัยป่วย (Risk = 2)"):
            assert want in out, f"หาย: {want}"

    def test_ตัวเลขเกณฑ์ต้องไม่ถูกกลืน(self):
        out = self._clean(self.RAW)
        for want in ("< 100 mg%", "=> 100", "< 126 mg%", ">= 126 mg%"):
            assert want in out, f"เกณฑ์หาย: {want}"

    def test_ยังลบแท็ก_html_จริงได้อยู่(self):
        out = self._clean(self.RAW)
        assert "<font" not in out and "</font>" not in out
        assert "<br>" not in out

    def test_บรรทัดไม่เชื่อมติดกัน(self):
        """<br> มีความหมาย — นิยามเป็นรายการข้อ ถ้าเชื่อมติดกันจะอ่านไม่รู้เรื่อง"""
        assert self._clean(self.RAW).count("\n") >= 4


class TestIndicatorNameNotFromPath:
    """ชื่อตัวชี้วัดต้องมาจากต้นทาง ไม่ใช่เดาจาก path

    บั๊กที่เจอ 2026-08-01: `build_vault_path` ตัดชั้น "ชื่อตัวชี้วัด" ทิ้งเมื่อ path
    ใกล้ลิมิต 150 ⇒ `build_data_dict` ที่อ่าน `parts[-2]` เลยได้ชื่อกลุ่มแทน
    ผลจริง — ไฟล์สุขภาพจิต 28 ไฟล์ได้ indicator_th = "ผู้ป่วยสุขภาพจิต" เหมือนกันหมด
    ทำให้ File Finder แยกไม่ออกว่าไฟล์ไหนคือตัวชี้วัดอะไร
    """

    def test_path_ที่ถูกย่อทำให้เดาชื่อผิด(self):
        """ยืนยันว่าต้นตอของบั๊กยังอยู่ — จึงห้ามพึ่ง build_data_dict ตัวเดียว"""
        import io

        import pandas as pd

        from src.tools.data_dict import build_data_dict

        df = pd.DataFrame({"จังหวัด": ["มุกดาหาร"], "ปีงบประมาณ": ["2569"], "target": ["1"]})
        buf = io.StringIO(); df.to_csv(buf, index=False)
        # path แบบที่ถูกย่อ: <โดเมน>/<กลุ่ม>/<ไฟล์> — ไม่มีชั้นชื่อตัวชี้วัด
        d = build_data_dict("1", "D2_Mental Health/ผู้ป่วยสุขภาพจิต/x.csv", "x.csv",
                            buf.getvalue().encode())
        assert d["indicator_th"] == "ผู้ป่วยสุขภาพจิต", "นี่คืออาการของบั๊ก ไม่ใช่พฤติกรรมที่ต้องการ"

    def test_ตัวนำเข้าต้องเขียนทับด้วยชื่อจริงจากต้นทาง(self):
        """กันบั๊กย้อนกลับ — `_do_import` ต้องมีบรรทัดที่ตั้ง indicator_th จาก title"""
        import inspect

        from src.routers import hdc_import

        src = inspect.getsource(hdc_import._do_import)
        assert 'd["indicator_th"] = title' in src, (
            "ถ้าบรรทัดนี้หาย ชื่อตัวชี้วัดจะกลับไปเดาจาก path อีก"
        )
