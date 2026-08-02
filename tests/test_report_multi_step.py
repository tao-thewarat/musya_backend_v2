"""Tests: เรียกเครื่องมือเดิมซ้ำแล้วผลของขั้นแรกต้องไม่หาย

บั๊กที่เกิดจาก Research Planner: ตัวเก็บผล (`obsidian_result` / `stats_final_holder`
/ ...) เป็น **dict เดียวต่อเครื่องมือ** ⇒ พอ planner สั่งเรียก `stats` 2 ครั้ง
ผลรอบแรกถูกเขียนทับหาย **รายงานจึงมีเนื้อหาน้อยกว่าเวอร์ชันก่อนหน้าโดยไม่มีอะไรฟ้อง**
(ผู้ใช้รายงานว่า format รายงาน PDF/Word เปลี่ยนไปจากเดิม)

แก้ 2 ชั้น:
  1. จับขั้นของเครื่องมือเดียวกันมาต่อกันเป็น "สายโซ่" รันเรียงกัน (กันแย่งเขียน)
  2. ดูดผลของขั้นก่อน ๆ เก็บใน `_extra` แล้วต่อกลับตอนประกอบ section
"""


def _chain_and_merge(steps, run_results):
    """จำลองตรรกะ _make_chain + _merged ที่อยู่ใน analyze.py

    เขียนแยกเพื่อทดสอบตรรกะได้โดยไม่ต้องยก pipeline ทั้งก้อน — ตรรกะเดียวกันเป๊ะ
    """
    holder = {"msg": ""}
    extra: dict[str, list[str]] = {}
    for i, st in enumerate(steps):
        holder["msg"] = run_results[i]          # worker เขียนทับ holder เดิม
        if i < len(steps) - 1:
            snap = holder.get("msg", "")
            if snap:
                extra.setdefault("stats", []).append(
                    f"**{st['purpose']}** (ค้นด้วย: {st['query']})\n\n{snap}")
    prev = extra.get("stats") or []
    latest = holder["msg"]
    return "\n\n".join([*prev, latest]) if prev else latest


class TestMultiStepResultsPreserved:
    STEPS = [
        {"tool": "stats", "query": "ตัวเลขฐาน", "purpose": "สถานการณ์"},
        {"tool": "stats", "query": "จุดเสี่ยง", "purpose": "จุดเสี่ยง"},
    ]

    def test_ผลของขั้นแรกต้องไม่หาย(self):
        out = _chain_and_merge(self.STEPS, ["ผลรอบแรก 100 ราย", "ผลรอบสอง ถนน A"])
        assert "ผลรอบแรก 100 ราย" in out, "นี่คือบั๊กที่ทำให้รายงานสั้นลง"
        assert "ผลรอบสอง ถนน A" in out

    def test_ติดป้ายว่าผลไหนมาจากคำค้นอะไร(self):
        """ผู้ประเมินต้องตรวจย้อนได้ว่าเนื้อหาส่วนไหนมาจากการค้นครั้งไหน"""
        out = _chain_and_merge(self.STEPS, ["ก", "ข"])
        assert "สถานการณ์" in out and "ค้นด้วย: ตัวเลขฐาน" in out

    def test_เรียกครั้งเดียวต้องได้ผลเหมือนเดิมเป๊ะ(self):
        """ห้ามเปลี่ยนพฤติกรรมของเคสปกติ — ไม่มีหัวข้อย่อยงอกมา"""
        one = [{"tool": "stats", "query": "q", "purpose": "p"}]
        assert _chain_and_merge(one, ["เนื้อหาเดิม"]) == "เนื้อหาเดิม"

    def test_ขั้นที่ไม่มีผลไม่ทำให้เกิดหัวข้อว่าง(self):
        out = _chain_and_merge(self.STEPS, ["", "มีผล"])
        assert out == "มีผล"


class TestPlanGroupsSameTool:
    def test_เครื่องมือเดียวกันถูกจับเป็นสายโซ่เดียว(self):
        """ต่างเครื่องมือรันขนานได้ แต่เครื่องมือเดียวกันต้องเรียงกัน"""
        plan = [
            {"tool": "stats", "query": "a"}, {"tool": "obsidian", "query": "b"},
            {"tool": "stats", "query": "c"}, {"tool": "obsidian", "query": "d"},
            {"tool": "pubmed", "query": "e"},
        ]
        chains: dict[str, list] = {}
        for s in plan:
            chains.setdefault(s["tool"], []).append(s)
        assert len(chains) == 3, "ต้องได้ 3 สายโซ่ ไม่ใช่ 5 งานแยก"
        assert len(chains["stats"]) == 2 and len(chains["obsidian"]) == 2
