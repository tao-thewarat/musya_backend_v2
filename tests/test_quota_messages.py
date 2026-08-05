"""Tests: ผู้ใช้ต้องรู้ว่า "ทำไมช้า" ไม่ใช่เห็นหน้าจอค้างเงียบ ๆ

เดิมเมื่อชนโควตา Gemini ระบบ sleep แล้ว retry เองเงียบ ๆ ผู้ใช้เห็นแค่ช้าผิดปกติ
โดยไม่รู้สาเหตุ **แล้วมักกดซ้ำ ซึ่งยิ่งซ้ำเติมโควตาที่ตันอยู่แล้ว**
"""
import inspect

from src.tools.agent_budget import AgentBudget, PressureController


class TestPressureListeners:
    def test_แจ้งผู้ที่ลงทะเบียนไว้เมื่อชนโควตา(self):
        p = PressureController()
        seen: list[tuple] = []
        p.add_listener(lambda f, d: seen.append((f, d)))
        p.report_429("429 RESOURCE_EXHAUSTED quota_metric: gen limit: 1000")
        assert len(seen) == 1
        factor, detail = seen[0]
        assert factor < 1.0, "ต้องส่งระดับแรงดันปัจจุบันไปด้วย"
        assert "limit" in detail or "quota" in detail

    def test_ถอนผู้ฟังแล้วต้องไม่ถูกเรียกอีก(self):
        """request ที่จบไปแล้วห้ามถูกเรียก ไม่งั้นจะเขียนลง queue ที่ปิดไปแล้ว"""
        p = PressureController()
        seen: list = []
        fn = lambda f, d: seen.append(1)
        p.add_listener(fn)
        p.remove_listener(fn)
        p.report_429()
        assert seen == []

    def test_ผู้ฟังคนหนึ่งพังต้องไม่กระทบคนอื่น(self):
        p = PressureController()
        ok: list = []

        def boom(f, d):
            raise RuntimeError("พัง")

        p.add_listener(boom)
        p.add_listener(lambda f, d: ok.append(1))
        p.report_429()
        assert ok == [1], "คนที่เหลือต้องยังได้รับแจ้ง"

    def test_หดงบแม้ไม่มีใครฟัง(self):
        p = PressureController()
        p.report_429()
        assert p.factor() < 1.0


class TestAnalyzeEmitsMessages:
    """ตรวจว่าสายท่อจริงส่ง event ออกไปหาผู้ใช้ ไม่ใช่แค่มีฟังก์ชันไว้เฉย ๆ"""

    def _src(self) -> str:
        from src.routers import analyze
        return inspect.getsource(analyze)

    def test_ส่งข้อความตอนรอคิว(self):
        s = self._src()
        assert '"type": "queued"' in s
        assert "กำลังจัดคิว" in s

    def test_ส่งข้อความตอนชนโควตา(self):
        s = self._src()
        assert '"type": "quota_wait"' in s
        assert "ชนโควตา" in s
        assert "อย่าปิดหน้าจอหรือกดซ้ำ" in s, "ต้องบอกไม่ให้กดซ้ำ เพราะยิ่งซ้ำเติมโควตา"

    def test_ส่งข้อความเมื่อได้งบน้อยกว่าที่ขอ(self):
        s = self._src()
        assert '"type": "budget_limited"' in s
        assert "อาจใช้เวลานานกว่าปกติ" in s

    def test_ลงทะเบียนและถอนผู้ฟังเป็นคู่กัน(self):
        """ถ้าลืมถอน จะเขียนลง queue ของ request ที่จบไปแล้ว"""
        s = self._src()
        assert "pressure.add_listener" in s and "pressure.remove_listener" in s

    def test_คืนงบใน_finally(self):
        s = self._src()
        i = s.index("_budget.pressure.remove_listener")
        assert "finally:" in s[max(0, i - 400):i], "ต้องอยู่ใน finally กันงบรั่ว"


class TestBusyMessageHasNumbers:
    def test_บอกจำนวนผู้ใช้จริงไม่ใช่ข้อความลอย(self):
        """เดิมบอกแค่ 'ระบบเต็ม ลองใหม่' ซึ่งไม่ช่วยให้ผู้ใช้ตัดสินใจอะไรได้"""
        from src.routers import analyze
        s = inspect.getsource(analyze)
        assert "มีผู้ใช้งานพร้อมกัน" in s
        assert "active_users" in s

    def test_snapshot_มีข้อมูลพอสำหรับข้อความ(self):
        b = AgentBudget()
        b.touch("u1")
        s = b.snapshot()
        assert s["active_users"] >= 1
        assert "effective_total" in s and "in_use" in s
