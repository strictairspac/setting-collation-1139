"""手动触发交接库保留期整理。

用法（容器内）：
    python -m scripts.retention_sweep
可选环境变量 RETENTION_DAYS 覆盖保留天数，便于验收。
"""
import json

from scripts.seed_demo import setup_schema
from src.services.retention import sweep


def main():
    setup_schema()
    report = sweep(actor="maintenance")
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
