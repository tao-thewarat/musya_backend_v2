-- 034_data_dict_definition.sql
-- เก็บ "นิยามเชิงปฏิบัติการ" ของตัวชี้วัด — ตัวเลขนี้นับใครบ้าง
--
-- ต่างจาก columns_json.desc ที่บอกแค่ว่าคอลัมน์ไหนคืออะไร ("จำนวนผู้ป่วย (B1)")
-- ฟิลด์นี้เก็บนิยามจริงจากหน้า HDC ที่บอกรหัสโรค ICD ที่รวม/ตัดออก รหัส LAB
-- ที่ต้องมี และเกณฑ์ตัดค่า เช่น "UPCR > 150 mg/g หรือ eGFR < 60"
--
-- ทำไมสำคัญ: คำถามแรกของคนทำนโยบายคือ "ตัวเลขนี้นับใคร" — ถ้าไม่มีข้อมูลนี้
-- AI ตอบได้แค่ตัวเลข แต่ตอบคำถามนั้นไม่ได้ และไม่รู้ตัวว่าตอบไม่ได้

ALTER TABLE csv_data_dict ADD COLUMN IF NOT EXISTS definition TEXT;
ALTER TABLE csv_data_dict ADD COLUMN IF NOT EXISTS numerator_th TEXT;
ALTER TABLE csv_data_dict ADD COLUMN IF NOT EXISTS denominator_th TEXT;

COMMENT ON COLUMN csv_data_dict.definition IS
  'หมายเหตุ/นิยามเชิงปฏิบัติการเต็ม จากหน้า standard-report-detail ของ HDC';
COMMENT ON COLUMN csv_data_dict.numerator_th IS 'นิยามตัวตั้ง (A) จาก HDC';
COMMENT ON COLUMN csv_data_dict.denominator_th IS 'นิยามตัวหาร (B) จาก HDC';
