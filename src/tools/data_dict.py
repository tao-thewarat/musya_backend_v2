"""สร้าง "พจนานุกรมข้อมูล" ของไฟล์ CSV สถิติ — เฟส 1 (อัตโนมัติล้วน ไม่ใช้ LLM)

ทำไมไม่ใช้ LLM ในเฟสนี้: ผลต้องซ้ำได้ทุกครั้ง ตรวจสอบย้อนกลับได้ และรัน 45 ไฟล์
ให้จบในไม่กี่วินาที · ส่วนที่ต้องตีความจริง ๆ (คำอธิบายคอลัมน์ที่กำกวม, ข้อควรระวัง)
จงใจปล่อยว่างไว้ให้คนเติม แล้วทำเครื่องหมาย `unknown_cols` ไว้ให้เห็นชัด

สิ่งที่แก้: เดิม metadata ของไฟล์ CSV มีแต่ APA (Author/Abstract/KeyStats ว่างหมด
เพราะออกแบบมาสำหรับงานวิจัย ไม่ใช่ตารางตัวเลข) ⇒ AI ไม่รู้ว่าไฟล์ครอบคลุมปีไหน
จังหวัดไหน คอลัมน์ไหนเป็นตัวตั้ง/ตัวหาร และมีข้อควรระวังอะไร
"""
from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

from src.domains import FOLDER_PREFIX_TO_DOMAIN

# ── ชื่อคอลัมน์ที่ใช้เป็นแกน — ต้องตรงกับ multi_csv_pipeline ─────────────────
_PROV_COLS = {"จังหวัด", "province", "Province"}
_DIST_COLS = {"อำเภอ", "district", "District", "a_name", "sub-province", "subprovince"}
_YEAR_COLS = {"ปี", "ปี พ.ศ.", "ปี_พศ", "ปีงบประมาณ", "ปี_ข้อมูล", "Year_BE", "year"}

ZONE10 = ("อุบลราชธานี", "ศรีสะเกษ", "ยโสธร", "อำนาจเจริญ", "มุกดาหาร")

# ── คอลัมน์ที่ "อ่านแล้วเดาความหมายไม่ออก" — ต้องให้คนยืนยัน ────────────────
# F3/F5/F8 เกิดจากการ export ที่หัวคอลัมน์หาย ระบบตั้งชื่อตามลำดับคอลัมน์ให้เอง
_OPAQUE = re.compile(r"^(F\d+|result\d*(_\d)?|target(_\d)?|allstage|a_name|Unnamed.*|col\d+)$", re.I)


def _norm(col: str) -> str:
    return str(col).strip().lower().replace(" ", "").replace("_", "")


def _pick(cols: list[str], wanted: set[str]) -> str:
    """หาคอลัมน์ที่ตรงกับชุดชื่อที่ต้องการ (เทียบแบบ normalize แล้วตรงทั้งคำ)"""
    want = {_norm(w) for w in wanted}
    for c in cols:
        if _norm(c) in want:
            return str(c)
    return ""


def _role_of(col: str) -> str:
    """เดา "บทบาท" ของคอลัมน์จากรูปแบบการตั้งชื่อที่ใช้กันใน HDC

    B = ตัวหาร (ฐานประชากรเป้าหมาย) · A = ตัวตั้ง (ผู้ผ่านเกณฑ์)
    ยืนยันจากคู่ที่เขียนกำกับไว้ชัด เช่น `ประชากร_B` + `คัดกรองแล้ว_A` + `รวม_%`
    """
    c = str(col)
    if re.search(r"ร้อยละ|%|percent|rate|อัตรา", c, re.I):
        return "percentage"
    if re.search(r"(^|[_ (])B\d?([_ )]|$)", c):
        return "denominator"
    if re.search(r"(^|[_ (])A\d?([_ )]|$)", c):
        return "numerator"
    if _norm(c) in {_norm(x) for x in (_PROV_COLS | _DIST_COLS | _YEAR_COLS)}:
        return "key"
    return "measure"


def _unit_of(col: str, series: pd.Series) -> str:
    c = str(col)
    if re.search(r"ร้อยละ|%|percent", c, re.I):
        return "ร้อยละ"
    if re.search(r"อัตรา.*แสน|per_?100", c, re.I):
        return "ต่อแสนประชากร"
    if re.search(r"คน|ราย|จำนวน|count", c):
        return "คน"
    return "ตัวเลข" if pd.api.types.is_numeric_dtype(series) else "ข้อความ"


