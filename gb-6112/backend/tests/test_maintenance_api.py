"""维护接口与下载入口的端到端用例。"""
import json

from fastapi.testclient import TestClient

from src.config.resources import engine
from src.constants.job_state import JobState
from src.main import app

from conftest import insert_run

client = TestClient(app)


def login(username, password="test-pass"):
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_sweep_requires_reviewer(db, archive_dirs):
    assert client.post("/maintenance/retention-sweep").status_code == 401
    operator = login("operator")
    assert client.post("/maintenance/retention-sweep", headers=operator).status_code == 403
    reviewer = login("reviewer")
    resp = client.post("/maintenance/retention-sweep", headers=reviewer)
    assert resp.status_code == 200
    assert resp.json()["retention_days"] == 90


def test_full_flow_archive_then_download_from_same_entry(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
    # 整理前从原入口下载
    reviewer = login("reviewer")
    before = client.get(f"/runs/{rid}/export", headers=reviewer)
    assert before.status_code == 200
    before_bytes = before.content
    # 触发整理
    swept = client.post("/maintenance/retention-sweep", headers=reviewer).json()
    assert rid in swept["archived"]
    # 批次列表仍含该批次，状态为 archived
    listing = client.get("/runs", headers=reviewer).json()
    entry = next(item for item in listing if item["id"] == rid)
    assert entry["status"] == JobState.ARCHIVED and entry["result"] is None
    # 归档台账可查
    archives = client.get("/maintenance/archives", headers=reviewer).json()
    ledger = next(item for item in archives if item["run_id"] == rid)
    assert ledger["stage"] == "verified"
    # 仍从原入口下载，内容逐字节一致
    after = client.get(f"/runs/{rid}/export", headers=reviewer)
    assert after.status_code == 200
    assert after.content == before_bytes
    assert f"evidence-{rid}.json" in after.headers["content-disposition"]
    doc = json.loads(after.content.decode())
    assert doc["id"] == rid and doc["review"]["actor"] == "reviewer"


def test_cleared_run_export_returns_conflict(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=False)
    client.post("/maintenance/retention-sweep", headers=login("reviewer"))
    resp = client.get(f"/runs/{rid}/export", headers=login("viewer"))
    assert resp.status_code == 409 and "清理" in resp.json()["detail"]


def test_review_endpoint_rejects_signing_cleared_run(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=False)
    client.post("/maintenance/retention-sweep", headers=login("reviewer"))
    resp = client.post(f"/runs/{rid}/review", headers=login("reviewer"),
                       json={"note": "逾期补签意见"})
    assert resp.status_code == 409 and "无法补签" in resp.json()["detail"]


def test_viewer_can_download_but_not_sweep(db, archive_dirs):
    with engine.begin() as conn:
        rid = insert_run(conn, age_days=120, signed=True)
    viewer = login("viewer")
    assert client.post("/maintenance/retention-sweep", headers=viewer).status_code == 403
    assert client.get(f"/runs/{rid}/export", headers=viewer).status_code == 200
