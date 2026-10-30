"""把检查点 JSON 与 10-29 结算前基线逐区块对比。

用法：python3 compare.py <基线 JSON> <检查点 JSON> [输出 JSON]
对每个带 rows 的区块：先核对 columns 与 order_by（不一致即判漂移、不比对），
再按主键对行：digest 相同为未变，不同列出变化字段，另列新增与删除的行。
"""
import hashlib
import json
import sys

KEYS = {"idempotency_keys": ("scope", "operation", "key")}


def key_of(table, row):
    cols = KEYS.get(table, ("id",))
    return "|".join(str(row[c]) for c in cols)


def digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def compare(table, old, new):
    res = {"old_count": old["count"], "new_count": new["count"]}
    if old["columns"] != new["columns"] or old["order_by"] != new["order_by"]:
        res["drift"] = {"old_columns": old["columns"], "new_columns": new["columns"],
                        "old_order_by": old["order_by"], "new_order_by": new["order_by"]}
        return res
    res["aggregate_equal"] = old["aggregate_sha256"] == new["aggregate_sha256"]
    o = {key_of(table, r): r for r in old["rows"]}
    n = {key_of(table, r): r for r in new["rows"]}
    res["added"] = [n[k] for k in n if k not in o]
    res["removed"] = sorted(k for k in o if k not in n)
    res["changed"] = {k: {c: [o[k][c], n[k][c]] for c in old["columns"] if o[k][c] != n[k][c]}
                      for k in o if k in n and digest(o[k]) != digest(n[k])}
    return res


base = json.load(open(sys.argv[1]))
cur = json.load(open(sys.argv[2]))
report = {"baseline": sys.argv[1], "checkpoint": sys.argv[2],
          "checkpoint_label": cur.get("label"), "checkpoint_at": cur.get("fetched_at_beijing")}
for db in ("main_db", "sim_db"):
    for table, old in base[db].items():
        if isinstance(old, dict) and "rows" in old and table in cur[db]:
            report[f"{db}.{table}"] = compare(table, old, cur[db][table])
    for table in ("data_source_runs", "valuations"):
        if table in base[db]:
            report[f"{db}.{table}"] = {"old": base[db][table], "new": {k: v for k, v in cur[db][table].items()
                                                                   if k in ("count", "max_rowid")}}
for table in ("idempotency_keys", "account_operations"):
    blk = cur["sim_db"][table]
    report[f"sim_db.{table}"] = {"old_count": base["sim_db"].get(f"{table}_count"),
                                 "new_count": blk["count"], "rows": blk["rows"]}
report["cli_verify"] = json.loads(cur["cli"]["verify"]["stdout"])

if len(sys.argv) > 3:
    open(sys.argv[3], "w").write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")

for name, r in report.items():
    if not isinstance(r, dict):
        print(f"{name}: {r}")
    elif "aggregate_equal" in r:
        flag = "SAME" if r["aggregate_equal"] else "DIFF"
        print(f"{name}: {flag} {r['old_count']}→{r['new_count']} added={len(r['added'])} "
              f"removed={len(r['removed'])} changed={len(r['changed'])}")
        for k, ch in r["changed"].items():
            print(f"    ~ {k}: {ch}")
        for row in r["added"]:
            short = {c: row[c] for c in list(row)[:8]}
            print(f"    + {short}")
    elif "drift" in r:
        print(f"{name}: COLUMN DRIFT {r['drift']}")
    else:
        print(f"{name}: {json.dumps(r, ensure_ascii=False)[:400]}")
