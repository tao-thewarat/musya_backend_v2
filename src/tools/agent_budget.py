"""จัดสรร "งบ agent" ให้ผู้ใช้ที่กำลังใช้งานพร้อมกัน + หน่วงเมื่อชนโควตา Gemini

ปัญหาเดิม (analyze.py):

    _AI_SEMAPHORE = threading.BoundedSemaphore(5)
    if not _AI_SEMAPHORE.acquire(blocking=False):
        yield "ระบบกำลังประมวลผลเต็มความสามารถ กรุณารอสักครู่แล้วลองใหม่"

- รับได้ **5 request** ทั้งระบบ ⇒ 60 คนกดพร้อมกัน **55 คนโดนเด้งทันที**
- `blocking=False` = **ไม่มีคิว** ผู้ใช้ต้องกดเองใหม่เรื่อย ๆ ซึ่งยิ่งซ้ำเติมโหลด
- เลข 5 ไม่ได้มาจากการวัด — หาที่มาในโค้ด/เอกสารไม่เจอ

แนวคิดใหม่ — **แบ่งงบรวมให้คนที่กำลังใช้งานเท่า ๆ กัน**

    งบต่อคน = clamp(งบรวม ÷ คนที่ใช้อยู่, MIN_PER_USER, MAX_PER_USER)

ด้วย TOTAL=70 · MIN=1 · MAX=7 จะได้:
    10 คน → 7 ตัว/คน · 35 คน → 2 · 60 คน → 1 · เกิน 70 คน → 1 + เข้าคิว

`MIN_PER_USER = 1` สำคัญ — ทุกคนต้องได้ทำงานเสมอ ไม่มีใครถูกอดตาย
เมื่อคนล้นจึงเข้าคิวแทนการลดส่วนแบ่งต่ำกว่า 1

⚠️ ตัวเลข TOTAL เป็น "การเดาที่ตั้งใจ" เหมือนเลข 5 เดิม — ต้องวัดโหลดจริงแล้วปรับ
ดู [[12 - Agent Concurrency — รองรับ 60-70 users]] ในวอลต์
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, "") or default))
    except ValueError:
        return default


# ── ค่าตั้งต้น (ปรับผ่าน env ได้โดยไม่ต้องแก้โค้ด) ────────────────────────────
TOTAL_AGENTS = _env_int("AGENT_BUDGET_TOTAL", 70)
MIN_PER_USER = _env_int("AGENT_BUDGET_MIN_PER_USER", 1)
MAX_PER_USER = _env_int("AGENT_BUDGET_MAX_PER_USER", 7)

# ผู้ใช้ที่ไม่มีความเคลื่อนไหวเกินเวลานี้ ถือว่าออกไปแล้ว — ถ้าไม่ล้าง งบต่อคนจะ
# หดลงเรื่อย ๆ จากคนที่ปิดแท็บทิ้งไว้แต่ระบบยังนับว่ายังอยู่
USER_IDLE_TIMEOUT = _env_int("AGENT_BUDGET_IDLE_TIMEOUT", 900)

# กันงบส่วนหนึ่งไว้ให้ "งานเบา" เสมอ — ไม่งั้นงานหนัก (สร้างรายงาน 20–24 call)
# จะกินงบหมด แล้วคำถามสั้น ๆ ที่ควรตอบใน 3 วินาทีต้องรอเป็นนาที
LIGHT_LANE_RESERVE = 0.20

# น้ำหนักงานของแต่ละโหมด — ใช้ตัดสินว่าขอ slot กี่ตัวและอยู่เลนไหน
LANE_LIGHT, LANE_MEDIUM, LANE_HEAVY = "light", "medium", "heavy"
_MODE_LANE: dict[str, str] = {
    "obsidian": LANE_LIGHT, "tavily": LANE_LIGHT, "chat": LANE_LIGHT,
    "stats": LANE_MEDIUM, "thaijo": LANE_MEDIUM, "pubmed": LANE_MEDIUM,
    "report-gather": LANE_HEAVY, "report-gather-retry": LANE_HEAVY,
    "thaijo-report": LANE_HEAVY,
}
_LANE_WANT = {LANE_LIGHT: 1, LANE_MEDIUM: 3, LANE_HEAVY: MAX_PER_USER}


def lane_of(mode: str) -> str:
    return _MODE_LANE.get(mode or "", LANE_LIGHT)


def want_for(mode: str) -> int:
    return _LANE_WANT.get(lane_of(mode), 1)


# ── แรงดันจากโควตา Gemini ────────────────────────────────────────────────────
@dataclass
class PressureController:
    """หดงบรวมเมื่อเจอ 429 แล้วค่อย ๆ คืนเมื่อเงียบ

    **ลดเร็ว คืนช้า** — โควตาที่ตันแล้วต้องใช้เวลาฟื้น การรีบคืนงบจะไปตันซ้ำทันที
    (เหตุผลเดียวกับ jitter ใน agent_defaults.py ที่กัน thundering herd)
    """

    shrink_ratio: float = 0.70      # เจอ 429 → เหลือ 70% ของงบเดิม
    recover_ratio: float = 1.10     # เงียบครบรอบ → คืน 10%
    recover_after: float = 60.0     # วินาทีที่ต้องเงียบก่อนเริ่มคืน
    floor: float = 0.25             # ต่ำสุดไม่ต่ำกว่า 25% ของงบเต็ม

    _factor: float = 1.0
    _last_429: float = 0.0
    _last_recover: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    #: รายละเอียดที่ Google ส่งมาตอน 429 — เก็บไว้เพื่อ "รู้เพดานจริง" แทนการเดา
    last_quota_detail: str = ""
    total_429: int = 0

    #: ผู้รอฟังเหตุการณ์ 429 — request ที่กำลังทำงานอยู่ลงทะเบียนไว้เพื่อ
    #: แจ้งผู้ใช้ว่า "ทำไมถึงช้า" แทนที่จะเงียบแล้วปล่อยให้เดาเอา
    _listeners: list = field(default_factory=list)

    def add_listener(self, fn) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def report_429(self, message: str = "") -> None:
        with self._lock:
            self.total_429 += 1
            self._last_429 = time.monotonic()
            self._factor = max(self.floor, self._factor * self.shrink_ratio)
            detail = _extract_quota_detail(message)
            if detail:
                self.last_quota_detail = detail
            logger.warning(
                "429 จาก Gemini (ครั้งที่ %d) — หดงบเหลือ %.0f%%%s",
                self.total_429, self._factor * 100,
                f" · {detail}" if detail else "",
            )
            listeners = list(self._listeners)
            factor = self._factor
        # เรียกนอก lock — callback ไปแตะ asyncio queue ของ request อื่น
        # ถ้าเรียกใน lock แล้วมันช้า จะหน่วง retry ของทุก agent ที่รออยู่
        for fn in listeners:
            try:
                fn(factor, detail)
            except Exception:
                pass  # ผู้ฟังคนหนึ่งพังต้องไม่ทำให้คนอื่นไม่ได้รับแจ้ง

    def factor(self) -> float:
        """คืนตัวคูณงบปัจจุบัน พร้อมทยอยฟื้นถ้าเงียบมานานพอ"""
        with self._lock:
            if self._factor >= 1.0:
                return 1.0
            now = time.monotonic()
            if now - self._last_429 < self.recover_after:
                return self._factor
            if now - self._last_recover < self.recover_after:
                return self._factor
            self._last_recover = now
            self._factor = min(1.0, self._factor * self.recover_ratio)
            logger.info("ไม่เจอ 429 มา %.0fs — คืนงบเป็น %.0f%%",
                        self.recover_after, self._factor * 100)
            return self._factor


# Google ส่งชื่อโควตาและค่าเพดานมาในข้อความ error — ดึงเก็บไว้จะได้ไม่ต้องเดาเพดาน
_QUOTA_RE = re.compile(
    r"(quota_metric|quotaMetric|QuotaFailure|limit:?\s*\d+|"
    r"GenerateRequests?PerMinute\w*|PerDay\w*)[^\n]{0,120}", re.I)


def _extract_quota_detail(message: str) -> str:
    if not message:
        return ""
    m = _QUOTA_RE.search(message)
    return m.group(0).strip()[:160] if m else ""


# ── งบ agent ─────────────────────────────────────────────────────────────────
class AgentBudget:
    def __init__(
        self,
        total: int = TOTAL_AGENTS,
        min_per_user: int = MIN_PER_USER,
        max_per_user: int = MAX_PER_USER,
    ) -> None:
        self.total = total
        self.min_per_user = min_per_user
        self.max_per_user = max_per_user
        self.pressure = PressureController()
        self._lock = threading.Condition()
        self._in_use = 0
        self._per_user_use: dict[str, int] = {}
        self._seen: dict[str, float] = {}   # user_id → เวลาที่เห็นล่าสุด
        self._waiting = 0

    # -- จำนวนคนที่กำลังใช้งาน --------------------------------------------------
    def touch(self, user_id: str) -> None:
        with self._lock:
            self._seen[user_id] = time.monotonic()
            self._sweep_locked()

    def _sweep_locked(self) -> None:
        cutoff = time.monotonic() - USER_IDLE_TIMEOUT
        stale = [u for u, t in self._seen.items()
                 if t < cutoff and not self._per_user_use.get(u)]
        for u in stale:
            self._seen.pop(u, None)

    def active_users(self) -> int:
        with self._lock:
            self._sweep_locked()
            return max(1, len(self._seen))

    def effective_total(self) -> int:
        """งบรวมหลังหักแรงดันจาก Gemini"""
        return max(self.min_per_user,
                   int(self.total * self.pressure.factor()))

    def per_user_limit(self) -> int:
        n = self.active_users()
        share = self.effective_total() // n
        return max(self.min_per_user, min(self.max_per_user, share))

    # -- ขอ/คืน slot ------------------------------------------------------------
    def acquire(self, user_id: str, want: int, timeout: float = 300.0,
                lane: str = LANE_LIGHT) -> int:
        """ขอ slot — คืน **จำนวนที่ได้จริง** ซึ่งอาจน้อยกว่าที่ขอ

        คืนจำนวนแทน True/False โดยตั้งใจ: Research Planner วางแผน 7 ขั้นแต่ได้งบ 2
        ควร "ยิง 2 ขั้นก่อน ที่เหลือทยอยตาม" ไม่ใช่ล้มทั้งงานหรือฝ่าเพดานไปเลย

        คืน 0 เมื่อรอจนหมดเวลา — ผู้เรียกตัดสินใจเองว่าจะแจ้งผู้ใช้อย่างไร
        """
        want = max(1, want)
        deadline = time.monotonic() + timeout
        with self._lock:
            self._seen[user_id] = time.monotonic()
            self._waiting += 1
            try:
                while True:
                    granted = self._grantable_locked(user_id, want, lane)
                    if granted > 0:
                        self._in_use += granted
                        self._per_user_use[user_id] = (
                            self._per_user_use.get(user_id, 0) + granted)
                        # ⚠️ ต้องประทับเวลาใหม่ "หลัง" จัดสรรเสร็จ
                        # `_grantable_locked` เรียก `_sweep_locked` ก่อนจัดสรร
                        # ตอนนั้นผู้ใช้ยังไม่มี slot จึงเข้าเกณฑ์ถูกล้างได้
                        # ⇒ จะหลุดจาก `_seen` ทั้งที่กำลังจะเริ่มทำงาน
                        self._seen[user_id] = time.monotonic()
                        return granted
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return 0
                    self._lock.wait(min(remaining, 1.0))
            finally:
                self._waiting -= 1

    def _grantable_locked(self, user_id: str, want: int, lane: str) -> int:
        self._sweep_locked()
        n = max(1, len(self._seen))
        total = max(self.min_per_user, int(self.total * self.pressure.factor()))
        limit = max(self.min_per_user, min(self.max_per_user, total // n))

        used_by_user = self._per_user_use.get(user_id, 0)
        room_user = limit - used_by_user
        if room_user <= 0:
            return 0

        # งานหนักห้ามกินงบที่กันไว้ให้งานเบา
        ceiling = total
        if lane == LANE_HEAVY:
            ceiling = max(self.min_per_user, int(total * (1 - LIGHT_LANE_RESERVE)))
        room_global = ceiling - self._in_use
        if room_global <= 0:
            return 0

        return max(0, min(want, room_user, room_global))

    def release(self, user_id: str, n: int) -> None:
        if n <= 0:
            return
        with self._lock:
            self._in_use = max(0, self._in_use - n)
            left = self._per_user_use.get(user_id, 0) - n
            if left > 0:
                self._per_user_use[user_id] = left
            else:
                self._per_user_use.pop(user_id, None)
            self._seen[user_id] = time.monotonic()
            self._lock.notify_all()

    # -- ข้อมูลสำหรับแสดงผล/ตรวจสอบ ---------------------------------------------
    def queue_position(self) -> int:
        with self._lock:
            return self._waiting

    def snapshot(self) -> dict:
        with self._lock:
            self._sweep_locked()
            users = max(1, len(self._seen))
        total = self.effective_total()
        return {
            "active_users": users,
            "per_user_limit": max(self.min_per_user,
                                  min(self.max_per_user, total // users)),
            "in_use": self._in_use,
            "total": self.total,
            "effective_total": total,
            "pressure_factor": round(self.pressure.factor(), 3),
            "total_429": self.pressure.total_429,
            "last_quota_detail": self.pressure.last_quota_detail,
            "waiting": self._waiting,
        }


_BUDGET: AgentBudget | None = None
_BUDGET_LOCK = threading.Lock()


def get_budget() -> AgentBudget:
    global _BUDGET
    if _BUDGET is None:
        with _BUDGET_LOCK:
            if _BUDGET is None:
                _BUDGET = AgentBudget()
                logger.info(
                    "AgentBudget เริ่มทำงาน — งบรวม %d · ต่อคน %d–%d",
                    TOTAL_AGENTS, MIN_PER_USER, MAX_PER_USER)
    return _BUDGET


class Lease:
    """ถือ slot ไว้จนจบงาน — ใช้เป็น context manager กันลืมคืน"""

    def __init__(self, user_id: str, granted: int) -> None:
        self.user_id = user_id
        self.granted = granted

    def __enter__(self) -> "Lease":
        return self

    def __exit__(self, *exc: object) -> None:
        get_budget().release(self.user_id, self.granted)
        self.granted = 0
