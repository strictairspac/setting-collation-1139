from sqlalchemy import inspect, text

from src.models import archives  # noqa: F401  登记表到 metadata
from src.models.archives import archives as archives_table
from src.models.base import metadata


def upgrade(connection):
    """Version 2：引入归档台账与整理后批次状态，幂等可重复执行。"""
    inspector = inspect(connection)
    if not inspector.has_table(archives_table.name):
        archives_table.create(connection, checkfirst=True)

    # runs.status 建表即为 VARCHAR(16)，archived/cleared 可直接容纳；
    # 仅当旧库列宽不足时才扩容，避免每次启动都执行 DDL。
    if connection.dialect.name == "mysql":
        status_column = next(
            (column for column in inspector.get_columns("runs") if column["name"] == "status"),
            None,
        )
        if status_column is not None and getattr(status_column["type"], "length", None) not in (None, 16):
            connection.execute(text("ALTER TABLE runs MODIFY COLUMN status VARCHAR(16) NOT NULL"))

    metadata.create_all(connection, tables=[archives_table], checkfirst=True)
