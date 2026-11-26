from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from src.constants.job_state import JobState
from src.models.reviews import reviews
from src.models.runs import runs
from . import audit


def get(db, run_id):
    row = db.execute(select(reviews).where(reviews.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None


def sign(db, run_id, data, actor):
    # 行锁与保留期整理互斥：谁先拿到锁谁先生效，避免边清理边签署。
    run = db.execute(select(runs).where(runs.c.id == run_id).with_for_update()).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    status = run["status"]
    if status == JobState.CLEARED:
        raise HTTPException(409, "该批次未在保留期内签署，试写结果已清理且无交接单，无法补签")
    if status == JobState.ARCHIVED:
        raise HTTPException(409, "该批次交接单已归档，签署以归档副本为准，不能重复签署")
    if status != JobState.DONE:
        raise HTTPException(409, "批次执行成功后才能签核")
    try:
        db.execute(insert(reviews).values(run_id=run_id, note=data.note, actor=actor, created_at=int(time())))
    except IntegrityError as exc:
        raise HTTPException(409, "该批次已经签核") from exc
    audit.record(db, "review", actor, "run", run_id)
    return get(db, run_id)
