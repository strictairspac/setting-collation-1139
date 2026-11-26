from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Table, UniqueConstraint
from .base import metadata


# 归档台账：每个交接单（执行批次）至多一条有效归档记录。
# stage 标识归档进度，保证中断后可按已核验批次恢复、重试不重复存档。
archives = Table(
    "archives", metadata,
    # MySQL 用 BIGINT 自增；SQLite 测试环境回退到 INTEGER 以支持 rowid 自增。
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("run_id", ForeignKey("runs.id"), nullable=False),
    Column("path", String(320), nullable=False),
    Column("digest", String(64), nullable=False),
    # staged   = 归档盘临时文件已写好但尚未核验
    # verified = 副本校验通过、原件已转走，归档完成
    Column("stage", String(16), nullable=False, index=True),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("verified_at", BigInteger, nullable=True),
    UniqueConstraint("run_id", name="uq_archives_run"),
    UniqueConstraint("digest", name="uq_archives_digest"),
)
