from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from fund_analysis.web.app import create_app
from fund_analysis.web.app import _host_name as host_name
from fund_analysis.web.app import _normalize_write_hosts as normalize_write_hosts
from fund_analysis.web.defaults import DEFAULT_WRITE_HOSTS


class WriteOriginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.main = Path(self.tmp.name) / "main.db"
        self.sim = Path(self.tmp.name) / "simulation.db"
        self.app = create_app(
            self.main,
            simulation_db_path=self.sim,
            write_origin_hosts=("testserver",),
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.tmp.cleanup()

    def test_cross_origin_is_rejected_before_body_storage_or_upstream(self) -> None:
        # 从当前路由枚举，避免新写入口漏掉统一检查；PATCH 当前没有业务路由。
        targets = [
            (
                method,
                route.path.replace("{holding_id}", "1")
                .replace("{account_id}", "acct_x")
                .replace("{order_id}", "ord_x"),
            )
            for route in self.app.routes
            for method in getattr(route, "methods", set())
            & {"POST", "PUT", "PATCH", "DELETE"}
        ]
        targets.append(("PATCH", "/api/holdings/1"))
        origins = (
            "https://evil.example",
            "null",
            "https://testserver",
            "http://testserver:81",
            "http://testserver.evil.example",
            "http://evil.testserver",
            "http://testserver@evil.example",
            "http://testserver/",
            "http://testserver http://evil.example",
            "",
        )
        with (
            patch("fund_analysis.web.app._refresh_all") as refresh,
            patch("fund_analysis.web.app.recognize_candidates") as recognize,
            patch(
                "starlette.requests.Request.body",
                side_effect=AssertionError("body read"),
            ),
            patch(
                "starlette.requests.Request.form",
                side_effect=AssertionError("form read"),
            ),
        ):
            for method, path in targets:
                for origin in origins:
                    for content_type in (
                        "text/plain",
                        "application/x-www-form-urlencoded",
                        "multipart/form-data; boundary=x",
                    ):
                        with self.subTest(
                            method=method,
                            path=path,
                            origin=origin,
                            content_type=content_type,
                        ):
                            response = self.client.request(
                                method,
                                path,
                                content='{"code":"110011","name":"probe"}',
                                headers={
                                    "Origin": origin,
                                    "Content-Type": content_type,
                                },
                            )
                            self.assertEqual(response.status_code, 403, response.text)
                            self.assertNotIn(
                                "access-control-allow-origin", response.headers
                            )
            refresh.assert_not_called()
            recognize.assert_not_called()
        self.assertIsNone(self.app.state.storage)
        self.assertIsNone(self.app.state.simulation_service)
        self.assertFalse(self.main.exists())
        self.assertFalse(self.sim.exists())

    def test_missing_origin_checks_browser_metadata_and_referer(self) -> None:
        for headers in (
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
            {"Sec-Fetch-Site": "none"},
            {"Sec-Fetch-Site": "invalid"},
            {"Referer": "https://evil.example/form"},
            {"Referer": "http://testserver:81/form"},
            {"Referer": "http://[broken"},
            {"Referer": "null"},
            {"Referer": ""},
            {"Cookie": "session=fixture"},
            {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.client.post("/api/funds/refresh", headers=headers).status_code,
                    403,
                )
        with patch(
            "fund_analysis.web.app._refresh_all", return_value={"message": "fixture"}
        ) as refresh:
            for headers in (
                {},
                {"Sec-Fetch-Site": "same-origin", "Cookie": "session=fixture"},
                {"Referer": "http://testserver/valuation", "Cookie": "session=fixture"},
            ):
                self.assertEqual(
                    self.client.post("/api/funds/refresh", headers=headers).status_code,
                    200,
                )
            self.assertEqual(refresh.call_count, 3)

    def test_same_origin_crud_and_duplicate_origin(self) -> None:
        headers = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
        payload = {"code": "110011", "name": "fixture", "amount": 100}
        response = self.client.post("/api/holdings", json=payload, headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        path = f"/api/holdings/{response.json()['id']}"
        self.assertEqual(
            self.client.put(
                path, json={**payload, "amount": 200}, headers=headers
            ).status_code,
            200,
        )
        self.assertEqual(self.app.state.storage.get_all_holdings()[0]["amount"], 200)
        self.assertEqual(self.client.delete(path, headers=headers).status_code, 200)
        self.assertEqual(self.app.state.storage.get_all_holdings(), [])
        duplicate = self.client.post(
            "/api/holdings",
            json=payload,
            headers=[
                ("Origin", "http://testserver"),
                ("Origin", "https://evil.example"),
            ],
        )
        self.assertEqual(duplicate.status_code, 403)
        repeated = self.client.post(
            "/api/holdings",
            json=payload,
            headers=[
                ("Origin", "http://testserver"),
                ("Origin", "http://testserver"),
            ],
        )
        self.assertEqual(repeated.status_code, 201, repeated.text)

    def test_write_hosts_default_to_loopback_and_env_adds_tunnel_host(self) -> None:
        with patch.dict("os.environ", {"FUND_ANALYSIS_WRITE_HOSTS": "fund.example"}):
            app = create_app(self.main, simulation_db_path=self.sim)
            with TestClient(app, base_url="http://127.0.0.1:8765") as loopback:
                self.assertEqual(
                    loopback.post(
                        "/api/holdings",
                        json={"code": "110011", "name": "fixture"},
                        headers={"Origin": "http://127.0.0.1:8765"},
                    ).status_code,
                    201,
                )
            with TestClient(app, base_url="http://localhost:8765") as named:
                # collect CLI 形态：无来源头、Host 为本机名，继续放行
                self.assertEqual(
                    named.post(
                        "/api/holdings", json={"code": "110012", "name": "fixture"}
                    ).status_code,
                    201,
                )
            with TestClient(app, base_url="http://fund.example") as tunnel:
                self.assertEqual(
                    tunnel.post(
                        "/api/holdings",
                        json={"code": "110013", "name": "fixture"},
                        headers={"Origin": "http://fund.example"},
                    ).status_code,
                    201,
                )

        app = create_app(self.main, simulation_db_path=self.sim)
        # 未配置的主机名：即使 Host 与 Origin 一致也拒绝，覆盖 DNS rebinding 形态。
        for headers in (
            {"Origin": "http://evil.example"},
            {"Origin": "http://evil.example", "Sec-Fetch-Site": "same-origin"},
            {},
        ):
            with self.subTest(headers=headers):
                with TestClient(app, base_url="http://evil.example") as attacker:
                    self.assertEqual(
                        attacker.post(
                            "/api/holdings",
                            json={"code": "110099", "name": "rebound"},
                            headers=headers,
                        ).status_code,
                        403,
                    )

    def test_host_names_match_request_and_config_forms(self) -> None:
        """请求 Host 与配置项都归一到同一个主机名口径，含端口与 IPv6 方括号。"""
        self.assertEqual(host_name("127.0.0.1:8765"), "127.0.0.1")
        self.assertEqual(host_name("localhost:5173"), "localhost")
        self.assertEqual(host_name("[::1]:8765"), "::1")
        self.assertEqual(host_name("FUND.example"), "fund.example")
        self.assertIsNone(host_name(""))
        self.assertIsNone(host_name("127.0.0.1/x"))
        self.assertEqual(
            normalize_write_hosts(DEFAULT_WRITE_HOSTS),
            frozenset({"127.0.0.1", "localhost", "::1"}),
        )
        self.assertEqual(
            normalize_write_hosts(("fund.example:443", "::1")),
            frozenset({"fund.example", "::1"}),
        )
        # 非法写法必须整体拒绝，不能被截断成另一个主机名
        for text in (
            "fund.example?other",
            "fund.example#other",
            "fund.example:bad",
            "fund.example:99999",
            "fund.example:0",
            "fund.example:",
            "fund.example\\other",
            "fund.example\x00",
            "fund example",
            "[::1",
            "[not-ip]:80",
            "fund.example]",
        ):
            with self.subTest(text=text):
                self.assertIsNone(host_name(text))

    def test_invalid_configured_write_host_fails_fast(self) -> None:
        for hosts in (
            ("http://fund.example",),
            ("*.fund.example",),
            ("",),
            ("fund.example?other",),
            ("fund.example#other",),
            ("fund.example:bad",),
            ("fund.example:99999",),
            ("fund.example\\other",),
            ("fund.example\x00",),
        ):
            with self.subTest(hosts=hosts):
                with self.assertRaises(ValueError):
                    create_app(
                        self.main,
                        simulation_db_path=self.sim,
                        write_origin_hosts=hosts,
                    )

    def test_proxy_scheme_requires_trusted_peer_and_preserves_host(self) -> None:
        # 隧道主机名来自显式配置：可信代理还原 scheme，不可信代理伪造转发头无效。
        tunnel_app = create_app(
            self.main,
            simulation_db_path=self.sim,
            write_origin_hosts=("fund.example",),
        )
        proxied = ProxyHeadersMiddleware(tunnel_app, trusted_hosts="127.0.0.1")
        headers = {"Origin": "https://fund.example", "X-Forwarded-Proto": "https"}
        with patch(
            "fund_analysis.web.app._refresh_all", return_value={"message": "fixture"}
        ):
            with TestClient(
                proxied, base_url="http://fund.example", client=("127.0.0.1", 1234)
            ) as trusted:
                self.assertEqual(
                    trusted.post("/api/funds/refresh", headers=headers).status_code, 200
                )
                self.assertEqual(
                    trusted.post(
                        "/api/funds/refresh",
                        headers={**headers, "Origin": "http://fund.example"},
                    ).status_code,
                    403,
                )
            with TestClient(
                proxied, base_url="http://fund.example", client=("192.0.2.1", 1234)
            ) as untrusted:
                self.assertEqual(
                    untrusted.post("/api/funds/refresh", headers=headers).status_code,
                    403,
                )
                forged_host = {
                    "Origin": "http://evil.example",
                    "X-Forwarded-Host": "evil.example",
                    "Forwarded": "host=evil.example;proto=https",
                }
                self.assertEqual(
                    untrusted.post(
                        "/api/funds/refresh", headers=forged_host
                    ).status_code,
                    403,
                )


if __name__ == "__main__":
    unittest.main()
