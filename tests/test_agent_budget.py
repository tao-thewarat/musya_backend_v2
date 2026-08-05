"""Tests: งบ agent ปรับตามจำนวนผู้ใช้ และหน่วงเมื่อชนโควตา Gemini

โจทย์จากผู้ใช้ 2026-08-05:
  60–70 คนพร้อมกัน → คนละ 1–2 ตัว
  10 คน            → คนละได้ถึง 7 ตัว
  ชนเพดาน Gemini   → หน่วง ไม่ใช่ปฏิเสธ

ของเดิม: BoundedSemaphore(5) + acquire(blocking=False)
⇒ 60 คนกดพร้อมกัน **55 คนโดนเด้งทันที ไม่มีคิว**
"""
import threading
import time

from src.tools.agent_budget import (
    AgentBudget, LANE_HEAVY, LANE_LIGHT, Lease, PressureController,
    lane_of, want_for,
)


def _budget(users: int, total: int = 70) -> AgentBudget:
    b = AgentBudget(total=total, min_per_user=1, max_per_user=7)
    for i in range(users):
        b.touch(f"u{i}")
    return b


class TestPerUserShare:
    """ตัวเลขต้องตรงกับที่ผู้ใช้ระบุมาเป๊ะ"""

    def test_สิบคนได้คนละเจ็ด(self):
        assert _budget(10).per_user_limit() == 7

    def test_หกสิบคนได้คนละหนึ่ง(self):
        assert _budget(60).per_user_limit() == 1

    def test_เจ็ดสิบคนได้คนละหนึ่ง(self):
        assert _budget(70).per_user_limit() == 1

    def test_สามสิบห้าคนได้คนละสอง(self):
        assert _budget(35).per_user_limit() == 2

    def test_คนน้อยไม่เกินเพดานบน(self):
        """5 คน → 70/5 = 14 แต่ต้องถูกจำกัดที่ 7"""
        assert _budget(5).per_user_limit() == 7
        assert _budget(1).per_user_limit() == 7

    def test_คนล้นยังได้อย่างน้อยหนึ่ง(self):
        """ทุกคนต้องได้ทำงานเสมอ ไม่มีใครถูกอดตาย"""
        assert _budget(200).per_user_limit() == 1


class TestAcquireGrantsPartial:
    def test_คืนจำนวนที่ได้จริงไม่ใช่ทั้งหมดหรือไม่ได้เลย(self):
        """Planner วางแผน 7 ขั้นแต่ได้งบ 2 ⇒ ยิง 2 ก่อน ไม่ใช่ล้มทั้งงาน"""
        b = _budget(35)                      # งบต่อคน = 2
        assert b.acquire("u0", want=7, timeout=1) == 2

    def test_ขอน้อยกว่างบได้ตามที่ขอ(self):
        assert _budget(10).acquire("u0", want=3, timeout=1) == 3

    def test_ขอเกินงบตัวเองรอบสองได้ศูนย์(self):
        b = _budget(35)                      # งบต่อคน = 2
        assert b.acquire("u0", want=2, timeout=1) == 2
        assert b.acquire("u0", want=1, timeout=0.2) == 0

    def test_คืนแล้วขอใหม่ได้(self):
        b = _budget(35)
        got = b.acquire("u0", want=2, timeout=1)
        b.release("u0", got)
        assert b.acquire("u0", want=2, timeout=1) == 2

    def test_lease_คืน_slot_ให้อัตโนมัติ(self):
        b = _budget(10)
        import src.tools.agent_budget as ab
        ab._BUDGET = b
        with Lease("u0", b.acquire("u0", want=3, timeout=1)):
            assert b.snapshot()["in_use"] == 3
        assert b.snapshot()["in_use"] == 0


class TestQueueNotReject:
    def test_รอแล้วได้เมื่อคนอื่นคืน_ไม่ใช่เด้งทันที(self):
        """หัวใจของการเปลี่ยนจาก acquire(blocking=False)"""
        b = AgentBudget(total=2, min_per_user=1, max_per_user=1)
        b.touch("a"); b.touch("b")
        assert b.acquire("a", want=1, timeout=1) == 1
        assert b.acquire("b", want=1, timeout=1) == 1

        got: list[int] = []

        def later():
            got.append(b.acquire("c", want=1, timeout=5))

        t = threading.Thread(target=later)
        t.start()
        time.sleep(0.3)
        b.release("a", 1)
        t.join(timeout=5)
        assert got == [1], "ต้องได้ slot หลังคนอื่นคืน ไม่ใช่ถูกปฏิเสธ"

    def test_รอจนหมดเวลาคืนศูนย์(self):
        b = AgentBudget(total=1, min_per_user=1, max_per_user=1)
        b.touch("a")
        assert b.acquire("a", want=1, timeout=1) == 1
        assert b.acquire("a", want=1, timeout=0.3) == 0


