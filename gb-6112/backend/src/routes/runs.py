from fastapi import APIRouter, Depends, Response

from src.config.resources import engine
from src.controllers.access import identity, reviewer
from src.controllers.workbench import new_review
from src.schemas.reviews import ReviewEntry, ReviewInput
from src.schemas.runs import RunEntry
from src.services import handover, reviews, runs

router = APIRouter(prefix="/runs", tags=["Execution"])


@router.get("", response_model=list[RunEntry], dependencies=[Depends(identity)])
def list_runs():
    with engine.connect() as db:
        return runs.list_entries(db)


@router.get("/{run_id}", response_model=RunEntry, dependencies=[Depends(identity)])
def run_detail(run_id: int):
    with engine.connect() as db:
        return runs.get(db, run_id)


@router.get("/{run_id}/review", response_model=ReviewEntry | None, dependencies=[Depends(identity)])
def review_detail(run_id: int):
    with engine.connect() as db:
        runs.get(db, run_id)
        return reviews.get(db, run_id)


@router.post("/{run_id}/review", response_model=ReviewEntry, status_code=201)
def sign(run_id: int, form: ReviewInput, user=Depends(reviewer)):
    return new_review(run_id, form, user)


@router.get("/{run_id}/export", dependencies=[Depends(identity)])
def export(run_id: int):
    # 同一入口同时服务库内原件与已归档副本，批次号、校验值和签署保持不变。
    with engine.connect() as db:
        content, filename = handover.build_export(db, run_id)
    return Response(content, media_type="application/json", headers={
        "Content-Disposition": f"attachment; filename={filename}",
    })
