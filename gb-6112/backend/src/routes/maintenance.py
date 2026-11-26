from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.config.resources import engine
from src.controllers.access import reviewer
from src.models.archives import archives
from src.schemas.retention import ArchiveEntry, RetentionReport
from src.services import retention

router = APIRouter(prefix="/maintenance", tags=["Retention"])


@router.post("/retention-sweep", response_model=RetentionReport, dependencies=[Depends(reviewer)])
def run_sweep():
    """整理保留期届满批次：已签转归档、未签清结果。复核员触发并留审计。"""
    report = retention.sweep(actor="reviewer")
    return report.as_dict()


@router.get("/archives", response_model=list[ArchiveEntry], dependencies=[Depends(reviewer)])
def list_archives():
    with engine.connect() as db:
        rows = db.execute(select(archives).order_by(archives.c.id.desc()).limit(100)).mappings()
        return [dict(row) for row in rows]