def _counting_basis(cols: list[str]) -> str:
    """ยืนยันจาก HDC: typearea = ในเขตรับผิดชอบ (ตัดซ้ำแล้ว 1 คน 1 record)
    chronicfu = ผู้มารับบริการจริง (1 คนนับได้หลายครั้ง)
    """
    blob = " ".join(cols).lower()
    has_ta = "typearea" in blob or "ในเขต" in blob
    # ⚠️ ต้องจับ "chronic" ไม่ใช่ "chronicfu" — ไฟล์จริงตั้งชื่อไม่เหมือนกัน
    # 486950 ใช้ `B2 ทั้งหมด CHRONICFU` ส่วน 975678 ใช้ `chronic_จำนวนผู้ป่วย_รวม`
    # เขียนเป็น chronicfu อย่างเดียวทำให้ 975678 ถูกจัดเป็น typearea ล้วน ทั้งที่มี 2 ชุด
    has_fu = "chronic" in blob or "รับบริการ" in blob
    if has_ta and has_fu:
        return "both"
    if has_ta:
        return "typearea"
    if has_fu:
        return "chronicfu"
    return ""


def _keywords(path: str, cols: list[str]) -> list[str]:
    """คำค้นจากชื่อโฟลเดอร์ + คำย่อที่คนมักพิมพ์แทน

    แก้ปัญหาที่วัดได้จริง: ถาม "BMI" ไม่เจอไฟล์ `ค่าดัชนีมวลกาย` เพราะค้นจากชื่อ
    โฟลเดอร์ตรงตัวอย่างเดียว · ตรงนี้เติมคำพ้องแบบกฎตายตัว ส่วนคำพ้องที่ต้อง
    ตีความจริง ๆ เป็นงานของเฟส 3 ที่ให้ LLM ช่วย
    """
    # ⚠️ ต้องใช้ "แกนคำที่สั้นที่สุดที่ยังไม่กำกวม" เป็นตัวจับ — โฟลเดอร์จริงเขียน
    # ว่า "โรคความดัน" บ้าง "ความดันโลหิตสูง" บ้าง ถ้าตั้ง key เป็นคำยาวจะไม่ติด
    SYN = {
        "ความดัน": ["ht", "hypertension", "ความดันโลหิต"],
        "เบาหวาน": ["dm", "diabetes", "น้ำตาล"],
        "ดัชนีมวลกาย": ["bmi", "อ้วน", "ผอม", "น้ำหนัก"],
        "รอบเอว": ["bmi", "อ้วนลงพุง", "obesity"],
        "ไต": ["ckd", "kidney", "egfr"],
        "จิตเวช": ["สุขภาพจิต", "mental"],
        "ซึมเศร้า": ["depression"],
        "จิตเภท": ["schizophrenia"],
        "โลหิตจาง": ["anemia", "ธาตุเหล็ก"],
        "หัวใจ": ["cvd", "cardiovascular"],
        "สูงดีสมส่วน": ["โภชนาการ", "ส่วนสูง"],
    }
    out: set[str] = set()
    for part in path.split("/"):
        for tok in re.split(r"[\s,()\[\]/–—-]+", part):
            tok = tok.strip()
            if len(tok) >= 3:
                out.add(tok)
    low = path.lower()
    for k, syns in SYN.items():
        if k.lower() in low:
            out.update(syns)
            out.add(k)
    for c in cols:
        if re.search(r"typearea|chronicfu", str(c), re.I):
            out.update(["ในเขตรับผิดชอบ", "รับบริการ"])
    return sorted(out)


# ค่าที่แปลว่า "แถวนี้เป็นผลรวม ไม่ใช่หน่วยย่อย" — ปนอยู่ในคอลัมน์อำเภอ/จังหวัด
_AGG_VALUES = {"รวม", "ทั้งหมด", "รวมทั้งหมด", "ผลรวม", "รวมทั้งสิ้น", "total", "sum"}


