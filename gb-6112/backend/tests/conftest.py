"""测试夹具：用 SQLite 文件库替代 MySQL，用临时目录替代归档盘。

环境变量必须在导入 src.config 之前设置；FOR UPDATE 在 SQLite 上为空操作，
因此并发裁决用例通过控制服务调用顺序来确定性复现，真实行锁语义在 MySQL 上成立。
"""
import os
import tempfile

import pytest

_DB_FD, _DB_PATH = tempfile.mkstemp(prefix="workbench-test-", suffix=".db")
os.close(_DB_FD)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_DB_PATH}")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DEMO_PASSWORD", "test-pass")
os.environ.setdefault("SEED_DEMO", "false")

from sqlalchemy import insert  # noqa: E402

from src.config.resources import engine  # noqa: E402
from src.constants.job_state import JobState  # noqa: E402
from src.models.base import metadata  # noqa: E402
from src.models import archives as archives_model  # noqa: E402,F401
from src.models import cases as cases_model, reviews as reviews_model  # noqa: E402,F401
from src.models import runs as runs_model, users as users_model  # noqa: E402,F401
from src.models.archives import archives  # noqa: E402
from src.models.cases import cases  # noqa: E402
from src.models.reviews import reviews  # noqa: E402
from src.models.runs import runs  # noqa: E402
from src.models.users import users  # noqa: E402
from src.utils.passwords import hash_password  # noqa: E402
from src.utils.serial import content_digest  # noqa: E402

NOW = 1_700_000_000
DAY = 86400


@pytest.fixture
def archive_dirs(monkeypatch):
    root = tempfile.mkdtemp(prefix="archive-test-")
    final_dir = os.path.join(root, "archive")
    staging_dir = os.path.join(root, "archive", ".staging")
    import src.services.retention as retention
    monkeypatch.setattr(retention, "ARCHIVE_DIR", final_dir)
    monkeypatch.setattr(retention, "ARCHIVE_STAGING_DIR", staging_dir)
    os.makedirs(staging_dir, exist_ok=True)
    return {"root": root, "final": final_dir, "staging": staging_dir}


@pytest.fixture
def db():
    metadata.drop_all(engine)
    metadata.create_all(engine)
    with engine.begin() as conn:
        for name, role in (("operator", "operator"), ("reviewer", "reviewer"), ("viewer", "viewer")):
            conn.execute(insert(users).values(
                username=name, role=role, password_hash=hash_password("test-pass")))
        conn.execute(insert(cases).values(
            code="CASE-TEST-1", title="测试卷", description="夹具资料",
            payload={"tickets": []}, created_by="operator"))
    yield engine
    metadata.drop_all(engine)


def sample_result():
    return {
        "summary": "1 组同间冲突，改号 1 张后全卷通过目标唯一约束。",
        "metrics": {"冲突组": 1, "需改号": 1, "通过试写": 2},
        "rows": [{"工票 ID": "N002", "工作间": "北间", "原票号": "eclat-27", "目标票号": "R-N002-1", "动作": "别名已验"}],
    }


def insert_run(conn, *, status=JobState.DONE, age_days=120, signed=False,
               result=True, failure=None, finished=True, run_id=None):
    payload = sample_result() if result is True else (result if isinstance(result, dict) else None)
    digest = content_digest(payload) if payload is not None else None
    values = dict(
        case_id=1, status=status, parameter=6, actor="operator",
        created_at=NOW - age_days * DAY - 100,
        finished_at=(NOW - age_days * DAY) if finished else None,
        result=payload, failure=failure, digest=digest,
    )
    if run_id is not None:
        values["id"] = run_id
    cur = conn.execute(insert(runs).values(**values))
    rid = run_id if run_id is not None else cur.inserted_primary_key[0]
    if signed:
        conn.execute(insert(reviews).values(
            run_id=rid, note="复核无误，同意交接。", actor="reviewer", created_at=NOW - age_days * DAY + 10))
    return rid


def get_run(conn, run_id):
    return dict(conn.execute(runs.select().where(runs.c.id == run_id)).mappings().first())


def get_ledger(conn, run_id):
    row = conn.execute(archives.select().where(archives.c.run_id == run_id)).mappings().first()
    return dict(row) if row else None
