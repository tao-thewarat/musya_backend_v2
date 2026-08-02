"""Regression tests for src/agents/question_resolver.py (Memory Agent).

ล็อกไว้ว่าพรอมต์ต้องมีคำแนะนำเรื่อง "ประโยคยืนยัน/เจาะจงขอบเขต" (เช่น
"ผมถามถึง X", "หมายถึง X นะ") อยู่เสมอ — เกิดจากบั๊กที่เจอจริงตอนทดสอบ: ผู้ใช้
พิมพ์ "ผมถามถึงจังหวัดอุบล" หลังจากเพิ่งคุยเรื่อง "อุบัติเหตุจราจร" ค้างอยู่ แต่
Memory Agent กลับขยายคำถามใหม่กว้างขึ้นย้อนกลับไปหาหัวข้อแรกสุดของบทสนทนาแทนที่
จะเกาะประเด็นล่าสุด (อุบัติเหตุ) ไว้

หมายเหตุ: ไม่ได้เทสต์ผลลัพธ์จริงจาก Gemini (ต้องยิง API จริง) — เทสต์นี้ล็อกแค่ว่า
"คำสั่งพรอมต์ที่ป้อนให้โมเดล" มีตัวอย่าง/กติกาที่ถูกต้องอยู่ครบ กัน regression ที่
อาจเกิดจากใครมาแก้พรอมต์แล้วเผลอลบคำแนะนำนี้ทิ้งในอนาคต
"""
from src.agents import question_resolver as qr


class TestPromptTemplate:
    def test_includes_scope_confirmation_guidance(self):
        rendered = qr._PROMPT.format(history="ประวัติทดสอบ", prompt="ผมถามถึงจังหวัดอุบล")
        assert "ยืนยัน/เจาะจงขอบเขต" in rendered
        assert "ผมถามถึง" in rendered

    def test_instructs_to_anchor_on_latest_specific_topic_not_conversation_start(self):
        rendered = qr._PROMPT.format(history="ประวัติทดสอบ", prompt="ผมถามถึงจังหวัดอุบล")
        assert "ล่าสุด" in rendered
        assert "ไม่ใช่หัวข้อแรกสุด" in rendered or "ไม่ใช่กลับไปถาม" in rendered

    def test_prompt_renders_without_crashing_for_various_inputs(self):
        for prompt in ["ผมถามถึงจังหวัดอุบล", "หมายถึงจังหวัดศรีสะเกษนะ", "3. อุบัติเหตุจราจร"]:
            rendered = qr._PROMPT.format(history="ประวัติทดสอบ", prompt=prompt)
            assert prompt in rendered


class TestResolveQuestionGuards:
    def test_short_circuits_without_history(self):
        resolved, changed = qr.resolve_question("คำถามอะไรก็ได้", "", "some-key")
        assert resolved == "คำถามอะไรก็ได้"
        assert changed is False

    def test_short_circuits_without_gemini_key(self):
        resolved, changed = qr.resolve_question("คำถามอะไรก็ได้", "ประวัติเก่า", "")
        assert resolved == "คำถามอะไรก็ได้"
        assert changed is False


class TestScopeNotNarrowedByHistory:
    """ขอบเขตที่คำถามใหม่ระบุเอง ต้องชนะขอบเขตเดิมจากประวัติ

    เจอจริง 2026-08-03 (ผู้ใช้จับได้จากหน้าเว็บ):
      ประวัติ: คุยเรื่อง "อำเภอคำชะอี จ.มุกดาหาร" อยู่หลายเทิร์น
      คำถามใหม่: "ปี 2569 เขต 10 มีผู้ป่วยซึมเศร้าเข้าถึงบริการร้อยละเท่าไร"
      ผล: Memory Agent เติม "อำเภอคำชะอี จังหวัดมุกดาหาร" กลับเข้าไป
          ⇒ ตอบเฉพาะมุกดาหาร ทั้งที่ผู้ใช้ระบุ "เขต 10" ชัดเจน

    เป็นความผิดที่จับยาก เพราะคำตอบดูสมบูรณ์ทุกอย่าง แค่ครอบคลุมพื้นที่ผิด
    จึงกันด้วยโค้ดด้วย ไม่พึ่งพรอมต์อย่างเดียว
    """

    Q = "ปี 2569 เขต 10 มีผู้ป่วยซึมเศร้าเข้าถึงบริการร้อยละเท่าไร"

    def test_จับได้เมื่อประวัติย่อขอบเขตเป็นจังหวัด(self):
        from src.agents.question_resolver import _narrows_scope

        bad = self.Q + " ของอำเภอคำชะอี จังหวัดมุกดาหาร"
        assert _narrows_scope(self.Q, bad) is True

    def test_ไม่จับผิดเมื่อเติมสิ่งที่ไม่ใช่พื้นที่(self):
        from src.agents.question_resolver import _narrows_scope

        ok = self.Q + " โดยแยกตามกลุ่มอายุ"
        assert _narrows_scope(self.Q, ok) is False

    def test_คำถามที่ไม่ได้ระบุทั้งเขตยังเติมพื้นที่ได้ตามปกติ(self):
        """ถามลอย ๆ ว่า 'แล้วปีที่แล้วล่ะ' ยังต้องเติมจังหวัดจากประวัติได้"""
        from src.agents.question_resolver import _narrows_scope

        q = "แล้วปีที่แล้วล่ะ"
        assert _narrows_scope(q, q + " ของจังหวัดมุกดาหาร") is False

    def test_รองรับการเขียน_เขต_หลายแบบ(self):
        from src.agents.question_resolver import _narrows_scope

        for q in ("ข้อมูลเขตสุขภาพที่ 10 ปี 2569",
                  "ทั้ง 5 จังหวัด มีผู้ป่วยเท่าไร",
                  "ทุกจังหวัดในเขต 10"):
            assert _narrows_scope(q, q + " จังหวัดมุกดาหาร") is True, q

    def test_การ์ดทำงานใน_resolve_question(self, monkeypatch):
        """ถึงโมเดลจะตอบผิด โค้ดต้องปัดทิ้งแล้วคืนคำถามเดิม"""
        from src.agents import question_resolver as qr

        class _Msg:
            content = "ปี 2569 เขต 10 ... อำเภอคำชะอี จังหวัดมุกดาหาร"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        monkeypatch.setattr(qr.litellm, "completion", lambda **k: _Resp())
        out, changed = qr.resolve_question(self.Q, "ประวัติ: คำชะอี มุกดาหาร", "key")
        assert out == self.Q and changed is False
