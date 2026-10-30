"""D4 轻量只读探测：账户状态、订单与运行计数、目标订单状态、估值行数。"""
import json, sqlite3
from datetime import datetime, timezone

sim = sqlite3.connect("file:///var/lib/fund-analysis/simulation.db?mode=ro", uri=True)
one = lambda q: sim.execute(q).fetchone()
acct = one("select status, pause_reason, version from accounts")
order = one("select status, version, confirmed_shares, fee, rounding_delta, confirmed_at "
            "from orders where id='ord_6f34daea96394a7e905d'")
print(json.dumps({
    "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "account": acct,
    "orders": one("select count(*) from orders")[0],
    "runs": one("select count(*) from runs")[0],
    "order_events": one("select count(*) from order_events")[0],
    "ledger_events": one("select count(*) from ledger_events")[0],
    "lots": one("select count(*) from lots")[0],
    "valuations": one("select count(*), max(observed_at) from valuations"),
    "ord_6f34": order,
}, ensure_ascii=False))
