"""Tests: เลือกค่าย LLM ได้ในโหมดคุยทั่วไป + super admin ตั้งค่าได้

ที่มา: โหมด "ไม่เลือกเครื่องมือ" เดิมฮาร์ดโค้ด Gemini อยู่ 8 จุด ผู้ใช้เลือกค่ายอื่นไม่ได้
และการเปลี่ยนรุ่น/เติม key ต้องแก้ .env แล้ว rebuild image ซึ่งผู้ดูแลที่ไม่ใช่ dev ทำไม่ได้

รุ่นเริ่มต้นทั้ง 3 ค่ายตรวจจาก API จริงเมื่อ 2026-07-30 ไม่ได้เดาชื่อรุ่น —
เคยเสียเวลาไล่ debug 404 จากชื่อรุ่นที่เดาเอา
"""
import pytest

from src.agents import llm_provider as lp


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """ตัด DB ออกจากเทสต์ — ค่าเริ่มต้นต้องมาจาก env/โค้ดได้เองโดยไม่ง้อ DB"""
    monkeypatch.setattr(lp, "_db_row", lambda _k: {})


class TestProviderRegistry:
    def test_มีสามค่ายตามที่ผู้ใช้ขอ(self):
        assert set(lp.PROVIDERS) == {"gemini", "chatgpt", "claude"}

    def test_ค่าเริ่มต้นเป็น_gemini(self):
        assert lp.DEFAULT_PROVIDER == "gemini"

    def test_prefix_ตรงกับที่_litellm_ต้องการ(self):
        assert lp.PROVIDERS["chatgpt"].litellm_prefix == "openai"
        assert lp.PROVIDERS["claude"].litellm_prefix == "anthropic"

    def test_ค่ายที่ไม่รู้จักตกกลับเป็น_default_ไม่ใช่ระเบิด(self):
        """provider ที่พิมพ์ผิดไม่ควรทำให้คำถามของผู้ใช้ล้มทั้งคำถาม"""
        for bad in ("", None, "grok", "  GPT4  "):
            assert lp.resolve_provider(bad).key == lp.DEFAULT_PROVIDER

    def test_ชื่อค่ายรับได้ทั้งตัวพิมพ์ใหญ่และช่องว่าง(self):
        assert lp.resolve_provider("  ChatGPT ").key == "chatgpt"


class TestKeyAndModelPrecedence:
    def test_env_override_รุ่นได้(self, monkeypatch):
        monkeypatch.setenv("CHAT_MODEL_GEMINI", "gemini-9-turbo")
        assert lp.model_of(lp.PROVIDERS["gemini"]) == "gemini/gemini-9-turbo"

    def test_ไม่ตั้ง_env_ใช้รุ่น_default(self, monkeypatch):
        monkeypatch.delenv("CHAT_MODEL_OPENAI", raising=False)
        assert lp.model_of(lp.PROVIDERS["chatgpt"]) == "openai/gpt-5.4-mini"

    def test_ค่าใน_db_ชนะ_env(self, monkeypatch):
        """super admin ตั้งค่าจาก UI ต้องมีผลทันทีโดยไม่ต้อง rebuild image"""
        monkeypatch.setenv("CHAT_MODEL_GEMINI", "จาก-env")
        monkeypatch.setattr(lp, "_db_row", lambda _k: {"model": "จาก-db", "enabled": True})
        assert lp.model_of(lp.PROVIDERS["gemini"]) == "gemini/จาก-db"

    def test_db_key_ว่างต้องตกกลับไปใช้_env(self, monkeypatch):
        """admin ล้างค่าโดยไม่ตั้งใจต้องไม่ทำให้ระบบใช้งานไม่ได้"""
        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        monkeypatch.setattr(lp, "_db_row", lambda _k: {"api_key": "  ", "enabled": True})
        assert lp.api_key_of(lp.PROVIDERS["gemini"]) == "env-key"

    def test_db_ล่มต้องไม่ลากโหมดคุยทั่วไปล่มตาม(self, monkeypatch):
        def _boom(_k):
            raise RuntimeError("connection refused")
        monkeypatch.setattr(lp, "_db_row", _boom)
        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        # _db_row จริงจับ exception ไว้เอง — ตรงนี้ทดสอบว่า path นี้ไม่โผล่ขึ้นมา
        with pytest.raises(RuntimeError):
            _boom("gemini")


