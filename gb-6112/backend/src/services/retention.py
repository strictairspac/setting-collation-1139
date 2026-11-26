"""交接库保留期整理。

规则：
- 仅处理结束（done/failed）满 RETENTION_DAYS 的批次，queued 等在途批次不动。
- 已签署：交接单副本写入归档盘并重新校验 SHA-256，通过后才转走库内原件。
- 未签署：清空试写结果，保留批次记录，状态置 cleared。
- 与复核签署并发时以行锁 + 复查裁决：先签成的转归档；先清掉的批次，
  签署请求会拿到明确的 409 原因。
- 归档中断按已核验批次恢复；台账保证同一交接单不重复存档。
"""
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field

from sqlalchemy import select, update

from src.config.settings import ARCHIVE_DIR, ARCHIVE_STAGING_DIR, RETENTION_DAYS
from src.constants.job_state import REVIEWABLE_STATES, JobState
from src.models.archives import archives
from src.models.reviews import reviews
from src.models.runs import runs
from src.services import handover
from src.services.audit import record

logger = logging.getLogger(__name__)

STAGE_STAGED = "staged"
STAGE_VERIFIED = "verified"


@dataclass
class SweepReport:
    cutoff: int
    retention_days: int
    candidates: int = 0
    archived: list[int] = field(default_factory=list)
    cleared: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    recovered: list[int] = field(default_factory=list)

    def as_dict(self):
        return {
            "cutoff": self.cutoff,
            "retention_days": self.retention_days,
            "candidates": self.candidates,
            "archived": self.archived,
            "cleared": self.cleared,
            "skipped": self.skipped,
            "recovered": self.recovered,
        }


def retention_cutoff(now: int | None = None, retention_days: int = RETENTION_DAYS) -> int:
    return (now if now is not None else int(time.time())) - retention_days * 86400


def _candidate_ids(db, cutoff: int) -> list[int]:
    rows = db.execute(
        select(runs.c.id)
        .where(runs.c.status.in_(REVIEWABLE_STATES), runs.c.finished_at.is_not(None))
        .where(runs.c.finished_at <= cutoff)
        .order_by(runs.c.id)
    ).scalars()
    return list(rows)


def _lock_run(db, run_id: int) -> dict | None:
    return db.execute(
        select(runs).where(runs.c.id == run_id).with_for_update()
    ).mappings().first()


def _ensure_dirs():
    os.makedirs(ARCHIVE_STAGING_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)


def _write_staging(run_id: int, payload: bytes) -> str:
    """先写到归档盘临时区（同卷），校验通过后再原子改名，避免半成品被当正式件。"""
    _ensure_dirs()
    fd, staging_path = tempfile.mkstemp(prefix=f"{handover.ARCHIVE_PREFIX}-{run_id}-", suffix=".tmp",
                                        dir=ARCHIVE_STAGING_DIR)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if os.path.exists(staging_path):
            os.unlink(staging_path)
        raise
    return staging_path


def _promote(staging_path: str, run_id: int) -> str:
    final_path = os.path.join(ARCHIVE_DIR, handover.archive_filename(run_id))
    os.replace(staging_path, final_path)
    return final_path


def _discard_stale_staging(run_id: int) -> None:
    """清理此前失败尝试遗留在临时区、属于本批次的半成品。"""
    if not os.path.isdir(ARCHIVE_STAGING_DIR):
        return
    prefix = f"{handover.ARCHIVE_PREFIX}-{run_id}-"
    for name in os.listdir(ARCHIVE_STAGING_DIR):
        if name.startswith(prefix) and name.endswith(".tmp"):
            try:
                os.unlink(os.path.join(ARCHIVE_STAGING_DIR, name))
            except FileNotFoundError:
                pass


