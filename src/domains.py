"""Domain definitions — health analysis domains d0–d6."""
from dataclasses import dataclass


@dataclass
class Domain:
    code: str
    name_th: str
    name_en: str
    folder_prefix: str
    expertise: str


DOMAINS: dict[str, Domain] = {
    "d0": Domain(
        code="d0",
        name_th="ทั่วไป",
        name_en="General Advisor",
        folder_prefix="",
        expertise="ผู้เชี่ยวชาญด้านสุขภาพและข้อมูลสาธารณสุขทั่วไป วิเคราะห์ได้ทุกประเด็น",
    ),
    "d1": Domain(
        code="d1",
        name_th="อุบัติเหตุทางถนน",
        name_en="Road Accidents",
        folder_prefix="D1_Road",
        expertise="ผู้เชี่ยวชาญด้านอุบัติเหตุทางถนน การบาดเจ็บ การเสียชีวิต และความปลอดภัยบนท้องถนน",
    ),
    "d2": Domain(
        code="d2",
        name_th="สุขภาพจิต",
        name_en="Mental Health",
        folder_prefix="D2_Mental Health",
        expertise="ผู้เชี่ยวชาญด้านสุขภาพจิต การฆ่าตัวตาย ภาวะซึมเศร้า และบริการจิตเวช",
    ),
    "d3": Domain(
        code="d3",
        name_th="โรคไม่ติดต่อ",
        name_en="NCDs",
        folder_prefix="D3_NCDs",
        expertise="ผู้เชี่ยวชาญด้านโรคไม่ติดต่อเรื้อรัง เช่น เบาหวาน ความดันโลหิตสูง โรคหัวใจ โรคหลอดเลือดสมอง",
    ),
    "d4": Domain(
        code="d4",
        name_th="โภชนาการ",
        name_en="Nutrition",
        folder_prefix="D4_Nutrition",
        expertise="ผู้เชี่ยวชาญด้านโภชนาการ ภาวะทุพโภชนาการ โรคอ้วน และความมั่นคงทางอาหาร",
    ),
    "d5": Domain(
        code="d5",
        name_th="ประชากร",
        name_en="Population",
        folder_prefix="D5_Population",
        expertise=(
            "ผู้เชี่ยวชาญด้านโครงสร้างประชากร จำนวนประชากรรายอำเภอ/ตำบล "
            "แยกตามเพศและช่วงอายุ ใช้เป็นฐานประชากร (ตัวหาร) ในการคำนวณอัตราต่อประชากร"
        ),
    ),
    "d6": Domain(
        code="d6",
        name_th="อื่น ๆ",
        name_en="Other",
        folder_prefix="D6_Other",
        expertise=(
            "ข้อมูลสุขภาพที่ยังไม่เข้าโดเมนใดโดยเฉพาะ — ใช้เมื่อคำถามไม่ตรงกับ "
            "โดเมนอื่นแต่ยังเป็นเรื่องสุขภาพในเขตสุขภาพที่ 10"
        ),
    ),
    "dt": Domain(
        code="dt",
        name_th="วิจัย ThaiJo",
        name_en="ThaiJo Research",
        folder_prefix="",
        expertise=(
            "ผู้เชี่ยวชาญด้านการสังเคราะห์งานวิจัยทางวิชาการ "
            "ค้นหาและสรุปบทความจากฐานข้อมูล ThaiJo สร้างรายงานวิชาการอัตโนมัติ"
        ),
    ),
    "obsidian": Domain(
        code="obsidian",
        name_th="คลังความรู้สุขภาพ เขต 10",
        name_en="Obsidian Knowledge Vault",
        folder_prefix="",
        expertise=(
            "ผู้เชี่ยวชาญด้านข้อมูลสุขภาพเขตสุขภาพที่ 10 (อุบลราชธานี ศรีสะเกษ ยโสธร อำนาจเจริญ มุกดาหาร) "
            "ค้นหาและตอบคำถามจาก Obsidian Knowledge Vault ซึ่งเป็นฐานความรู้ "
            "ด้านนโยบาย รายงาน และข้อมูลสุขภาพของเขตสุขภาพที่ 10"
        ),
    ),
}

DOMAIN_LIST_TEXT = "\n".join(
    f"- {d.code}: {d.name_th} ({d.name_en})"
    for d in DOMAINS.values()
)

# ── โดเมนที่มีไฟล์ CSV ให้ค้น ────────────────────────────────────────────────
#
# คำนวณจาก `folder_prefix` แทนการเขียนรายชื่อไว้เอง — เดิมมีรายชื่อ {"d2","d3","d4"}
# กระจายอยู่ 4 ที่ (router / analyze × 3) พอเพิ่มโดเมนใหม่แล้วลืมแก้ที่ใดที่หนึ่ง
# ผลคือไฟล์อยู่ในคลังจริงแต่ AI ค้นไม่เจอ **โดยไม่มีอะไรฟ้อง**
# (เจอมาแล้วกับโฟลเดอร์ D5_Other 43 ไฟล์ ที่ไม่มีโดเมนไหนรับผิดชอบ)
#
# d0/dt/obsidian ไม่มี prefix โดยตั้งใจ — ไม่ได้อ่านจาก CSV
CSV_DOMAIN_CODES: set[str] = {c for c, d in DOMAINS.items() if d.folder_prefix}

# แผนที่ prefix → รหัสโดเมน สำหรับเดาโดเมนจาก path ของไฟล์ (`D3_NCDs/...` → `d3`)
# ใช้ 2 ตัวแรกของ prefix เป็นกุญแจ เผื่อชื่อโฟลเดอร์จริงยาวกว่า prefix
#
# ⚠️ `folder_prefix` ต้องสะกดตรงกับ **ชื่อโฟลเดอร์จริงในคลัง** เป๊ะ ๆ
# เพราะ `vault_placement` ใช้ค่านี้ตั้งชื่อโฟลเดอร์ตอนนำเข้าไฟล์ใหม่
# เคยเพี้ยนมาแล้ว: โค้ดเขียน `D3_NCD` แต่คลังใช้ `D3_NCDs` ⇒ กลายเป็นแหล่งความจริง
# 2 ที่ที่ขัดกันเอง · ถ้าจะแก้ค่าตรงนี้ ต้องย้ายไฟล์ในคลังตามด้วยเสมอ
FOLDER_PREFIX_TO_DOMAIN: dict[str, str] = {
    d.folder_prefix[:2].upper(): c for c, d in DOMAINS.items() if d.folder_prefix
}