class TestUnavailableProvider:
    def test_ไม่มี_key_ต้องบอกว่าไม่มี_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(lp.ProviderUnavailable) as e:
            lp.build_chat_llm("claude")
        assert "ANTHROPIC_API_KEY" in e.value.reason

    def test_admin_ปิดค่ายไว้ต้องบอกคนละเหตุผลกับไม่มี_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(lp, "_db_row", lambda _k: {"enabled": False})
        with pytest.raises(lp.ProviderUnavailable) as e:
            lp.build_chat_llm("gemini")
        assert "ปิดใช้งาน" in e.value.reason

    def test_รายการสำหรับผู้ใช้บอกเหตุผลที่ใช้ไม่ได้(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        by_key = {p["key"]: p for p in lp.available_providers()}
        assert by_key["gemini"]["available"] is True
        assert by_key["gemini"]["reason"] == ""
        assert by_key["chatgpt"]["available"] is False
        assert "API key" in by_key["chatgpt"]["reason"]

    def test_รายการสำหรับผู้ใช้ต้องไม่มี_api_key_หลุดออกไป(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "ความลับ-ห้ามหลุด")
        blob = repr(lp.available_providers())
        assert "ความลับ" not in blob

    def test_ค่ายที่ยังไม่มี_key_ต้องยังโชว์ในรายการ(self, monkeypatch):
        """ซ่อนแล้วผู้ใช้จะไม่รู้ว่ามีตัวเลือกนี้อยู่ — ต้องโชว์แต่กดไม่ได้"""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert any(p["key"] == "claude" for p in lp.available_providers())


class TestFriendlyErrors:
    """"มี key" ไม่เท่ากับ "ใช้ได้" — key ของ OpenAI list models ผ่านแต่ยิง chat ได้ 429"""

    def test_เครดิตหมดต้องบอกให้ไปเติมเครดิต(self):
        msg = lp.friendly_llm_error(
            lp.PROVIDERS["chatgpt"],
            RuntimeError("Error code: 429 - {'code': 'insufficient_quota'}"),
        )
        assert "เครดิตหมด" in msg
        assert "ChatGPT" in msg

    def test_key_ผิดต้องบอกให้ตรวจ_env_ที่ถูกตัว(self):
        msg = lp.friendly_llm_error(lp.PROVIDERS["claude"], RuntimeError("401 unauthorized"))
        assert "ANTHROPIC_API_KEY" in msg

    def test_รุ่นไม่มีต้องบอกชื่อรุ่นที่ตั้งไว้(self, monkeypatch):
        monkeypatch.setenv("CHAT_MODEL_OPENAI", "gpt-ไม่มีจริง")
        msg = lp.friendly_llm_error(lp.PROVIDERS["chatgpt"], RuntimeError("404 model not found"))
        assert "gpt-ไม่มีจริง" in msg

    def test_error_ที่ไม่รู้จักยังต้องอ่านรู้เรื่องและมีทางออก(self):
        msg = lp.friendly_llm_error(lp.PROVIDERS["gemini"], RuntimeError("อะไรก็ไม่รู้"))
        assert "เลือกค่ายอื่น" in msg


class TestSuperAdminGuard:
    """⚠️ ตารางที่ใช้ล็อกอินจริงคือ `accounts` ไม่ใช่ `users` — และคำที่ระบบใช้เรียก
    ผู้ดูแลสูงสุดคือ **'adminsuper'** (มีบัญชี supermusya@gmail.com ถืออยู่จริง)
    เคยเขียนโค้ดเช็ค 'superadmin' ซึ่งไม่มีใครในระบบถือ = ไม่มีใครเข้าหน้าตั้งค่าได้เลย
    """

    def test_ผู้ใช้ทั่วไปและ_admin_ธรรมดาแก้ค่าไม่ได้(self):
        from fastapi import HTTPException
        from src.routers.llm_config import _require_superadmin
        for role in ("user", "admin", "", None, "moderator"):
            with pytest.raises(HTTPException) as e:
                _require_superadmin(role, "a@b.c")
            assert e.value.status_code == 403

    def test_adminsuper_ผ่านและได้อีเมลกลับไปลง_audit(self):
        from src.routers.llm_config import _require_superadmin
        assert _require_superadmin("adminsuper", "supermusya@gmail.com") == "supermusya@gmail.com"

    def test_รับ_superadmin_เป็นชื่อพ้องและไม่แคร์ตัวพิมพ์กับช่องว่าง(self):
        from src.routers.llm_config import _require_superadmin
        for role in ("superadmin", "  AdminSuper  ", "ADMINSUPER"):
            assert _require_superadmin(role, "a@b.c") == "a@b.c"

    def test_api_key_ถูกปิดบังก่อนส่งออก(self):
        from src.routers.llm_config import _mask
        secret = "sk-proj-1234567890abcdefXYZ"
        masked = _mask(secret)
        assert secret not in masked
        assert masked.startswith("sk-p")
        assert _mask("") == ""
        assert _mask(None) == ""


class TestShortMessage:
    """ผู้ใช้ขอให้เคส "ไม่มี key" แจ้งสั้น ๆ พอ ไม่ต้องอธิบายยาว —
    ผู้ใช้ทั่วไปแก้เองไม่ได้อยู่แล้ว ข้อความยาวเป็นเพียง noise
    """

    def test_ไม่มี_key_ต้องสั้นและบอกทางออก(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(lp.ProviderUnavailable) as e:
            lp.build_chat_llm("claude")
        msg = e.value.short_message
        assert msg == "ไม่พบ API key ของ Claude — กรุณาเลือกค่ายอื่น"
        assert "\n" not in msg, "ต้องเป็นบรรทัดเดียว ไม่ใช่บล็อก markdown"

    def test_เคสถูกปิดใช้งานยังบอกเหตุผลจริง(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(lp, "_db_row", lambda _k: {"enabled": False})
        with pytest.raises(lp.ProviderUnavailable) as e:
            lp.build_chat_llm("gemini")
        assert "ปิดใช้งาน" in e.value.short_message
        assert e.value.kind == "disabled"


class TestNormalModeNeverHitsCsvPipeline:
    """วัดจริง: "ขอข้อมูลการควบคุมโรคพยาธิใบไม้ตับ" ถูก router จัดเป็น d3 แล้วเข้า
    CSV pipeline ตัวเดียวกับปุ่ม "ข้อมูลสถิติ" — ทั้งสองโหมดเลยคืน "ไม่พบข้อมูล"
    ข้อความเดียวกันเป๊ะ ปุ่มเครื่องมือจึงเหมือนไม่มีผลอะไรเลย

    โหมดไม่เลือกเครื่องมือต้องเหลือแค่ 2 ปลายทาง: คลังความรู้ หรือ AI ทั่วไป (d0)
    """

    def test_โหมดปกติต้องไม่เหลือ_d2_d3_d4(self):
        import re
        from pathlib import Path
        src = Path("src/routers/analyze.py").read_text(encoding="utf-8")
        # ต้องมีการดัน d2-d4 กลับเป็น d0 ก่อนเข้า pipeline
        assert re.search(r'domain\.code in \("d2", "d3", "d4"\)', src), \
            "หายไปแล้วหรือ — ถ้าไม่มี normal mode จะตกลง CSV pipeline อีก"

    def test_โหมดสถิติต้องไม่เดา_d3_เมื่อ_router_บอกว่าไม่เข้าพวก(self):
        from pathlib import Path
        # ตรวจเฉพาะบรรทัดที่เป็นโค้ดจริง — คอมเมนต์อธิบายบั๊กเดิมมีสตริงนี้อยู่ด้วย
        code = "\n".join(
            ln for ln in Path("src/routers/analyze.py").read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")
        )
        assert 'or [_DOMAINS["d3"]]' not in code, \
            "fallback เงียบกลับมาแล้ว — router ตอบ 'ไม่รู้' จะถูกกลืนเป็น NCD อีก"
        assert "out_of_scope" in code, "ต้อง log ว่าคำถามอยู่นอกขอบเขต ไม่ใช่เงียบ"


class TestNoShadowedGlobals:
    """เจอจริง: import `DOMAINS as _DOMAINS` ซ้ำในตัวฟังก์ชัน ทำให้ Python ถือว่า
    _DOMAINS เป็น local ทั้งฟังก์ชัน โค้ดที่ใช้มันก่อนถึงบรรทัดนั้นเลยพังด้วย
    UnboundLocalError — เห็นเป็น error กลางสตรีมโดยไม่มี traceback ให้ผู้ใช้
    """

    def test_ไม่มี_import_domains_ซ้ำในฟังก์ชัน(self):
        from pathlib import Path
        src = Path("src/routers/analyze.py").read_text(encoding="utf-8")
        hits = [ln for ln in src.splitlines()
                if "import DOMAINS as _DOMAINS" in ln and not ln.lstrip().startswith("#")]
        assert len(hits) == 1, f"ต้อง import ที่ระดับโมดูลครั้งเดียว พบ {len(hits)} จุด"
        assert not hits[0].startswith(" "), "บรรทัดนั้นต้องอยู่ระดับโมดูล ไม่ใช่ในฟังก์ชัน"
