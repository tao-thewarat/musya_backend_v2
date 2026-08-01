"""ตัวเลือกผู้ให้บริการ LLM สำหรับโหมด "ไม่เลือกเครื่องมือ" (AI ทั่วไป)

ทำไมต้องมีไฟล์นี้: เดิมทุก agent ฮาร์ดโค้ด `LLM(model="gemini/...")` กระจายอยู่ 8 จุด
ผู้ใช้เลยเลือกค่ายอื่นไม่ได้เลย ไฟล์นี้รวมการสร้าง LLM ของ "โหมดคุยทั่วไป" ไว้ที่เดียว
ส่วน pipeline เฉพาะทาง (CSV/อุบัติเหตุ/ThaiJo) ยังใช้ Gemini ตามเดิม เพราะพรอมป์
และ tool contract ถูกจูนกับ Gemini ไว้แล้ว การสลับค่ายตรงนั้นเสี่ยงพังโดยไม่จำเป็น

รุ่นที่เลือกเป็น "รุ่นคุ้มค่า" ของแต่ละค่าย ไม่ใช่รุ่นท็อป — ตรวจจาก API จริงของแต่ละ
ค่ายเมื่อ 2026-07-30 ไม่ได้เดาชื่อรุ่น และเปลี่ยนได้ทาง env ทุกตัวโดยไม่ต้องแก้โค้ด
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from crewai import LLM


@dataclass(frozen=True)
class Provider:
    key: str            # รหัสที่ frontend ส่งมา
    name_th: str        # ชื่อที่แสดงบนปุ่ม
    env_key: str        # ชื่อ env ของ API key
    env_model: str      # ชื่อ env ที่ override รุ่นได้
    default_model: str  # รุ่นคุ้มค่าที่ยืนยันว่ามีจริงจาก API
    litellm_prefix: str # prefix ที่ LiteLLM ใช้แยกค่าย


# ⚠️ ห้ามใส่รุ่นที่ยังไม่ยืนยันว่ามีจริง — เคยเสียเวลาไล่ debug 404 จากชื่อรุ่นที่เดาเอา
PROVIDERS: dict[str, Provider] = {
    "gemini": Provider(
        key="gemini",
        name_th="Gemini",
        env_key="GEMINI_API_KEY",
        env_model="CHAT_MODEL_GEMINI",
        default_model="gemini-3.6-flash",
        litellm_prefix="gemini",
    ),
    "chatgpt": Provider(
        key="chatgpt",
        name_th="ChatGPT",
        env_key="OPENAI_API_KEY",
        env_model="CHAT_MODEL_OPENAI",
        default_model="gpt-5.4-mini",
        litellm_prefix="openai",
    ),
    "claude": Provider(
        key="claude",
        name_th="Claude",
        env_key="ANTHROPIC_API_KEY",
        env_model="CHAT_MODEL_ANTHROPIC",
        default_model="claude-haiku-4-5-20251001",
        litellm_prefix="anthropic",
    ),
}

DEFAULT_PROVIDER = "gemini"


def resolve_provider(key: str | None) -> Provider:
    """แปลงรหัสที่ผู้ใช้ส่งมาเป็น Provider — ค่าที่ไม่รู้จักตกกลับเป็น default

    ไม่ raise เพราะ provider ที่พิมพ์ผิดไม่ควรทำให้คำถามของผู้ใช้ล้มทั้งคำถาม
    """
    return PROVIDERS.get((key or "").strip().lower(), PROVIDERS[DEFAULT_PROVIDER])


def _db_row(provider_key: str) -> dict:
    """ค่าที่ super admin ตั้งไว้ใน DB — ล้มเหลวแล้วคืน {} เพื่อตกกลับไปใช้ env

    ห้ามให้ DB ล่มลากโหมดคุยทั่วไปล่มตามไปด้วย ค่าใน env ยังใช้งานได้อยู่
    """
    try:
        from src.db.pool import query_db
        rows = query_db(
            "SELECT api_key, model, enabled FROM llm_settings WHERE provider = %s",
            (provider_key,),
        )
        return rows[0] if rows else {}
    except Exception:
        return {}


def api_key_of(p: Provider) -> str:
    """ลำดับความสำคัญ: ค่าที่ admin ตั้งใน DB > env > ว่าง"""
    return ((_db_row(p.key).get("api_key") or "").strip()
            or (os.getenv(p.env_key) or "").strip())


def has_key(p: Provider) -> bool:
    return bool(api_key_of(p))


def is_enabled(p: Provider) -> bool:
    """admin ปิดค่ายไหนไว้หรือเปล่า — แถวที่ยังไม่มีใน DB ถือว่าเปิด"""
    row = _db_row(p.key)
    return bool(row.get("enabled", True))


def model_of(p: Provider) -> str:
    """ชื่อรุ่นแบบเต็มที่ LiteLLM เข้าใจ — DB > env > default ในโค้ด"""
    model = ((_db_row(p.key).get("model") or "").strip()
             or (os.getenv(p.env_model) or "").strip()
             or p.default_model)
    return f"{p.litellm_prefix}/{model}"


def available_providers() -> list[dict]:
    """รายการ provider สำหรับให้ frontend วาดปุ่ม พร้อมบอกว่าตัวไหนพร้อมใช้

    ส่ง provider ที่ยังไม่มี key มาด้วย (available=False) เพื่อให้ผู้ใช้เห็นว่ามีตัวเลือกนี้
    อยู่แต่ต้องตั้ง key ก่อน — ดีกว่าซ่อนแล้วผู้ใช้ไม่รู้ว่าเลือกได้
    """
    out = []
    for p in PROVIDERS.values():
        keyed, on = has_key(p), is_enabled(p)
        out.append({
            "key": p.key,
            "nameTh": p.name_th,
            "model": model_of(p).split("/", 1)[1],
            "available": keyed and on,
            "reason": ("" if keyed and on
                       else "ยังไม่ได้ตั้ง API key" if not keyed
                       else "ผู้ดูแลระบบปิดใช้งานไว้"),
            "envKey": p.env_key,
        })
    return out


class ProviderUnavailable(RuntimeError):
    """ผู้ใช้เลือก provider ที่ยังใช้ไม่ได้ — ต้องแจ้งเหตุผลให้ตรง

    แยก "ไม่มี key" กับ "ถูกปิดไว้" ออกจากกัน เพราะทางแก้ต่างกันคนละเรื่อง
    """

    def __init__(self, p: Provider, reason: str, kind: str = "other"):
        self.provider, self.reason, self.kind = p, reason, kind
        super().__init__(f"ใช้ {p.name_th} ไม่ได้: {reason}")

    @property
    def short_message(self) -> str:
        """ข้อความสั้นสำหรับโชว์ในแชท — เคสไม่มี key บอกแค่ว่าไม่เจอ key"""
        if self.kind == "no_key":
            return f"ไม่พบ API key ของ {self.provider.name_th} — กรุณาเลือกค่ายอื่น"
        return f"ใช้ {self.provider.name_th} ไม่ได้: {self.reason}"


def build_chat_llm(
    provider_key: str | None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> tuple[LLM, Provider]:
    """สร้าง LLM ของโหมดคุยทั่วไปตาม provider ที่ผู้ใช้เลือก

    Raises:
        ProviderUnavailable: เลือกค่ายที่ยังใช้ไม่ได้ — ผู้เรียกต้องแจ้งผู้ใช้ตรง ๆ
            ไม่ใช่เงียบ ๆ สลับไปค่ายอื่น เพราะผู้ใช้จะไม่รู้ว่าได้คำตอบจากใคร
    """
    p = resolve_provider(provider_key)
    if not has_key(p):
        raise ProviderUnavailable(p, f"ยังไม่ได้ตั้ง API key ({p.env_key})", kind="no_key")
    if not is_enabled(p):
        raise ProviderUnavailable(p, "ผู้ดูแลระบบปิดใช้งานค่ายนี้ไว้", kind="disabled")
    return (
        LLM(
            model=model_of(p),
            api_key=api_key_of(p),
            temperature=temperature,
            max_tokens=max_tokens,
        ),
        p,
    )


def friendly_llm_error(p: Provider, exc: Exception) -> str:
    """แปลง error ดิบของแต่ละค่ายเป็นข้อความที่ผู้ใช้อ่านรู้เรื่องและรู้ว่าต้องทำอะไร

    เจอจริง: OpenAI key ที่ list models ได้ปกติ แต่ยิง chat แล้วได้ 429
    insufficient_quota — ผู้ใช้เห็น stack trace แล้วไม่รู้ว่าต้องไปเติมเครดิต
    """
    msg = str(exc)
    low = msg.lower()
    if "insufficient_quota" in low or "exceeded your current quota" in low:
        return (f"## ใช้ {p.name_th} ไม่ได้ชั่วคราว\n\n"
                f"บัญชี API ของ {p.name_th} **เครดิตหมดหรือยังไม่ได้ผูกการชำระเงิน**\n\n"
                f"**ทางแก้**\n"
                f"- เลือกค่ายอื่นในตัวเลือกโมเดล แล้วถามคำถามเดิมอีกครั้ง\n"
                f"- หรือแจ้งผู้ดูแลระบบให้เติมเครดิตของ {p.name_th}")
    if "rate limit" in low or "429" in low:
        return (f"## {p.name_th} กำลังถูกเรียกถี่เกินไป\n\n"
                f"กรุณารอสักครู่แล้วลองอีกครั้ง หรือเลือกค่ายอื่น")
    if any(t in low for t in ("invalid_api_key", "unauthorized", "401", "api key not valid")):
        return (f"## API key ของ {p.name_th} ใช้ไม่ได้\n\n"
                f"กรุณาแจ้งผู้ดูแลระบบให้ตรวจสอบค่า `{p.env_key}` อีกครั้ง")
    if any(t in low for t in ("not found", "404", "does not exist")):
        return (f"## ไม่พบรุ่นที่ตั้งไว้ของ {p.name_th}\n\n"
                f"รุ่น `{model_of(p).split('/', 1)[1]}` อาจถูกยกเลิกหรือพิมพ์ผิด "
                f"กรุณาแจ้งผู้ดูแลระบบให้แก้ในหน้าตั้งค่า")
    return (f"## เกิดข้อผิดพลาดกับ {p.name_th}\n\n"
            f"กรุณาลองอีกครั้ง หรือเลือกค่ายอื่น\n\n"
            f"รายละเอียดทางเทคนิค: `{msg[:200]}`")
