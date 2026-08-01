"""Tests: พจนานุกรมข้อมูล CSV (เฟส 1 — อัตโนมัติล้วน)

ที่มา: metadata เดิมของไฟล์ CSV มีแต่ APA ซึ่ง Author/Abstract/KeyStats ว่างหมด
(ออกแบบมาสำหรับงานวิจัย ไม่ใช่ตารางตัวเลข) ⇒ AI ไม่รู้ขอบเขตข้อมูล ไม่รู้ว่า
คอลัมน์ไหนตัวตั้ง/ตัวหาร และไม่รู้ข้อควรระวัง
"""
import io

import pandas as pd
import pytest

from src.tools.data_dict import build_data_dict


def _csv(df: pd.DataFrame) -> bytes:
    b = io.StringIO()
    df.to_csv(b, index=False)
    return b.getvalue().encode()


PATH = "D3_NCDs/โรคความดัน/ร้อยละของประชากรอายุ 35 ปีขึ้นไปที่ได้รับการคัดกรอง/x.csv"


def _sample():
    return pd.DataFrame({
        "จังหวัด": ["อุบลราชธานี", "ศรีสะเกษ", "ยโสธร"],
        "ปีงบประมาณ": ["2565", "2566", "2569"],
        "อำเภอ": ["เมือง", "เมือง", "เมือง"],
        "ประชากร_B": ["100", "200", "300"],
        "คัดกรองแล้ว_A": ["50", "120", "150"],
        "รวม_%": ["50.0", "60.0", "50.0"],
    })


class TestCoverage:
    def test_อ่านปีจากเนื้อไฟล์ไม่ใช่ชื่อไฟล์(self):
        """⚠️ ปีในชื่อไฟล์เชื่อไม่ได้ — 486950 ชื่อเขียน '2569-2569'
        แต่ข้างในมีตั้งแต่ 2565 · ต้องอ่านจากคอลัมน์ปีจริงเท่านั้น
        """
        d = build_data_dict("1", PATH, "ตัวชี้วัด 2569-2569.csv", _csv(_sample()))
        assert d["years"] == ["2565", "2566", "2569"]
        assert d["year_min"] == "2565" and d["year_max"] == "2569"

    def test_แปลงปี_ค_ศ_เป็น_พ_ศ(self):
        df = _sample()
        df["ปีงบประมาณ"] = ["2022", "2023", "2026"]
        d = build_data_dict("1", PATH, "x.csv", _csv(df))
        assert d["years"] == ["2565", "2566", "2569"]

    def test_จับจังหวัดเขต_10_ที่มีจริงในไฟล์(self):
        d = build_data_dict("1", PATH, "x.csv", _csv(_sample()))
        assert d["provinces"] == ["อุบลราชธานี", "ศรีสะเกษ", "ยโสธร"]

    def test_ระบุความละเอียดของข้อมูล(self):
        assert build_data_dict("1", PATH, "x.csv", _csv(_sample()))["granularity"] == "อำเภอ"
        df = _sample().drop(columns=["อำเภอ"])
        assert build_data_dict("1", PATH, "x.csv", _csv(df))["granularity"] == "จังหวัด"

    def test_ไฟล์ระดับหน่วยบริการ(self):
        df = _sample()
        df["hospcode"] = ["10669", "10945", "10944"]
        assert build_data_dict("1", PATH, "x.csv", _csv(df))["granularity"] == "หน่วยบริการ"


class TestJoinKeys:
    def test_หาแกนเชื่อมครบสามมิติ(self):
        d = build_data_dict("1", PATH, "x.csv", _csv(_sample()))
        assert (d["key_province"], d["key_district"], d["key_year"]) == ("จังหวัด", "อำเภอ", "ปีงบประมาณ")

    def test_รองรับชื่อคอลัมน์นอกมาตรฐานที่พบจริง(self):
        """a_name (476686 โรคไต) และ sub-province (141988) เคยเชื่อมข้ามไฟล์ไม่ได้เลย"""
        df = pd.DataFrame({"ปี_ข้อมูล": ["2565"], "a_name": ["เมืองอุบลราชธานี"], "allstage": ["10"]})
        d = build_data_dict("1", "D3_NCDs/โรคไต/x.csv", "x.csv", _csv(df))
        assert d["key_district"] == "a_name"
        assert d["key_year"] == "ปี_ข้อมูล"


class TestColumnRoles:
    def test_แยกตัวตั้งตัวหารและร้อยละ(self):
        """B = ตัวหาร (ฐานประชากร) · A = ตัวตั้ง (ผู้ผ่านเกณฑ์) — ศัพท์ HDC"""
        d = build_data_dict("1", PATH, "x.csv", _csv(_sample()))
        role = {c["name"]: c["role"] for c in d["columns"]}
        assert role["ประชากร_B"] == "denominator"
        assert role["คัดกรองแล้ว_A"] == "numerator"
        assert role["รวม_%"] == "percentage"
        assert role["จังหวัด"] == "key"

    def test_ระบุหน่วยของคอลัมน์(self):
        d = build_data_dict("1", PATH, "x.csv", _csv(_sample()))
        unit = {c["name"]: c["unit"] for c in d["columns"]}
        assert unit["รวม_%"] == "ร้อยละ"