class TestGeminiPressure:
    def test_เจอ429แล้วงบหด(self):
        b = _budget(10)
        before = b.effective_total()
        b.pressure.report_429("RESOURCE_EXHAUSTED")
        assert b.effective_total() < before

    def test_หดหลายครั้งไม่ต่ำกว่าพื้น(self):
        p = PressureController()
        for _ in range(50):
            p.report_429()
        assert p.factor() >= p.floor

    def test_เก็บรายละเอียดโควตาที่_google_ส่งมา(self):
        """เดิม log แค่ว่าเจอแล้ว sleep กี่วิ ⇒ ไม่เคยรู้เพดานจริง"""
        p = PressureController()
        p.report_429("429 RESOURCE_EXHAUSTED quota_metric: generate_requests limit: 1000")
        assert "quota" in p.last_quota_detail.lower() or "limit" in p.last_quota_detail.lower()
        assert p.total_429 == 1

    def test_คืนงบช้ากว่าตอนหด(self):
        """ลดเร็ว คืนช้า — รีบคืนจะไปตันซ้ำทันที"""
        p = PressureController()
        assert p.shrink_ratio < 1.0 < p.recover_ratio
        assert (1 - p.shrink_ratio) > (p.recover_ratio - 1)

    def test_ยังไม่ถึงเวลาไม่คืน(self):
        p = PressureController(recover_after=999)
        p.report_429()
        f = p.factor()
        assert p.factor() == f


class TestLanes:
    def test_แยกเลนตามน้ำหนักงาน(self):
        assert lane_of("report-gather") == LANE_HEAVY
        assert lane_of("obsidian") == LANE_LIGHT
        assert lane_of("stats") == "medium"

    def test_งานหนักขอมากงานเบาขอน้อย(self):
        assert want_for("report-gather") > want_for("stats") >= want_for("obsidian")

    def test_งานหนักกินงบไม่หมด_เหลือให้งานเบา(self):
        """คำถามสั้นที่ควรตอบใน 3 วิ ต้องไม่รอคนสร้างรายงานเป็นนาที"""
        b = AgentBudget(total=10, min_per_user=1, max_per_user=10)
        b.touch("heavy")
        got = b.acquire("heavy", want=10, timeout=1, lane=LANE_HEAVY)
        assert got < 10, "งานหนักต้องถูกกันไม่ให้กินงบทั้งหมด"
        b.touch("light")
        assert b.acquire("light", want=1, timeout=1, lane=LANE_LIGHT) == 1


class TestIdleSweep:
    def test_ล้างคนที่หายไปแล้ว(self):
        """ถ้าไม่ล้าง งบต่อคนจะหดลงเรื่อย ๆ จากคนที่ปิดแท็บทิ้งไว้"""
        import src.tools.agent_budget as ab
        old = ab.USER_IDLE_TIMEOUT
        ab.USER_IDLE_TIMEOUT = 0
        try:
            b = _budget(50)
            b.touch("active")
            assert b.active_users() <= 51
            time.sleep(0.05)
            b.touch("active")
            assert b.active_users() == 1
        finally:
            ab.USER_IDLE_TIMEOUT = old

    def test_ไม่ล้างคนที่ยังถือ_slot_อยู่(self):
        import src.tools.agent_budget as ab
        old = ab.USER_IDLE_TIMEOUT
        ab.USER_IDLE_TIMEOUT = 0
        try:
            b = _budget(2)
            b.acquire("u0", want=1, timeout=1)
            time.sleep(0.05)
            assert "u0" in b._seen, "คนที่กำลังทำงานอยู่ห้ามถูกล้าง"
        finally:
            ab.USER_IDLE_TIMEOUT = old


class TestSnapshot:
    def test_รายงานสถานะครบสำหรับติดตาม(self):
        s = _budget(10).snapshot()
        for k in ("active_users", "per_user_limit", "in_use", "total",
                  "effective_total", "pressure_factor", "total_429", "waiting"):
            assert k in s
