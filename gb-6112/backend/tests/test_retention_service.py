"""保留期整理的服务层用例（确定性复现并发裁决与断点恢复）。"""
import json
import os

import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select

from src.config.resources import engine
from src.constants.job_state import JobState
from src.models.archives import archives
from src.models.reviews import reviews
from src.schemas.reviews import ReviewInput
from src.services import handover, retention, reviews as review_service
from src.utils.serial import content_digest

from conftest import NOW, get_ledger, get_run, insert_run


def test_unsigned_expired_run_is_cleared_but_record_kept(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=False)
    report = retention.sweep(now=NOW)
    assert report.cleared == [rid]
    with engine.connect() as conn:
        row = get_run(conn, rid)
        assert row["status"] == JobState.CLEARED
        assert row["result"] is None and row["digest"] is None and row["failure"] is None
        # 批次记录仍在
        assert row["id"] == rid and row["case_id"] == 1
        assert get_ledger(conn, rid) is None


def test_signed_expired_run_is_archived_after_verification(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
        original = get_run(conn, rid)
    report = retention.sweep(now=NOW)
    assert report.archived == [rid]
    final_file = os.path.join(archive_dirs["final"], handover.archive_filename(rid))
    assert os.path.exists(final_file)
    assert not os.listdir(archive_dirs["staging"])  # 临时区无残留
    with engine.connect() as conn:
        row = get_run(conn, rid)
        assert row["status"] == JobState.ARCHIVED
        assert row["result"] is None and row["failure"] is None
        assert row["digest"] == original["digest"]  # 校验值随批次记录保留
        ledger = get_ledger(conn, rid)
        assert ledger["stage"] == "verified" and ledger["path"] == final_file
        assert ledger["digest"] == original["digest"]
    with open(final_file, "rb") as handle:
        doc = json.loads(handle.read().decode())
    assert doc["id"] == rid and doc["digest"] == original["digest"]
    assert doc["review"]["actor"] == "reviewer"
    assert content_digest(doc["result"]) == original["digest"]


def test_archived_export_matches_live_export_byte_for_byte(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
    with engine.connect() as conn:
        before_bytes, before_name = handover.build_export(conn, rid)
        before_doc = json.loads(before_bytes.decode())
    retention.sweep(now=NOW)
    with engine.connect() as conn:
        after_bytes, after_name = handover.build_export(conn, rid)
    # 批次号、校验值、签署照旧，文档逐字节一致
    assert before_bytes == after_bytes
    assert before_name == after_name == f"evidence-{rid}.json"
    after_doc = json.loads(after_bytes.decode())
    assert after_doc["id"] == rid
    assert after_doc["digest"] == before_doc["digest"]
    assert after_doc["review"] == before_doc["review"]
    assert after_doc["case_title"] == "测试卷"


def test_cleared_run_cannot_be_signed_or_exported(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=False)
    retention.sweep(now=NOW)
    with engine.begin() as conn:
        with pytest.raises(HTTPException) as exc:
            review_service.sign(conn, rid, ReviewInput(note="尝试补签"), "reviewer")
        assert exc.value.status_code == 409 and "清理" in exc.value.detail
    with engine.connect() as conn:
        with pytest.raises(HTTPException) as exc:
            handover.build_export(conn, rid)
        assert exc.value.status_code == 409


def test_concurrent_sign_wins_then_archived(db, archive_dirs):
    # 清理前一刻签署先落库：处理时复查到签署，转归档而非清理
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=False)
        conn.execute(insert(reviews).values(
            run_id=rid, note="保留期内及时签署", actor="reviewer", created_at=NOW))
    outcome = retention._process_in_transaction(rid, "maintenance")
    assert outcome in ("archived", "recovered")
    with engine.connect() as conn:
        assert get_run(conn, rid)["status"] == JobState.ARCHIVED
        assert get_ledger(conn, rid)["stage"] == "verified"


def test_concurrent_clear_wins_blocks_later_sign(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=False)
    retention.sweep(now=NOW)
    # 清理后复核员再尝试签署：拿到明确的签不了原因
    with engine.begin() as conn:
        with pytest.raises(HTTPException) as exc:
            review_service.sign(conn, rid, ReviewInput(note="迟来的签署"), "reviewer")
        assert exc.value.status_code == 409 and "无法补签" in exc.value.detail


def test_recovery_promotes_staged_copy_without_duplicate_archive(db, archive_dirs, monkeypatch):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
        run = get_run(conn, rid)
    # 模拟中断：staging 已写、台账停在 staged、原件还在
    doc_payload = None
    with engine.begin() as conn:
        joined = handover.load_run(conn, rid)
        review = review_service.get(conn, rid)
        document = handover.document_for_run(joined, review)
        doc_payload = handover.encode_document(document)
    staging = retention._write_staging(rid, doc_payload)
    with engine.begin() as conn:
        conn.execute(insert(archives).values(
            run_id=rid, path=staging, digest=run["digest"], stage="staged",
            actor="maintenance", created_at=NOW, verified_at=None))
        assert get_run(conn, rid)["result"] is not None  # 原件尚未转走
    # 恢复执行
    report = retention.sweep(now=NOW)
    assert rid in report.recovered and rid in report.archived
    final_file = os.path.join(archive_dirs["final"], handover.archive_filename(rid))
    assert os.path.exists(final_file)
    with engine.connect() as conn:
        assert get_ledger(conn, rid)["stage"] == "verified"
        assert get_run(conn, rid)["status"] == JobState.ARCHIVED
    # 再跑一次：同一交接单不重复存档
    retention.sweep(now=NOW)
    with engine.connect() as conn:
        assert len(conn.execute(select(archives).where(archives.c.run_id == rid)).all()) == 1
    assert len([f for f in os.listdir(archive_dirs["final"]) if f.endswith(".json")]) == 1


def test_fresh_archive_removes_orphan_staging_files(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
    # 制造一个此前失败尝试遗留的临时件
    orphan = os.path.join(archive_dirs["staging"], f"handover-{rid}-orphan123.tmp")
    with open(orphan, "wb") as handle:
        handle.write(b"stale")
    report = retention.sweep(now=NOW)
    assert report.archived == [rid]
    assert not os.path.exists(orphan)
    assert os.listdir(archive_dirs["staging"]) == []
    assert os.path.exists(os.path.join(archive_dirs["final"], handover.archive_filename(rid)))


def test_recovery_after_promote_before_commit_uses_final_copy(db, archive_dirs):
    # 模拟文件已原子改名到正式位、但 DB 事务未提交（台账仍 staged、原件还在库内）
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
        run = get_run(conn, rid)
    with engine.begin() as conn:
        joined = handover.load_run(conn, rid)
        document = handover.document_for_run(joined, review_service.get(conn, rid))
        payload = handover.encode_document(document)
    final_path = os.path.join(archive_dirs["final"], handover.archive_filename(rid))
    os.makedirs(archive_dirs["final"], exist_ok=True)
    with open(final_path, "wb") as handle:
        handle.write(payload)
    with engine.begin() as conn:
        conn.execute(insert(archives).values(
            run_id=rid, path=final_path, digest=run["digest"], stage="staged",
            actor="maintenance", created_at=NOW, verified_at=None))
    report = retention.sweep(now=NOW)
    assert rid in report.recovered
    with engine.connect() as conn:
        ledger = get_ledger(conn, rid)
        assert ledger["stage"] == "verified" and ledger["path"] == final_path
        assert get_run(conn, rid)["status"] == JobState.ARCHIVED
    # 正式位仍只有一份，没有重复文件
    assert [f for f in os.listdir(archive_dirs["final"]) if f.endswith(".json")] == \
        [handover.archive_filename(rid)]


def test_recovery_regenerates_when_staging_file_lost(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
        run = get_run(conn, rid)
    with engine.begin() as conn:
        conn.execute(insert(archives).values(
            run_id=rid, path="/nonexistent/x.tmp", digest=run["digest"], stage="staged",
            actor="maintenance", created_at=NOW, verified_at=None))
    # staged 副本已丢失：恢复时删除旧台账并重新生成、核验、落盘
    with engine.begin() as conn:
        outcome = retention.process_run(conn, rid)
    assert outcome in ("archived", "recovered")
    with engine.connect() as conn:
        ledger = get_ledger(conn, rid)
        assert ledger["stage"] == "verified"
        assert get_run(conn, rid)["status"] == JobState.ARCHIVED
    # 再扫一遍也不会重复存档
    again = retention.sweep(now=NOW)
    assert again.archived == [] and again.candidates == 0


def test_verification_failure_keeps_library_original(db, archive_dirs, monkeypatch):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
    # 模拟落盘副本与库内校验值不符：篡改已写好的 staging 文件
    real_write = retention._write_staging

    def tampered(run_id, payload):
        path = real_write(run_id, payload)
        with open(path, "wb") as handle:
            handle.write(b'{"digest":"deadbeef","result":{}}')
        return path

    monkeypatch.setattr(retention, "_write_staging", tampered)
    with pytest.raises(RuntimeError):
        retention._process_in_transaction(rid, "maintenance")
    with engine.connect() as conn:
        row = get_run(conn, rid)
        assert row["status"] == JobState.DONE and row["result"] is not None  # 原件保留
        assert get_ledger(conn, rid) is None  # 事务回滚，无台账
    # 修复后可正常重试：仅还原被篡改的写盘函数，保留归档目录补丁
    monkeypatch.setattr(retention, "_write_staging", real_write)
    report = retention.sweep(now=NOW)
    assert report.archived == [rid]


def test_retention_window_and_failed_runs(db, archive_dirs):
    with engine.begin() as conn:
        old_signed = insert_run(conn, age_days=120, signed=True)
        old_unsigned = insert_run(conn, age_days=91, signed=False)
        fresh = insert_run(conn, age_days=30, signed=False)
        failed_old = insert_run(conn, age_days=200, status=JobState.FAILED,
                                result=False, failure="处理失败，请检查 worker 日志")
        queued = insert_run(conn, age_days=200, status=JobState.QUEUED, finished=False)
    report = retention.sweep(now=NOW, retention_days=90)
    assert report.candidates == 3  # 满 90 天的 done/failed，queued 与新鲜批次不在内
    assert sorted(report.archived) == [old_signed]
    assert sorted(report.cleared) == sorted([old_unsigned, failed_old])
    with engine.connect() as conn:
        assert get_run(conn, fresh)["status"] == JobState.DONE
        assert get_run(conn, queued)["status"] == JobState.QUEUED
        assert get_run(conn, failed_old)["failure"] is None
