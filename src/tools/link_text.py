"""ย่อข้อความลิงก์ให้อ่านได้ โดยยังกดไปที่ URL เต็มได้เหมือนเดิม

ปัญหา — ผู้ใช้รายงาน 2026-08-03: รายการเอกสารอ้างอิงในรายงานอ่านไม่รู้เรื่อง
เพราะ URL ภาษาไทยถูกเข้ารหัสเป็น percent-encoding ทำให้ยาวมหาศาล เช่น

    https://www.scribd.com/document/852131003/%E0%B8%A3%E0%B8%B2%E0%B8%A2...
    (ยาว 300+ ตัวอักษร ทั้งที่เนื้อความจริงคือ "รายงานประจำปีกรมสุขภาพจิต")

หนึ่งรายการกินพื้นที่หลายบรรทัด รายการอ้างอิง 8 รายการจึงกลืนทั้งหน้า

วิธีแก้: ถอดรหัสกลับเป็นภาษาไทยแล้วตัดให้สั้น ใช้เป็น "ข้อความที่แสดง"
ส่วน `href` ยังเป็น URL เต็มเดิมทุกตัวอักษร — กดแล้วไปถูกที่เสมอ
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

MAX_LEN = 52


def short_link_text(url: str, max_len: int = MAX_LEN) -> str:
    """คืนข้อความสั้นที่ใช้แสดงแทน URL เต็ม

    รูปแบบ: `โดเมน/ส่วนท้ายของ path` — เก็บส่วนท้ายเพราะเป็นชื่อเอกสารจริง
    ส่วนต้นมักเป็นโฟลเดอร์ระบบที่ไม่บอกอะไร (`/document/852131003/`)

    ถ้าย่อแล้วยังไม่พอ ตัดตรงกลางแล้วคั่นด้วย `…` เก็บทั้งหัวและท้ายไว้
    เพราะท้าย path มักมีนามสกุลไฟล์ที่บอกชนิดเอกสาร (.pdf)
    """
    try:
        u = urlsplit(url)
    except Exception:
        return url[:max_len]

    host = (u.netloc or "").removeprefix("www.")
    if not host:
        return url[:max_len]

    # ถอด percent-encoding กลับเป็นข้อความอ่านได้ (ภาษาไทยส่วนใหญ่)
    path = unquote(u.path or "").strip("/")
    if u.query:
        path = f"{path}?{unquote(u.query)}" if path else unquote(u.query)

    if not path:
        return host

    # เก็บ 2 ส่วนท้ายของ path — ชื่อเอกสารมักอยู่ตรงนั้น
    segs = [s for s in path.split("/") if s]
    tail = "/".join(segs[-2:]) if len(segs) > 1 else (segs[0] if segs else "")

    room = max_len - len(host) - 1
    if room < 8:
        return host
    if len(tail) > room:
        head = tail[: room - 9]
        end = tail[-8:]
        tail = f"{head}…{end}"
    return f"{host}/{tail}"


_BARE_URL = re.compile(r'(?<![="\'(])(https?://[^\s<>"\')\]]+)')
_MD_LINK = re.compile(r'\[[^\]]*\]\((https?://[^)\s]+)\)')


def shorten_urls_markdown(text: str) -> str:
    """แปลง URL เปล่าใน markdown ให้เป็นลิงก์ที่มีข้อความสั้น

    ข้ามลิงก์ที่เขียนเป็น `[ข้อความ](url)` อยู่แล้ว — คนเขียนตั้งใจใส่ข้อความนั้น
    และข้าม URL ที่สั้นพออยู่แล้ว เพราะย่อไปก็ไม่ได้อะไร แถมอ่านยากกว่าเดิม
    """
    spans = [m.span() for m in _MD_LINK.finditer(text)]

    def _inside(pos: int) -> bool:
        return any(a <= pos < b for a, b in spans)

    def _repl(m: re.Match) -> str:
        if _inside(m.start()):
            return m.group(0)
        url = m.group(1).rstrip(".,;")
        trail = m.group(1)[len(url):]
        if len(url) <= MAX_LEN:
            return m.group(0)
        return f"[{short_link_text(url)}]({url}){trail}"

    return _BARE_URL.sub(_repl, text)
