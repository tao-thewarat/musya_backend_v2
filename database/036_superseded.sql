-- 036_superseded.sql
-- ทำเครื่องหมายไฟล์ที่ถูกแทนที่ด้วยไฟล์ใหม่กว่า
--
-- ปัญหาที่เจอจริง 2026-08-03: ถาม "ผู้ป่วยความดันควบคุมได้ดี ที่คำชะอี"
-- มี 3 ไฟล์ชื่อตัวชี้วัดแทบเหมือนกัน ให้คำตอบ 20.54% / 6.40% / 54.75%
-- **ต่างกัน 8 เท่า** และไม่มีอะไรบอกว่าไฟล์ไหนคือคำตอบที่ถูก
--
-- ต้นเหตุ: ตาราง HDC เดียวถูกนำเข้าซ้ำหลายครั้ง (30 ตาราง → 71 ไฟล์)
-- จากการทดลองนำเข้าซ้ำ ๆ ระหว่างพัฒนา · แต่ละครั้งได้ file_id ใหม่
-- File Finder จึงเห็นไฟล์เดียวกันหลายเวอร์ชันแล้วเลือกแบบสุ่ม
--
-- ไม่ลบไฟล์ทิ้ง เพราะบทสนทนาเก่าอ้าง file_id เดิมไว้ — แค่ซ่อนจากการค้นหา

ALTER TABLE csv_data_dict ADD COLUMN IF NOT EXISTS superseded_by VARCHAR(32);

COMMENT ON COLUMN csv_data_dict.superseded_by IS
  'file_id ของไฟล์ที่มาแทน — ถ้าไม่ว่าง File Finder ต้องข้ามไฟล์นี้';

CREATE INDEX IF NOT EXISTS idx_csv_data_dict_active
    ON csv_data_dict (file_id) WHERE superseded_by IS NULL;
