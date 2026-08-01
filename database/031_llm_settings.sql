-- 031_llm_settings.sql
-- ตั้งค่า LLM ของโหมด "คุยทั่วไป" จากหน้าเว็บได้ โดยไม่ต้องแก้ .env แล้ว rebuild image
--
-- ทำไมต้องเก็บใน DB: เดิม API key กับชื่อรุ่นอยู่ใน .env ของ container การเปลี่ยนรุ่น
-- หรือเติม key ใหม่ต้องแก้ไฟล์ + rebuild + restart ซึ่งผู้ดูแลระบบที่ไม่ใช่ dev ทำไม่ได้
-- ตารางนี้ให้ super admin ตั้งค่าจาก UI ได้ทันที ส่วน .env ยังใช้เป็นค่าเริ่มต้นสำรอง
-- (ลำดับความสำคัญ: DB > env > default ในโค้ด)

CREATE TABLE IF NOT EXISTS llm_settings (
    provider     VARCHAR(32) PRIMARY KEY,   -- gemini | chatgpt | claude
    api_key      TEXT,                      -- NULL = ใช้ค่าจาก .env ตามเดิม
    model        VARCHAR(128),              -- NULL = ใช้ default ในโค้ด
    enabled      BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_by   VARCHAR(255),              -- อีเมลผู้แก้ล่าสุด (audit)
    updated_at   TIMESTAMP   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  llm_settings IS 'ตั้งค่า provider ของโหมดคุยทั่วไป — แก้ได้จากหน้า super admin';
COMMENT ON COLUMN llm_settings.api_key IS 'ว่าง = ตกกลับไปใช้ env เดิม เพื่อไม่ให้ระบบล่มถ้า admin ล้างค่าโดยไม่ตั้งใจ';
COMMENT ON COLUMN llm_settings.enabled IS 'ปิดได้เป็นรายค่าย เช่นเครดิตหมด ไม่ต้องลบ key ทิ้ง';

-- เตรียมแถวของทั้ง 3 ค่ายไว้เลย ให้หน้า admin มีของให้แก้ตั้งแต่เปิดครั้งแรก
INSERT INTO llm_settings (provider, enabled) VALUES
    ('gemini',  TRUE),
    ('chatgpt', TRUE),
    ('claude',  TRUE)
ON CONFLICT (provider) DO NOTHING;

-- ── บทบาทผู้ดูแลสูงสุด ───────────────────────────────────────────────────────
-- ⚠️ ตารางที่ใช้ล็อกอินจริงคือ `accounts` (53 บัญชี) ไม่ใช่ `users` (1 แถว ไม่มีใครเรียก)
-- ดู app/api/auth/login/route.ts ที่ query `FROM accounts`
--
-- คำที่ระบบใช้อยู่แล้วคือ 'adminsuper' (มีบัญชี supermusya@gmail.com ถืออยู่)
-- จงใจ **ไม่เพิ่ม CHECK constraint** ให้ accounts — ตารางนี้มีข้อมูลจริง 53 แถว
-- การผูก constraint ย้อนหลังเสี่ยงล้มถ้ามีค่าที่ไม่ได้เผื่อไว้ และไม่ได้แก้ปัญหาอะไร
-- เพราะโค้ดตรวจสิทธิ์ที่ llm_config.py::_require_superadmin อยู่แล้ว
CREATE INDEX IF NOT EXISTS idx_accounts_role ON accounts (role);
