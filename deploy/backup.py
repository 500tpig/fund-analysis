"""用 SQLite backup 保存一致性快照，不复制活跃 WAL 文件。"""

import sqlite3
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:])
# 目标独占创建，避免误覆盖已有快照。
with target.open("xb") as output:
    target.chmod(0o600)
try:
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("backup integrity check failed")
except Exception:
    target.unlink()
    raise
print(f"SQLite backup verified: {target}")
