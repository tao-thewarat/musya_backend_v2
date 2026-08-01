-- 032_csv_data_dict.sql
-- พจนานุกรมข้อมูลของไฟล์ CSV สถิติ — "metadata คู่กับ CSV เพื่อให้เข้าใจข้อมูลมากขึ้น"
--
-- ทำไมไม่เก็บเป็น sidecar ใน MinIO เหมือน __meta__/__pathdata__ ที่มีอยู่:
-- งานหลักของตารางนี้คือ "ค้นหาข้ามไฟล์" (ถาม BMI แล้วต้องเจอไฟล์รอบเอวด้วย)
-- ถ้าอยู่ใน MinIO ต้องอ่านทีละไฟล์ทั้ง 45 ไฟล์ทุกครั้งที่ค้น — ช้าและ index ไม่ได้
--
-- เก็บส่วนที่ต้อง query เป็นคอลัมน์จริง ส่วนที่อ่านอย่างเดียวเก็บเป็น JSONB

CREATE TABLE IF NOT EXISTS csv_data_dict (
    file_id        VARCHAR(32) PRIMARY KEY,   -- object name ใน MinIO bucket fileapa
    vault_path     TEXT NOT NULL,             -- x-amz-meta-path เต็ม
    file_name      TEXT,
    domain         VARCHAR(8),                -- d2 | d3 | d4 (จากโฟลเดอร์ราก)
    indicator_th   TEXT,                      -- ชื่อตัวชี้วัด = ชื่อโฟลเดอร์ชั้นสุดท้าย

    -- ── ขอบเขตจริงที่อ่านจากไฟล์ ไม่ใช่เดาจากชื่อ ──────────────────────────
    -- เจอจริง: 486950 ชื่อไฟล์เขียน "2569-2569" แต่ข้างในมีตั้งแต่ 2565
    year_min       VARCHAR(4),
    year_max       VARCHAR(4),
    years          TEXT[],
    provinces      TEXT[],
    granularity    VARCHAR(16),               -- จังหวัด | อำเภอ | หน่วยบริการ | ไม่มีมิติพื้นที่
    row_count      INTEGER,
    col_count      INTEGER,

    -- ── แกนเชื่อมข้อมูล (จาก _detect_geo_keys / _detect_year_keys) ─────────
    key_province   TEXT,
    key_district   TEXT,
    key_year       TEXT,

    -- ── คำค้นสำหรับ File Finder ───────────────────────────────────────────
    keywords       TEXT[],

    columns_json   JSONB NOT NULL DEFAULT '[]'::jsonb,   -- [{name, role, unit, desc}]
    caveats        TEXT[] NOT NULL DEFAULT '{}',         -- ข้อควรระวังที่ต้องบอก AI ทุกครั้ง
    counting_basis VARCHAR(16),                          -- typearea | chronicfu | workload

    -- ── ความน่าเชื่อถือ ───────────────────────────────────────────────────
    -- 'auto' = เครื่องสรุปเอง · 'reviewed' = มีคนตรวจแล้ว
    -- ใช้ตัดสินว่าจะปล่อยให้ AI ใช้ไฟล์นี้ตอบคำถามไหม
    confidence     VARCHAR(16) NOT NULL DEFAULT 'auto',
    verified_by    VARCHAR(255),
    unknown_cols   TEXT[] NOT NULL DEFAULT '{}',         -- คอลัมน์ที่ระบุความหมายไม่ได้

    source         VARCHAR(32),                          -- upload | hdc_opendata
    built_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  csv_data_dict IS 'พจนานุกรมข้อมูล CSV สถิติ — ป้อนให้ File Finder / Schema Analyst / Insight Analyst';
COMMENT ON COLUMN csv_data_dict.years IS 'ปีที่พบในเนื้อไฟล์จริง ไม่ใช่ปีในชื่อไฟล์ซึ่งเชื่อไม่ได้';
COMMENT ON COLUMN csv_data_dict.unknown_cols IS 'คอลัมน์อย่าง F3/a_name/result1 ที่ยังไม่มีใครยืนยันความหมาย';
COMMENT ON COLUMN csv_data_dict.caveats IS 'เช่น "เป็น Work Load ผู้ป่วย 1 คนนับได้หลายครั้ง" — ต้องแนบให้ AI เสมอ';

CREATE INDEX IF NOT EXISTS idx_csv_dict_domain   ON csv_data_dict (domain);
CREATE INDEX IF NOT EXISTS idx_csv_dict_keywords ON csv_data_dict USING GIN (keywords);
CREATE INDEX IF NOT EXISTS idx_csv_dict_years    ON csv_data_dict USING GIN (years);
CREATE INDEX IF NOT EXISTS idx_csv_dict_prov     ON csv_data_dict USING GIN (provinces);