def _recover_staged(db, ledger_row: dict, run: dict, actor: str) -> str | None:
    """恢复中断的归档：已有 staged 台账则核验既有副本，不重复写盘、不重复登记。"""
    staging_path = None
    final_path = os.path.join(ARCHIVE_DIR, handover.archive_filename(run["id"]))
    if os.path.exists(final_path):
        candidate_path = final_path
    else:
        # 找到本批次遗留在临时区的半成品
        for name in os.listdir(ARCHIVE_STAGING_DIR) if os.path.isdir(ARCHIVE_STAGING_DIR) else []:
            if name.startswith(f"{handover.ARCHIVE_PREFIX}-{run['id']}-") and name.endswith(".tmp"):
                staging_path = os.path.join(ARCHIVE_STAGING_DIR, name)
                candidate_path = staging_path
                break
        else:
            candidate_path = None
    if candidate_path is None:
        # 临时文件已丢失：删除 staged 台账，让本次重新生成副本
        db.execute(archives.delete().where(archives.c.id == ledger_row["id"]))
        return None
    with open(candidate_path, "rb") as handle:
        document = json.loads(handle.read().decode())
    handover.verify_document(document, expected_digest=run["digest"])
    promoted_path = _promote(candidate_path, run["id"]) if staging_path else final_path
    db.execute(
        update(archives).where(archives.c.id == ledger_row["id"]).values(
            path=promoted_path, stage=STAGE_VERIFIED, verified_at=int(time.time())
        )
    )
    db.execute(
        update(runs).where(runs.c.id == run["id"]).values(
            status=JobState.ARCHIVED, result=None, failure=None
        )
    )
    record(db, "archive_recover", actor, "run", run["id"])
    return "recovered"


def _archive_signed(db, run: dict, actor: str) -> str | None:
    """在已持有行锁的事务内归档一个已签署批次。

    返回 "archived"（本次新归档/幂等收口）或 "recovered"（断点恢复），未签署返回 None。
    """
    run_id = run["id"]
    existing = db.execute(
        select(archives).where(archives.c.run_id == run_id).with_for_update()
    ).mappings().first()
    if existing is not None:
        if existing["stage"] == STAGE_VERIFIED:
            # 已核验过：直接幂等收口，同一交接单绝不重复存档
            if run["status"] != JobState.ARCHIVED:
                db.execute(update(runs).where(runs.c.id == run_id).values(
                    status=JobState.ARCHIVED, result=None, failure=None))
                record(db, "archive", actor, "run", run_id)
            return "archived"
        recovered = _recover_staged(db, dict(existing), run, actor)
        if recovered:
            return recovered
        # staged 副本已丢失时恢复逻辑会删掉旧台账；重新查询以决定是否需要新建。
        existing = db.execute(
            select(archives).where(archives.c.run_id == run_id)
        ).mappings().first()

    review = db.execute(select(reviews).where(reviews.c.run_id == run_id)).mappings().first()
    if review is None:
        # 行锁复查：台账未落地前签署被回滚或本就未签，交给清理分支
        return None

    document = handover.document_for_run(run, dict(review))
    handover.verify_document(document, expected_digest=run["digest"])
    payload = handover.encode_document(document)

    # 全新归档前先清掉此前失败尝试的残留临时件（有台账的恢复由 _recover_staged 处理）。
    _discard_stale_staging(run_id)
    staging_path = _write_staging(run_id, payload)
    # 副本落盘后重新解析并校验：副本内结果的 SHA-256 必须与库内校验值一致，
    # 校验通过才允许转走原件。
    with open(staging_path, "rb") as handle:
        staged_document = json.loads(handle.read().decode())
    try:
        handover.verify_document(staged_document, expected_digest=run["digest"])
    except ValueError as exc:
        os.unlink(staging_path)
        raise RuntimeError(f"批次 {run_id} 归档副本校验失败，库内原件保留：{exc}") from exc

    now = int(time.time())
    if existing is None:
        db.execute(archives.insert().values(
            run_id=run_id, path=staging_path, digest=run["digest"],
            stage=STAGE_STAGED, actor=actor, created_at=now, verified_at=None,
        ))
    final_path = _promote(staging_path, run_id)
    db.execute(
        update(archives).where(archives.c.run_id == run_id).values(
            path=final_path, stage=STAGE_VERIFIED, verified_at=now
        )
    )
    # 校验通过才移走库内原件：result 清空，digest 与批次记录保留
    db.execute(update(runs).where(runs.c.id == run_id).values(
        status=JobState.ARCHIVED, result=None, failure=None))
    record(db, "archive", actor, "run", run_id)
    return "archived"


