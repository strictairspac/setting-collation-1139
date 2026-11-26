from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"
    # 终态批次在保留期届满后的去向：
    # archived = 已签署交接单转归档盘，库内只留批次记录与归档台账
    # cleared  = 未签署批次的试写结果已清空，批次记录保留
    ARCHIVED = "archived"
    CLEARED = "cleared"


# 仍占用交接库存放试写结果的终态，保留期届满后需要整理
REVIEWABLE_STATES = (JobState.DONE, JobState.FAILED)
