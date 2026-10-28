from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from fund_analysis.simulation.acceptance import FIXTURE_CODE, at
from fund_analysis.simulation.rules import FIXTURE_QDII_C_V1
from fund_analysis.simulation.service import SimulationService
from fund_analysis.simulation.store import SimulationStore
from fund_analysis.web.app import create_app
from fund_analysis.web.storage import LocalStorage


# 测试客户端使用假 Host（testserver），显式列入可信写来源。
_WRITE_HOSTS = patch.dict(os.environ, {"FUND_ANALYSIS_WRITE_HOSTS": "testserver"})


def setUpModule() -> None:
    _WRITE_HOSTS.start()


def tearDownModule() -> None:
    _WRITE_HOSTS.stop()


class SimulationApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.main = root / "fund_analysis.db"
        self.sim_path = root / "simulation.db"
        self.service = SimulationService(SimulationStore(self.sim_path, environment="fixture"))
        self.service.import_rule(FIXTURE_QDII_C_V1)
        self.client = TestClient(
            create_app(self.main, storage=LocalStorage(self.main), simulation_db_path=self.sim_path)
        )

    def tearDown(self) -> None:
        self.client.close()
        self._tmp.cleanup()

    def create(self, key: str = "init") -> dict:
        response = self.client.post("/api/simulation/accounts", headers={"Idempotency-Key": key})
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    def test_account_lifecycle_and_error_shape(self) -> None:
        listed = self.client.get("/api/simulation/accounts").json()
        self.assertEqual((listed["environment"], listed["items"]), ("fixture", []))
        missing_key = self.client.post("/api/simulation/accounts")
        self.assertEqual(missing_key.status_code, 422)
        self.assertEqual(missing_key.json()["detail"]["code"], "missing_idempotency_key")
        first = self.client.post("/api/simulation/accounts", headers={"Idempotency-Key": "init"})
        self.assertEqual(first.status_code, 201)
        replay = self.client.post("/api/simulation/accounts", headers={"Idempotency-Key": "init"})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["id"], first.json()["id"])
        conflict = self.client.post("/api/simulation/accounts", headers={"Idempotency-Key": "other"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            set(conflict.json()["detail"]), {"code", "message", "fields"}
        )
        account = self.client.get(f"/api/simulation/accounts/{first.json()['id']}").json()
        self.assertEqual(account["balances"]["available"], "200000.00")
        self.assertIsInstance(account["balances"]["available"], str)
        not_found = self.client.get("/api/simulation/accounts/acct_missing")
        self.assertEqual((not_found.status_code, not_found.json()["detail"]["code"]), (404, "account_not_found"))

    def test_no_http_order_entry(self) -> None:
        account = self.create()
        response = self.client.post(
            f"/api/simulation/accounts/{account['id']}/orders",
            headers={"Idempotency-Key": "k"},
            json={"code": FIXTURE_CODE, "side": "buy", "amount": "500.00"},
        )
        self.assertIn(response.status_code, (404, 405))

    def fixture_account(self) -> dict:
        # fixture 库的读取时钟沿用最近一次样例时间，因此账户也用样例时间创建。
        return self.service.create_account("init", now=at(2026, 10, 14, 9))[0]

    def test_orders_positions_pagination_cancel_and_pause(self) -> None:
        account = self.fixture_account()
        account_id = account["id"]
        self.service.record_nav(FIXTURE_CODE, date(2026, 10, 9), "1.2000", now=at(2026, 10, 13, 18, 30))
        order_ids = []
        for index in range(3):
            order, _ = self.service.submit_order(
                account_id, f"k{index}", side="buy", code=FIXTURE_CODE, amount="100.00",
                now=at(2026, 10, 14, 10, index),
            )
            order_ids.append(order["id"])
        page = self.client.get(f"/api/simulation/accounts/{account_id}/orders", params={"limit": 2}).json()
        self.assertEqual([item["id"] for item in page["items"]], order_ids[::-1][:2])
        rest = self.client.get(
            f"/api/simulation/accounts/{account_id}/orders",
            params={"limit": 2, "cursor": page["next_cursor"]},
        ).json()
        self.assertEqual([item["id"] for item in rest["items"]], [order_ids[0]])
        self.assertIsNone(rest["next_cursor"])
        filtered = self.client.get(
            f"/api/simulation/accounts/{account_id}/orders", params={"status": "settled"}
        ).json()
        self.assertEqual(filtered["items"], [])
        bad = self.client.get(f"/api/simulation/accounts/{account_id}/orders", params={"limit": 500})
        self.assertEqual(bad.status_code, 422)

        detail = self.client.get(f"/api/simulation/orders/{order_ids[0]}").json()
        self.assertTrue(detail["cancellable"])
        self.assertEqual(detail["events"][-1]["to_status"], "awaiting_nav")
        self.assertEqual(detail["rule"]["environment"], "fixture")
        stale = self.client.post(
            f"/api/simulation/orders/{order_ids[0]}/cancel",
            headers={"Idempotency-Key": "c1"},
            json={"expected_version": 99},
        )
        self.assertEqual((stale.status_code, stale.json()["detail"]["code"]), (409, "version_conflict"))
        cancelled = self.client.post(
            f"/api/simulation/orders/{order_ids[0]}/cancel",
            headers={"Idempotency-Key": "c2"},
            json={"expected_version": detail["version"]},
        )
        self.assertEqual(cancelled.json()["status"], "cancelled")
        replay = self.client.post(
            f"/api/simulation/orders/{order_ids[0]}/cancel",
            headers={"Idempotency-Key": "c2"},
            json={"expected_version": detail["version"]},
        )
        self.assertEqual(replay.status_code, 200)

        self.service.record_nav(FIXTURE_CODE, date(2026, 10, 14), "1.2500", now=at(2026, 10, 15, 20))
        self.service.settle(account_id, now=at(2026, 10, 16, 18))
        positions = self.client.get(f"/api/simulation/accounts/{account_id}/positions").json()["items"]
        self.assertEqual(positions[0]["shares"], "160.00")
        self.assertEqual(positions[0]["value"], "200.00")

        version = self.client.get(f"/api/simulation/accounts/{account_id}").json()["version"]
        bad_body = self.client.post(
            f"/api/simulation/accounts/{account_id}/pause",
            headers={"Idempotency-Key": "p1"},
            json={"expected_version": version, "extra": 1},
        )
        self.assertEqual(bad_body.status_code, 422)
        paused = self.client.post(
            f"/api/simulation/accounts/{account_id}/pause",
            headers={"Idempotency-Key": "p1"},
            json={"expected_version": version},
        ).json()
        self.assertEqual(paused["status"], "paused")
        resumed = self.client.post(
            f"/api/simulation/accounts/{account_id}/resume",
            headers={"Idempotency-Key": "r1"},
            json={"expected_version": paused["version"]},
        )
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["status"], "active")

    def test_write_endpoints_reject_simple_cross_site_requests(self) -> None:
        """统一来源检查先于模拟写接口的幂等键与业务校验。"""
        account = self.fixture_account()
        account_id = account["id"]
        before = self.client.get(f"/api/simulation/accounts/{account_id}").json()
        simple_types = (
            "text/plain;charset=UTF-8",
            "application/x-www-form-urlencoded",
            "multipart/form-data; boundary=x",
        )
        targets = (
            f"/api/simulation/accounts/{account_id}/pause",
            f"/api/simulation/accounts/{account_id}/resume",
            "/api/simulation/orders/ord_x/cancel",
            "/api/simulation/accounts",
        )
        for path in targets:
            for content_type in simple_types:
                with self.subTest(path=path, content_type=content_type):
                    response = self.client.post(
                        path,
                        content=f'{{"expected_version": {before["version"]}}}',
                        headers={"Origin": "https://evil.example", "Content-Type": content_type},
                    )
                    self.assertEqual(response.status_code, 403, response.text)
                    # 应用没有 CORS 中间件：跨站页面读不到响应，也无从通过预检
                    self.assertNotIn("access-control-allow-origin", response.headers)
        preflight = self.client.options(
            f"/api/simulation/accounts/{account_id}/pause",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "idempotency-key,content-type",
            },
        )
        self.assertEqual(preflight.status_code, 405)
        self.assertNotIn("access-control-allow-origin", preflight.headers)
        after = self.client.get(f"/api/simulation/accounts/{account_id}").json()
        self.assertEqual((after["status"], after["version"]), (before["status"], before["version"]))
        self.assertEqual(len(self.client.get("/api/simulation/accounts").json()["items"]), 1)

    def test_reads_do_not_initialize_or_settle(self) -> None:
        account = self.fixture_account()
        self.service.record_nav(FIXTURE_CODE, date(2026, 10, 9), "1.2000", now=at(2026, 10, 13, 18, 30))
        order, _ = self.service.submit_order(
            account["id"], "k", side="buy", code=FIXTURE_CODE, amount="100.00", now=at(2026, 10, 14, 10)
        )
        self.service.record_nav(FIXTURE_CODE, date(2026, 10, 14), "1.2500", now=at(2026, 10, 20, 20))
        for _ in range(2):
            self.client.get(f"/api/simulation/accounts/{account['id']}")
            self.client.get(f"/api/simulation/orders/{order['id']}")
        self.assertEqual(self.client.get(f"/api/simulation/orders/{order['id']}").json()["status"], "awaiting_nav")


if __name__ == "__main__":
    unittest.main()