class TestUnknownColumns:
    def test_ทำเครื่องหมายคอลัมน์ที่ระบุความหมายไม่ได้(self):
        """F3/F5/result1 เกิดจากการ export ที่หัวคอลัมน์หาย — ต้องโชว์ให้เห็น
        ไม่ใช่ปล่อยให้ AI เดาเอง
        """
        df = pd.DataFrame({"ปี_ข้อมูล": ["2565"], "a_name": ["เมือง"],
                           "allstage": ["100"], "stage1": ["10"], "F3": ["10.0"]})
        d = build_data_dict("1", "D3_NCDs/โรคไต/x.csv", "x.csv", _csv(df))
        assert set(d["unknown_cols"]) >= {"F3", "allstage", "a_name"}
        assert d["confidence"] == "auto"

    def test_ไฟล์ที่ชื่อคอลัมน์ชัดเจนต้องไม่ถูกทำเครื่องหมาย(self):
        d = build_data_dict("1", PATH, "x.csv", _csv(_sample()))
        assert d["unknown_cols"] == []


class TestAggregateRowCaveat:
    """แถว "รวม" ปนกับแถวรายอำเภอ — วัดจริง 24 จาก 45 ไฟล์อัปโหลดเป็นแบบนี้

    ถ้า AI สั่ง sum() ทั้งก้อนจะได้ค่าผิดพอดี 2.00 เท่า โดยไม่มีสัญญาณเตือนอะไรเลย
    (พิสูจน์กับ 238260: รายอำเภอรวม 13,374 และแถว "รวม" = 13,374 → sum ได้ 26,748)
    """

    def _mixed(self):
        return pd.DataFrame({
            "จังหวัด": ["มุกดาหาร"] * 4,
            "ปีงบประมาณ": ["2565"] * 4,
            "อำเภอ": ["คำชะอี", "ดงหลวง", "ดอนตาล", "รวม"],
            "B": ["100", "200", "300", "600"],
        })

    def test_เตือนเมื่อมีแถวผลรวมปนอยู่(self):
        d = build_data_dict("1", PATH, "x.csv", _csv(self._mixed()))
        assert d["caveats"], "ต้องเตือน ไม่งั้น sum() ได้ค่าผิด 2 เท่าเงียบ ๆ"
        c = " ".join(d["caveats"])
        assert "ห้ามใช้ sum()" in c and "อำเภอ" in c

    def test_ไฟล์ที่มีแต่แถวรายอำเภอไม่ต้องเตือน(self):
        df = self._mixed().iloc[:3]
        assert build_data_dict("1", PATH, "x.csv", _csv(df))["caveats"] == []

    def test_ไฟล์ที่มีแต่แถวผลรวมล้วนไม่ต้องเตือน(self):
        """ถ้าทั้งไฟล์เป็นระดับจังหวัดอยู่แล้ว sum() ไม่ได้นับซ้ำ"""
        df = pd.DataFrame({
            "จังหวัด": ["มุกดาหาร", "ยโสธร"],
            "ปีงบประมาณ": ["2565", "2565"],
            "อำเภอ": ["รวม", "รวม"],
            "B": ["600", "700"],
        })
        assert build_data_dict("1", PATH, "x.csv", _csv(df))["caveats"] == []

    def test_เตือนเรื่อง_work_load_จากชื่อตัวชี้วัด(self):
        path = "D3_NCDs/โรคไต/จำนวนผู้ป่วยที่มารับบริการที่โรงพยาบาล/x.csv"
        d = build_data_dict("1", path, "x.csv", _csv(self._mixed().iloc[:3]))
        assert any("Work Load" in c for c in d["caveats"])


class TestCountingBasis:
    """ยืนยันจาก HDC schema แล้ว: typearea = ในเขตรับผิดชอบ (1 คน 1 record)
    · chronicfu = ผู้มารับบริการจริง (1 คนนับได้หลายครั้ง) — ต่างกัน 18%
    """

    def test_จับได้ทั้งสองแบบในไฟล์เดียว(self):
        df = pd.DataFrame({"จังหวัด": ["อุบลราชธานี"], "ปีงบประมาณ": ["2565"],
                           "B1_ทั้งหมด_Typearea": ["1"], "B2_ทั้งหมด_CHRONICFU": ["2"]})
        assert build_data_dict("1", PATH, "x.csv", _csv(df))["counting_basis"] == "both"

    def test_จับคำไทยที่สื่อความหมายเดียวกัน(self):
        df = pd.DataFrame({"จังหวัด": ["อุบลราชธานี"], "ปี พ.ศ.": ["2565"],
                           "ในเขต_จำนวนผู้ป่วย_B1": ["1"], "รับบริการ_จำนวนผู้ป่วย_B2": ["2"]})
        assert build_data_dict("1", PATH, "x.csv", _csv(df))["counting_basis"] == "both"


class TestKeywords:
    def test_เติมคำย่อที่คนมักพิมพ์แทน(self):
        """วัดได้จริง: ถาม 'BMI' ไม่เจอไฟล์ 'ค่าดัชนีมวลกาย' เพราะค้นจากชื่อโฟลเดอร์ตรงตัว"""
        p = "D4_Nutrition/ผู้สูงอายุ/ร้อยละของประชากรผู้สูงอายุ 60 ปีขึ้นไป มีค่าดัชนีมวลกายปกติ/x.csv"
        kw = build_data_dict("1", p, "x.csv", _csv(_sample()))["keywords"]
        assert "bmi" in kw
        assert "อ้วน" in kw

    def test_ความดันได้คำพ้องภาษาอังกฤษ(self):
        kw = build_data_dict("1", PATH, "x.csv", _csv(_sample()))["keywords"]
        assert "ht" in kw and "hypertension" in kw
