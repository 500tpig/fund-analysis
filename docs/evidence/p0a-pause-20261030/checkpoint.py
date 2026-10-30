"""P0-A D 节检查点：生产库只读采集（在服务器上以 fund-analysis 用户运行）。

做法与 docs/evidence/p0a-settlement-baseline-20261029.json 的 fingerprint_spec 一致：
经 sqlite3 backup API 把主库与模拟库复制到临时一致性副本，在副本上逐行规范化 JSON、sha256，
再在副本上运行项目自带的 `simulation show / metrics / verify`；结束删除副本。全程不写生产库。

用法：python3 - <label>   （stdin 为本文件）
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

LABEL = sys.argv[1] if len(sys.argv) > 1 else "checkpoint"
MAIN = "/var/lib/fund-analysis/fund_analysis.db"
SIM = "/var/lib/fund-analysis/simulation.db"
CLI = "/opt/fund-analysis/current/.venv/bin/fund-analysis"

SIM_ROW_TABLES = {
    "accounts": "id",
    "strategy_versions": "id",
    "rule_versions": "id",
    "orders": "id",
    "order_events": "id",
    "runs": "started_at, id",
    "run_items": "id",
    "lots": "id",
    "ledger_events": "id",
    "snapshots": "observed_at, id",
    "idempotency_keys": "created_at, scope, operation, key",
    "account_operations": "id",
}
MAIN_ROW_TABLES = {"holdings": "id", "transactions": "id"}


def canon(row: dict) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def block(con: sqlite3.Connection, table: str, order_by: str) -> dict:
    cur = con.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    digests = [hashlib.sha256(canon(r).encode("utf-8")).hexdigest() for r in rows]
    return {
        "count": len(rows),
        "columns": cols,
        "order_by": order_by,
        "aggregate_sha256": hashlib.sha256("\n".join(digests).encode("utf-8")).hexdigest(),
        "row_digests": digests,
        "rows": rows,
    }


def copy_db(src: str, dst: str) -> None:
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    with target:
        source.backup(target)
    source.close()
    target.close()


def cli(*args: str, sim: str, main: str) -> dict:
    proc = subprocess.run([CLI, "simulation", "--sim-db", sim, "--main-db", main, *args],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "TZ": "Asia/Shanghai", "PYTHONDONTWRITEBYTECODE": "1"})
    return {"exit": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}


now = datetime.now(timezone.utc)
out = {
    "label": LABEL,
    "fetched_at_utc": now.isoformat(),
    "fetched_at_beijing": now.astimezone().isoformat() if os.environ.get("TZ") else None,
    "python_version": sys.version.split()[0],
    "sqlite_lib_version": sqlite3.sqlite_version,
    "current_release": os.path.realpath("/opt/fund-analysis/current"),
}
tmp = tempfile.mkdtemp(prefix=f"fa-dcheck-{now.strftime('%Y%m%dT%H%M%SZ')}-")
os.chmod(tmp, 0o700)
try:
    main_copy, sim_copy = os.path.join(tmp, "main.db"), os.path.join(tmp, "sim.db")
    copy_db(MAIN, main_copy)
    copy_db(SIM, sim_copy)
    m = sqlite3.connect(main_copy)
    out["main_db"] = {t: block(m, t, o) for t, o in MAIN_ROW_TABLES.items()}
    for t in ("data_source_runs", "fund_valuation_profiles"):
        c, mx = m.execute(f"SELECT count(*), max(rowid) FROM {t}").fetchone()
        out["main_db"][t] = {"count": c, "max_rowid": mx}
    m.close()
    s = sqlite3.connect(sim_copy)
    out["sim_db"] = {t: block(s, t, o) for t, o in SIM_ROW_TABLES.items()}
    for t in ("valuations", "dividends"):
        c, mx = s.execute(f"SELECT count(*), max(rowid) FROM {t}").fetchone()
        out["sim_db"][t] = {"count": c, "max_rowid": mx}
    cur = s.execute("SELECT * FROM valuations ORDER BY rowid DESC LIMIT 3")
    cols = [d[0] for d in cur.description]
    out["sim_db"]["valuations"]["latest"] = [dict(zip(cols, r)) for r in cur.fetchall()]
    s.close()
    acct = "acct_57e3deb6871f420c9e98"
    out["cli"] = {
        "show": cli("show", "--account", acct, sim=sim_copy, main=main_copy),
        "metrics": cli("metrics", "--account", acct, sim=sim_copy, main=main_copy),
        "verify": cli("verify", sim=sim_copy, main=main_copy),
    }
finally:
    shutil.rmtree(tmp)
out["temp_copy"] = {"path": tmp, "deleted": not os.path.exists(tmp)}
print(json.dumps(out, ensure_ascii=False, indent=1))
