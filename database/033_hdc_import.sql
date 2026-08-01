-- 033_hdc_import.sql
-- ทะเบียนไฟล์ที่นำเข้าจาก MoPH Open Data — ทำให้ "กดรีเฟรชดึงใหม่" ได้
--
-- ต่างจากไฟล์ที่ผู้ใช้อัปโหลดเอง: ไฟล์จาก HDC มีต้นทางที่ดึงซ้ำได้ จึงต้องจำว่า
-- ไฟล์นี้มาจากตารางไหน ดึงปีอะไรไว้ และครั้งล่าสุดได้ date_com เท่าไร

CREATE TABLE IF NOT EXISTS hdc_import (
    file_id       VARCHAR(32) PRIMARY KEY,   -- object ใน MinIO bucket fileapa
    table_name    VARCHAR(64) NOT NULL,      -- s_ckd_stage_typearea
    title_th      TEXT,
    vault_path    TEXT NOT NULL,             -- โฟลเดอร์ปลายทางในคลัง
    report_url    TEXT,                      -- หน้ารายงาน HDC (ไว้ให้คนกดดูนิยาม)

    -- ⚠️ ปีที่ "ดึงได้จริง" ไม่ใช่ปีที่ API ประกาศ — report_year บอก 15 ปี
    -- แต่ 2555–2559 คืน HTTP 400 จึงต้องบันทึกผลจากการดึงจริง
    declared_years TEXT[] NOT NULL DEFAULT '{}',
    usable_years   TEXT[] NOT NULL DEFAULT '{}',

    row_count     INTEGER,
    provinces     TEXT[] NOT NULL DEFAULT '{}',
    -- date_com ต่างกันรายจังหวัด (แต่ละจังหวัดประมวลผลคนละเวลา) จึงเก็บเป็น map
    date_com      JSONB NOT NULL DEFAULT '{}'::jsonb,

    last_sync_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    last_sync_by  VARCHAR(255),
    sync_note     TEXT
);

COMMENT ON TABLE  hdc_import IS 'ไฟล์ที่ดึงจาก MoPH Open Data — กดรีเฟรชดึงใหม่ทับได้';
COMMENT ON COLUMN hdc_import.usable_years IS 'ปีที่ดึงได้ครบทุกจังหวัดจริง (report_year เชื่อไม่ได้)';
COMMENT ON COLUMN hdc_import.date_com IS 'วันที่ประมวลผลของต้นทาง แยกรายจังหวัด — ใช้ตรวจว่ามีของใหม่ไหม';

CREATE INDEX IF NOT EXISTS idx_hdc_import_table ON hdc_import (table_name);
