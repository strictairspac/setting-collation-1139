from pydantic import BaseModel


class ArchiveEntry(BaseModel):
    id: int
    run_id: int
    path: str
    digest: str
    stage: str
    actor: str
    created_at: int
    verified_at: int | None


class RetentionReport(BaseModel):
    cutoff: int
    retention_days: int
    candidates: int
    archived: list[int]
    cleared: list[int]
    skipped: list[int]
    recovered: list[int]
