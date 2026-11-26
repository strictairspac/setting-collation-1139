import os

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.environ["JWT_SECRET"]
DEMO_PASSWORD = os.environ["DEMO_PASSWORD"]
SEED_DEMO = os.environ.get("SEED_DEMO", "true").lower() == "true"

# 交接库保留期：批次结束满这么多天才纳入整理（默认 90 天，即满三个月）
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "90"))
# 归档盘根目录（compose 中由 evidence 命名卷挂载），其下的 archive 子目录存放交接单副本
ARCHIVE_ROOT = os.environ.get("ARCHIVE_ROOT", "/var/lib/workbench")
ARCHIVE_DIR = os.environ.get("ARCHIVE_DIR", os.path.join(ARCHIVE_ROOT, "archive"))
# 归档临时子目录，校验通过后再原子移动到正式目录，中断时这里只会留下半成品
ARCHIVE_STAGING_DIR = os.environ.get(
    "ARCHIVE_STAGING_DIR", os.path.join(ARCHIVE_ROOT, "archive", ".staging")
)