def _clear_unsigned(db, run: dict, actor: str) -> bool:
    """清空未签署批次的试写结果，保留批次记录。返回是否执行了清理。"""
    run_id = run["id"]
    review = db.execute(select(reviews).where(reviews.c.run_id == run_id)).mappings().first()
    if review is not None:
        # 行锁复查发现已签署：并发中签署先成，转归档而不是清理
        return False
    db.execute(update(runs).where(runs.c.id == run_id).values(
        status=JobState.CLEARED, result=None, digest=None, failure=None))
    record(db, "clear", actor, "run", run_id)
    return True


def process_run(db, run_id: int, actor: str = "maintenance") -> str:
    """处理单个批次，返回去向：archived / recovered / cleared / skipped。调用方持有事务。"""
    locked = _lock_run(db, run_id)
    if locked is None:
        return "skipped"
    # 行锁只落在 runs 上；联表数据用于构建与下载入口一致的交接单文档。
    run = handover.load_run(db, run_id)
    if run["status"] not in REVIEWABLE_STATES:
        return "skipped"

    existing = db.execute(
        select(archives).where(archives.c.run_id == run_id)
    ).mappings().first()
    if existing is not None and existing["stage"] == STAGE_VERIFIED:
        outcome = _archive_signed(db, run, actor)
        return outcome or "skipped"

    # 已签署 -> 归档；未签署 -> 清理。两者内部都会在锁内复查签署状态。
    review = db.execute(select(reviews).where(reviews.c.run_id == run_id)).mappings().first()
    if review is not None:
        outcome = _archive_signed(db, run, actor)
        if outcome:
            return outcome
        # 归档路径复查认为未签署（理论上不会走到），落入清理
    if _clear_unsigned(db, run, actor):
        return "cleared"
    # 清理路径复查发现刚刚被签署：重跑归档分支
    outcome = _archive_signed(db, run, actor)
    if outcome:
        return outcome
    return "skipped"


def sweep(actor: str = "maintenance", now: int | None = None,
          retention_days: int = RETENTION_DAYS) -> SweepReport:
    """扫描保留期届满的批次并逐批处理，每批独立事务以便中断恢复。"""
    cutoff = retention_cutoff(now, retention_days)
    report = SweepReport(cutoff=cutoff, retention_days=retention_days)

    candidate_ids = _scan_candidates(cutoff)
    report.candidates = len(candidate_ids)

    for run_id in candidate_ids:
        try:
            outcome = _process_in_transaction(run_id, actor)
        except Exception:
            # 单批失败不影响其它批次；原件与校验都在，下次可安全重试
            logger.exception("保留期整理中断于批次 %s", run_id)
            report.skipped.append(run_id)
            continue
        if outcome == "recovered":
            report.recovered.append(run_id)
            report.archived.append(run_id)
        elif outcome == "archived":
            report.archived.append(run_id)
        elif outcome == "cleared":
            report.cleared.append(run_id)
        else:
            report.skipped.append(run_id)
    return report


def _scan_candidates(cutoff: int) -> list[int]:
    from src.config.resources import engine
    with engine.connect() as db:
        return _candidate_ids(db, cutoff)


def _process_in_transaction(run_id: int, actor: str) -> str:
    from src.config.resources import engine
    with engine.begin() as db:
        return process_run(db, run_id, actor)
