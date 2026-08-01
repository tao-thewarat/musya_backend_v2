"""Regression tests: คิว ingest หลายไฟล์ (_run_batch)

ประเด็นที่ต้องคุม:
  • ไฟล์เดียวพัง ต้องไม่ล้มทั้งคิว — ที่เหลือต้องเดินต่อ
  • ต้องรัน "ทีละไฟล์เรียงลำดับ" ไม่ขนาน (ขนานแล้วชน 429 quota ของ Gemini)
  • ขอยกเลิกกลางคันได้ ไฟล์ที่เหลือต้องถูกข้าม ไม่ใช่ทำต่อจนหมด
"""
import pytest

from src.routers import pdf_ingest
from src.routers.pdf_ingest import _run_batch


def _batch(n: int) -> str:
    bid = f"batch-test-{n}"
    pdf_ingest._batches[bid] = {
        "batch_id": bid,
        "status": "queued",
        "items": [
            {
                "file_id": f"f{i}", "original_name": f"{i}.pdf",
                "province": None, "district": None, "folder_name": None,
                "job_id": f"job-{bid}-{i}", "status": "pending",
                "error": None, "result": None,
            }
            for i in range(n)
        ],
        "created_at": 0.0,
        "cancel_requested": False,
    }
    return bid


@pytest.fixture(autouse=True)
def _clean():
    pdf_ingest._batches.clear()
    pdf_ingest._jobs.clear()
    yield
    pdf_ingest._batches.clear()
    pdf_ingest._jobs.clear()


class TestRunBatch:
    def test_ทำครบทุกไฟล์และเรียงตามลำดับ(self, monkeypatch):
        order = []

        def fake(job_id, file_id, name, prov, dist, folder):
            order.append(file_id)
            pdf_ingest._jobs[job_id]["status"] = "completed"
            pdf_ingest._jobs[job_id]["result"] = {"ok": True}

        monkeypatch.setattr(pdf_ingest, "_do_ingest", fake)
        bid = _batch(3)
        _run_batch(bid)

        b = pdf_ingest._batches[bid]
        assert order == ["f0", "f1", "f2"], "ต้องทำเรียงลำดับ ไม่สลับ"
        assert b["status"] == "completed"
        assert all(i["status"] == "completed" for i in b["items"])

    def test_ไฟล์เดียวพังต้องไม่ล้มทั้งคิว(self, monkeypatch):
        def fake(job_id, file_id, name, prov, dist, folder):
            if file_id == "f1":
                raise RuntimeError("PDF เสีย")
            pdf_ingest._jobs[job_id]["status"] = "completed"

        monkeypatch.setattr(pdf_ingest, "_do_ingest", fake)
        bid = _batch(3)
        _run_batch(bid)

        b = pdf_ingest._batches[bid]
        st = [i["status"] for i in b["items"]]
        assert st == ["completed", "error", "completed"]
        assert b["status"] == "partial", "มีไฟล์พัง → คิวต้องเป็น partial ไม่ใช่ completed"
        assert "PDF เสีย" in (b["items"][1]["error"] or "")

    def test_ยกเลิกกลางคันต้องข้ามที่เหลือ(self, monkeypatch):
        def fake(job_id, file_id, name, prov, dist, folder):
            pdf_ingest._jobs[job_id]["status"] = "completed"
            if file_id == "f0":
                pdf_ingest._batches[bid]["cancel_requested"] = True

        monkeypatch.setattr(pdf_ingest, "_do_ingest", fake)
        bid = _batch(3)
        _run_batch(bid)

        st = [i["status"] for i in pdf_ingest._batches[bid]["items"]]
        assert st == ["completed", "cancelled", "cancelled"]

    def test_สถานะ_error_จาก_job_ถูกส่งต่อ(self, monkeypatch):
        """_do_ingest จับ exception เองแล้วตั้ง status=error — คิวต้องอ่านค่านั้นด้วย"""
        def fake(job_id, file_id, name, prov, dist, folder):
            pdf_ingest._jobs[job_id]["status"] = "error"
            pdf_ingest._jobs[job_id]["error"] = "ingest ล้มเหลว"

        monkeypatch.setattr(pdf_ingest, "_do_ingest", fake)
        bid = _batch(2)
        _run_batch(bid)

        b = pdf_ingest._batches[bid]
        assert all(i["status"] == "error" for i in b["items"])
        assert b["status"] == "partial"


class TestSharedState:
    """uvicorn รัน --workers 4 ⇒ POST กับ GET อาจคนละ process
    สถานะต้องอ่านข้าม worker ได้ผ่าน Redis ไม่ใช่ 404
    """

    def test_อ่าน_batch_ข้าม_worker_ได้(self, monkeypatch):
        shared: dict[str, str] = {}
        monkeypatch.setattr(pdf_ingest, "_redis", lambda: _FakeRedis(shared))

        bid = _batch(1)
        pdf_ingest._publish("batch", bid, pdf_ingest._batches[bid])
        pdf_ingest._batches.clear()          # จำลอง worker อื่นที่ไม่มี dict ก้อนนี้

        got = pdf_ingest._get_batch(bid)
        assert got is not None, "worker อื่นต้องยังอ่านสถานะคิวได้"
        assert got["batch_id"] == bid

    def test_ยกเลิกจาก_worker_อื่นถึงตัวที่ถือคิว(self, monkeypatch):
        shared: dict[str, str] = {}
        monkeypatch.setattr(pdf_ingest, "_redis", lambda: _FakeRedis(shared))

        bid = _batch(3)
        batch = pdf_ingest._batches[bid]
        pdf_ingest._publish("batch", bid, batch)

        # worker ที่รับคำสั่งยกเลิก "ไม่มี" batch ใน dict ตัวเอง
        holder, pdf_ingest._batches = pdf_ingest._batches, {}
        pdf_ingest._request_cancel(bid)
        pdf_ingest._batches = holder

        assert pdf_ingest._cancel_requested(batch) is True

    def test_redis_ล่มต้องไม่ทำให้_ingest_พัง(self, monkeypatch):
        monkeypatch.setattr(pdf_ingest, "_redis", lambda: None)
        bid = _batch(1)
        pdf_ingest._publish("batch", bid, pdf_ingest._batches[bid])   # ต้องไม่ raise
        assert pdf_ingest._get_batch(bid) is not None                 # ยังอ่านจาก dict ได้
        assert pdf_ingest._cancel_requested(pdf_ingest._batches[bid]) is False


class _FakeRedis:
    """Redis จำลองแบบ key-value พอสำหรับ setex/get"""

    def __init__(self, store: dict):
        self._s = store

    def setex(self, key, _ttl, value):
        self._s[key] = value

    def get(self, key):
        return self._s.get(key)