def detect_frame_caveats(df, key_dist: str, key_prov: str, indicator: str) -> list[str]:
    """หาข้อควรระวังจาก "เนื้อไฟล์" — ใช้ได้ทั้งไฟล์อัปโหลดและไฟล์ที่ดึงจาก API

    ต่างจาก `_detect_caveats()` ใน hdc_import ที่ต้องใช้สถิติรายปีตอนดึง
    ตัวนี้ดูจาก DataFrame ล้วน ๆ จึงย้อนไปตรวจไฟล์เก่าที่มีอยู่แล้วได้
    """
    out: list[str] = []

    # ── แถวผลรวมปนกับแถวรายหน่วย ⇒ sum() ทั้งก้อนได้ค่าเป็น 2 เท่า ──────────
    # วัดจริง 2026-07-31: 24 จาก 45 ไฟล์อัปโหลดมีปัญหานี้
    # เช่น 238260 มุกดาหาร 2565 รายอำเภอรวมได้ 13,374 และมีแถว "รวม" = 13,374
    # ถ้า AI สั่ง groupby().sum() จะได้ 26,748 = ผิดพอดี 2.00 เท่า โดยไม่มีสัญญาณเตือน
    for key, level in ((key_dist, "อำเภอ"), (key_prov, "จังหวัด")):
        if not key or key not in df.columns:
            continue
        vals = df[key].dropna().astype(str).str.strip()
        n_agg = int(vals.isin(_AGG_VALUES).sum())
        if n_agg and n_agg < len(vals):
            found = sorted(set(vals[vals.isin(_AGG_VALUES)]))
            out.append(
                f"ไฟล์นี้มีแถวผลรวมปนอยู่กับแถวราย{level} — คอลัมน์ '{key}' มีค่า "
                f"{'/'.join(found)} อยู่ {n_agg:,} แถว "
                f"**ห้ามใช้ sum() กับทั้งไฟล์** เพราะจะนับซ้ำเป็น 2 เท่า "
                f"ต้องกรองแถวผลรวมออกก่อน (หรือใช้เฉพาะแถวผลรวมอย่างเดียว) "
                f"แล้วบอกผู้ใช้ด้วยว่าเลือกวิธีไหน"
            )
            break        # เตือนครั้งเดียวพอ ระดับอำเภอครอบคลุมจังหวัดอยู่แล้ว

    # ── Work Load ─────────────────────────────────────────────────────────
    if "มารับบริการที่โรงพยาบาล" in indicator:
        out.append(
            "ตัวเลขนี้เป็น Work Load ไม่ใช่จำนวนคน — ผู้ป่วย 1 คนไปรับบริการหลาย "
            "โรงพยาบาลจะถูกนับหลายครั้ง ต้องตอบว่า 'มีการมารับบริการ N ราย' "
            "ห้ามตอบว่า 'มีผู้ป่วย N คน'"
        )
    return out


def build_data_dict(file_id: str, vault_path: str, file_name: str, raw: bytes) -> dict[str, Any]:
    """อ่าน CSV แล้วสรุปเป็นพจนานุกรมข้อมูล — ไม่แก้ไฟล์ต้นฉบับ"""
    df = pd.read_csv(io.BytesIO(raw), dtype=str)
    cols = [str(c) for c in df.columns]

    parts = vault_path.split("/")
    domain = FOLDER_PREFIX_TO_DOMAIN.get(parts[0][:2].upper(), "")
    indicator = parts[-2] if len(parts) > 2 else file_name.rsplit(".", 1)[0]

    key_prov = _pick(cols, _PROV_COLS)
    key_dist = _pick(cols, _DIST_COLS)
    key_year = _pick(cols, _YEAR_COLS)

    # ── ปีที่อยู่ในเนื้อไฟล์จริง ────────────────────────────────────────────
    # ⚠️ ห้ามเชื่อปีในชื่อไฟล์ — 486950 เขียน "2569-2569" แต่ข้างในมีตั้งแต่ 2565
    years: set[str] = set()
    if key_year:
        for v in df[key_year].dropna().astype(str):
            years.update(re.findall(r"25[4-8]\d", v))
            years.update(str(int(y) + 543) for y in re.findall(r"^20[0-3]\d$", v.strip()))

    provinces = []
    if key_prov:
        seen = {str(v).strip() for v in df[key_prov].dropna()}
        provinces = [p for p in ZONE10 if any(p in s for s in seen)]

    if key_dist:
        granularity = "อำเภอ"
    elif key_prov:
        granularity = "จังหวัด"
    else:
        granularity = "ไม่มีมิติพื้นที่"
    if any(_norm(c) == "hospcode" for c in cols):
        granularity = "หน่วยบริการ"

    columns = []
    unknown = []
    for c in cols:
        role = _role_of(c)
        entry = {"name": c, "role": role, "unit": _unit_of(c, df[c])}
        if _OPAQUE.match(c.strip()):
            entry["desc"] = ""
            unknown.append(c)
        columns.append(entry)

    return {
        "file_id": file_id,
        "vault_path": vault_path,
        "file_name": file_name,
        "domain": domain,
        "indicator_th": indicator,
        "years": sorted(years),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "provinces": provinces,
        "granularity": granularity,
        "row_count": int(len(df)),
        "col_count": int(len(cols)),
        "key_province": key_prov,
        "key_district": key_dist,
        "key_year": key_year,
        "keywords": _keywords(vault_path, cols),
        "columns": columns,
        "unknown_cols": unknown,
        "caveats": detect_frame_caveats(df, key_dist, key_prov, indicator),
        "counting_basis": _counting_basis(cols),
        # ยังไม่มีคนยืนยัน — ไฟล์ที่มี unknown_cols ต้องถูกจับตาเป็นพิเศษ
        "confidence": "auto",
    }
