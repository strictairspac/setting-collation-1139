"""交接单（证据导出）的构建与读取。

归档前与下载入口共用同一套文档结构和规范化编码，保证批次号、SHA-256 校验值
和复核签署在搬移到归档盘前后完全一致。
"""
import json

from fastapi import HTTPException
from sqlalchemy import select

from src.constants.job_state import JobState
from src.models.archives import archives
from src.services import runs as run_service
from src.utils.serial import canonical_json, content_digest
from . import reviews as review_service

EXPORT_PREFIX = "evidence"
ARCHIVE_PREFIX = "handover"


def document_for_run(run: dict, review: dict | None) -> dict:
    """组装交接单文档，结构与历史 /export 响应（联表批次 + review）保持一致。"""
    document = dict(run)
    document["review"] = review
    return document


def encode_document(document: dict) -> bytes:
    return canonical_json(document)


def archive_filename(run_id: int) -> str:
    return f"{ARCHIVE_PREFIX}-{run_id}.json"


def export_filename(run_id: int) -> str:
    return f"{EXPORT_PREFIX}-{run_id}.json"


def verify_document(document: dict, expected_digest: str | None = None) -> str:
    """校验交接单副本：批次结果的 SHA-256 必须与库内校验值一致。"""
    digest = document.get("digest")
    result = document.get("result")
    if not digest or result is None:
        raise ValueError("归档交接单缺少校验值或业务结果")
    if content_digest(result) != digest:
        raise ValueError("归档交接单校验值与业务结果不一致")
    if expected_digest is not None and digest != expected_digest:
        raise ValueError("归档交接单校验值与批次台账不一致")
    return digest


def load_run(db, run_id: int, for_update: bool = False) -> dict | None:
    """读取联表批次（含 case_title），可选行锁，供归档与下载共用同一数据形状。"""
    statement = run_service.query().where(run_service.runs.c.id == run_id)
    if for_update:
        statement = statement.with_for_update()
    row = db.execute(statement).mappings().first()
    return dict(row) if row is not None else None


def live_document(db, run: dict) -> dict:
    """库内仍持有结果时构建交接单（归档前使用）。"""
    review = review_service.get(db, run["id"])
    return document_for_run(run, review)


def read_archived(db, run_id: int) -> dict:
    """从归档盘读取已归档批次的交接单并核验，失败即拒绝下载。"""
    ledger = db.execute(select(archives).where(archives.c.run_id == run_id)).mappings().first()
    if ledger is None or ledger["stage"] != "verified":
        raise HTTPException(409, "交接单尚未完成归档，暂时无法下载")
    try:
        with open(ledger["path"], "rb") as handle:
            document = json.loads(handle.read().decode())
    except FileNotFoundError as exc:
        raise HTTPException(409, "归档盘上的交接单副本缺失，请联系管理员") from exc
    if document.get("id") != run_id:
        raise HTTPException(500, "归档交接单的批次号与台账不符")
    try:
        verify_document(document, ledger["digest"])
    except ValueError as exc:
        raise HTTPException(500, str(exc)) from exc
    return document


def build_export(db, run_id: int) -> tuple[bytes, str]:
    """统一下载入口：库内原件或归档副本都返回相同结构的交接单。"""
    run = load_run(db, run_id)
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    status = run["status"]
    if status == JobState.ARCHIVED:
        document = read_archived(db, run_id)
    elif status == JobState.CLEARED:
        raise HTTPException(409, "该批次未签署，试写结果已按保留期清理，无交接单可下载")
    elif status != JobState.DONE:
        raise HTTPException(409, "没有可导出的执行结果")
    else:
        document = live_document(db, run)
    return encode_document(document), export_filename(run_id)
