"""API ตั้งค่า LLM — ผู้ใช้ทั่วไปดูรายการเพื่อเลือกโมเดล, super admin แก้ค่าได้

การแยกสิทธิ์:
  GET  /api/llm/providers        ผู้ใช้ทั่วไป — เห็นชื่อค่าย/รุ่น/พร้อมใช้ไหม **ไม่เห็น key**
  GET  /api/llm/admin/providers  super admin — เห็นค่าที่ตั้งไว้ (key แสดงแบบปิดบัง)
  PUT  /api/llm/admin/providers  super admin — แก้ key/รุ่น/เปิดปิด รายค่าย

⚠️ ห้ามส่ง api_key ตัวเต็มออกจาก backend เด็ดขาด แม้กับ super admin — หน้าเว็บไม่จำเป็น
ต้องรู้ค่าเดิมเพื่อตั้งค่าใหม่ และ response อาจถูก log/แคชที่ไหนก็ได้
"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from src.agents.llm_provider import PROVIDERS, available_providers
from src.db.pool import query_db, execute_db

router = APIRouter(prefix="/api/llm", tags=["llm-config"])


def _mask(key: str | None) -> str:
    """แสดงให้พอรู้ว่าตั้งค่าไว้แล้วและตั้งตัวไหน แต่เอาไปใช้ต่อไม่ได้"""
    k = (key or "").strip()
    if not k:
        return ""
    return f"{k[:4]}…{k[-4:]} ({len(k)} ตัวอักษร)" if len(k) > 12 else f"…({len(k)} ตัวอักษร)"


# คำว่า "ผู้ดูแลสูงสุด" ที่ระบบนี้ใช้จริงคือ 'adminsuper' — ตรวจจากตาราง accounts
# ที่ใช้ล็อกอินจริง (53 บัญชี) ไม่ใช่ตาราง users ที่มีแถวเดียวและไม่มีใครเรียกใช้
# รับ 'superadmin' เป็นชื่อพ้องด้วย เผื่อข้อมูลที่กรอกมาจากที่อื่นสะกดอีกแบบ
_SUPERADMIN_ROLES = {"adminsuper", "superadmin"}


def _require_superadmin(role: str | None, email: str | None) -> str:
    """ด่านสิทธิ์ — role มาจาก header ที่ Next.js ใส่ให้หลังตรวจ session แล้ว

    backend นี้อยู่หลัง internal network และ Next.js เป็นตัวยืนยันตัวตน (ดู
    internalFetch.ts) จึงเชื่อ header ได้เท่าที่ Next.js เชื่อถือได้ — ถ้าวันหน้า
    เปิด backend ตรงสู่อินเทอร์เน็ต ต้องเปลี่ยนมาตรวจ JWT ที่นี่เอง
    """
    if (role or "").strip().lower() not in _SUPERADMIN_ROLES:
        raise HTTPException(status_code=403, detail="ต้องเป็นสิทธิ์ผู้ดูแลสูงสุด (adminsuper) เท่านั้น")
    return (email or "unknown").strip()


@router.get("/providers")
async def list_providers():
    """สำหรับผู้ใช้ทั่วไปเลือกโมเดลที่จะใช้ตอบ"""
    return {"providers": available_providers()}


@router.get("/admin/providers")
async def admin_list_providers(
    x_user_role: str | None = Header(default=None),
    x_user_email: str | None = Header(default=None),
):
    _require_superadmin(x_user_role, x_user_email)
    rows = {r["provider"]: r for r in query_db(
        "SELECT provider, api_key, model, enabled, updated_by, updated_at FROM llm_settings"
    )}
    out = []
    for p in PROVIDERS.values():
        r = rows.get(p.key, {})
        out.append({
            "key": p.key,
            "nameTh": p.name_th,
            "apiKeyMasked": _mask(r.get("api_key")),
            "hasDbKey": bool((r.get("api_key") or "").strip()),
            "model": (r.get("model") or "").strip(),
            "defaultModel": p.default_model,
            "enabled": bool(r.get("enabled", True)),
            "envKey": p.env_key,
            "updatedBy": r.get("updated_by") or "",
            "updatedAt": str(r.get("updated_at") or ""),
        })
    return {"providers": out}


class ProviderUpdate(BaseModel):
    provider: str
    # None = ไม่แตะค่าเดิม, "" = ล้างค่าเพื่อกลับไปใช้ env
    api_key: str | None = None
    model: str | None = None
    enabled: bool | None = None


@router.put("/admin/providers")
async def admin_update_provider(
    body: ProviderUpdate,
    x_user_role: str | None = Header(default=None),
    x_user_email: str | None = Header(default=None),
):
    editor = _require_superadmin(x_user_role, x_user_email)
    if body.provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"ไม่รู้จักค่าย '{body.provider}'")

    # สร้างแถวถ้ายังไม่มี — กันเคสที่ migration ยังไม่ได้รัน
    execute_db(
        "INSERT INTO llm_settings (provider) VALUES (%s) ON CONFLICT (provider) DO NOTHING",
        (body.provider,),
    )

    # อัปเดตเฉพาะฟิลด์ที่ส่งมา — None แปลว่า "ไม่แตะ" ไม่ใช่ "ล้างเป็น NULL"
    sets, params = [], []
    if body.api_key is not None:
        sets.append("api_key = %s")
        params.append(body.api_key.strip() or None)
    if body.model is not None:
        sets.append("model = %s")
        params.append(body.model.strip() or None)
    if body.enabled is not None:
        sets.append("enabled = %s")
        params.append(body.enabled)
    if not sets:
        raise HTTPException(status_code=400, detail="ไม่มีฟิลด์ที่ต้องแก้")

    sets += ["updated_by = %s", "updated_at = NOW()"]
    params += [editor, body.provider]
    execute_db(f"UPDATE llm_settings SET {', '.join(sets)} WHERE provider = %s", tuple(params))
    return {"ok": True, "provider": body.provider, "updatedBy": editor}


@router.post("/admin/providers/test")
async def admin_test_provider(
    body: ProviderUpdate,
    x_user_role: str | None = Header(default=None),
    x_user_email: str | None = Header(default=None),
):
    """ยิงคำถามสั้น ๆ จริงเพื่อพิสูจน์ว่าค่าที่ตั้งไว้ใช้ได้

    จำเป็นเพราะ "มี key" ไม่เท่ากับ "ใช้ได้" — เจอจริงว่า key ของ OpenAI
    list models ผ่านแต่ยิง chat ได้ 429 insufficient_quota
    """
    _require_superadmin(x_user_role, x_user_email)
    from src.agents.llm_provider import (
        build_chat_llm, friendly_llm_error, resolve_provider, ProviderUnavailable,
    )
    p = resolve_provider(body.provider)
    try:
        llm, p = build_chat_llm(body.provider, max_tokens=64)
        reply = str(llm.call("ตอบกลับคำเดียวว่า: พร้อม"))
        return {"ok": True, "provider": p.key, "model": p.default_model, "reply": reply[:120]}
    except ProviderUnavailable as exc:
        return {"ok": False, "provider": p.key, "message": exc.reason}
    except Exception as exc:
        return {"ok": False, "provider": p.key, "message": friendly_llm_error(p, exc)}
