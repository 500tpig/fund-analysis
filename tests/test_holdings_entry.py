from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

from fund_analysis.web.app import create_app
from fund_analysis.web.holdings_entry import (
    HoldingsRecognitionError,
    StructuredHoldingsRecognizer,
    StructuredOcrConfig,
)
from fund_analysis.web.storage import LocalStorage


def image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "white").save(output, format="PNG")
    return output.getvalue()


class FakeRecognizer:
    configured = True
    provider = "structured_vlm"
    model = "fixture-vlm"

    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls: list[int] = []

    def recognize_image(self, image: bytes, image_index: int):
        self.calls.append(image_index)
        result = self.results[image_index]
        if isinstance(result, Exception):
            raise result
        return result


class UnconfiguredRecognizer(FakeRecognizer):
    configured = False


class FailingMarketProvider:
    def fetch_without_quarterly_holdings(self, code: str):
        raise RuntimeError("fixture refresh failure")


class EmptyEstimateProvider:
    def fetch_many(self, codes: list[str]):
        return SimpleNamespace(
            estimates={},
            failures={code: "fixture estimate failure" for code in codes},
            evidence={},
        )


class EmptyQuoteProvider:
    name = "fixture_quotes"

    def fetch_all(self):
        return SimpleNamespace(quotes={}, failures={})


def search_funds(keyword: str, limit: int = 5):
    if keyword == "110011":
        return [{"code": "110011", "name": "易方达沪深300ETF联接A"}]
    if keyword == "示例基金":
        return [
            {"code": "000001", "name": "示例基金A"},
            {"code": "000002", "name": "示例基金C"},
        ]
    return []


class HoldingsEntryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.TemporaryDirectory()
        self.storage = LocalStorage(Path(self.root.name) / "test.db")
        self.recognizer = FakeRecognizer(
            [[{"code": "110011", "name": "易方达沪深300ETF联接A", "amount": "1000"}]]
        )
        self.client = self._client(self.recognizer)

    def tearDown(self) -> None:
        self.client.close()
        self.root.cleanup()

    def _client(self, recognizer: object) -> TestClient:
        return TestClient(
            create_app(
                storage=self.storage,
                market_provider=FailingMarketProvider(),
                estimate_provider=EmptyEstimateProvider(),
                market_quote_provider=EmptyQuoteProvider(),
                fund_searcher=search_funds,
                holdings_recognizer=recognizer,
            )
        )

    @staticmethod
    def _files(count: int = 1):
        return [
            ("images", (f"fixture-{index}.png", image_bytes(), "image/png"))
            for index in range(count)
        ]

    def test_recognition_requires_configured_provider_without_writing(self) -> None:
        self.storage.add_holding({"code": "000001", "name": "保留", "amount": 500})
        with self._client(UnconfiguredRecognizer([])) as client:
            response = client.post("/api/holdings/entry/recognize", files=self._files())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "未配置持仓识图服务")
        self.assertEqual([row["code"] for row in self.storage.get_all_holdings()], ["000001"])

    def test_image_count_size_and_format_validation(self) -> None:
        empty = self.client.post("/api/holdings/entry/recognize")
        too_many = self.client.post(
            "/api/holdings/entry/recognize",
            files=self._files(6),
        )
        oversized = self.client.post(
            "/api/holdings/entry/recognize",
            files={
                "images": (
                    "large.png",
                    b"\x89PNG\r\n\x1a\n" + b"0" * (8 * 1024 * 1024),
                    "image/png",
                )
            },
        )
        pdf = self.client.post(
            "/api/holdings/entry/recognize",
            files={"images": ("fixture.pdf", b"%PDF-1.7", "application/pdf")},
        )
        heic = self.client.post(
            "/api/holdings/entry/recognize",
            files={"images": ("fixture.heic", b"\x00\x00\x00\x18ftypheic0000", "image/heic")},
        )
        self.assertEqual((empty.status_code, empty.json()["detail"]), (400, "请选择 1 到 5 张持仓截图"))
        self.assertEqual((too_many.status_code, too_many.json()["detail"]), (400, "一次最多识别 5 张截图"))
        self.assertEqual((oversized.status_code, oversized.json()["detail"]), (413, "单张图片不能超过 8 MB"))
        self.assertEqual((pdf.status_code, pdf.json()["detail"]), (415, "仅支持 JPG、PNG 或 WebP 截图"))
        self.assertEqual((heic.status_code, heic.json()["detail"]), (415, "当前不支持 HEIC，请使用 JPG 或 PNG"))

    def test_recognition_verifies_name_and_merges_conflicting_images(self) -> None:
        recognizer = FakeRecognizer(
            [
                [{"code": "110011", "name": "易方达沪深300ETF联接C", "amount": "1,000.00", "shares": "123.45678"}],
                [{"code": "110011", "name": "易方达沪深300ETF联接C", "amount": "1001.00", "shares": "123.4568"}],
            ]
        )
        with self._client(recognizer) as client:
            response = client.post("/api/holdings/entry/recognize", files=self._files(2))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["candidates"]), 1)
        candidate = payload["candidates"][0]
        self.assertEqual(candidate["name"], "易方达沪深300ETF联接A")
        self.assertEqual(candidate["ocr_name"], "易方达沪深300ETF联接C")
        self.assertIsNone(candidate["amount"])
        self.assertIn("share_class_mismatch", candidate["issues"])
        self.assertIn("amount_conflict", candidate["issues"])
        self.assertEqual(candidate["confidence"], "low")

    def test_name_with_multiple_share_classes_stays_unresolved(self) -> None:
        recognizer = FakeRecognizer([[{"name": "示例基金", "amount": "88.8"}]])
        with self._client(recognizer) as client:
            response = client.post("/api/holdings/entry/recognize", files=self._files())
        candidate = response.json()["candidates"][0]
        self.assertIsNone(candidate["code"])
        self.assertEqual([hit["code"] for hit in candidate["name_matches"]], ["000001", "000002"])
        self.assertIn("share_class_ambiguous", candidate["issues"])

    def test_partial_provider_failure_keeps_successful_candidates(self) -> None:
        recognizer = FakeRecognizer(
            [
                HoldingsRecognitionError(502, "持仓识图服务失败"),
                [{"code": "110011", "name": "基金", "amount": "100"}],
            ]
        )
        with self._client(recognizer) as client:
            response = client.post("/api/holdings/entry/recognize", files=self._files(2))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["candidates"]), 1)
        self.assertIn("第 1 张截图识别失败", response.json()["warnings"][0])

    def test_provider_failure_timeout_invalid_result_and_empty_result_are_explicit(self) -> None:
        cases = [
            (HoldingsRecognitionError(502, "持仓识图服务失败"), 502, "持仓识图服务失败"),
            (HoldingsRecognitionError(504, "持仓识图超时"), 504, "持仓识图超时"),
            ({"funds": []}, 502, "持仓识图结果无法解析"),
            ([], 422, "未识别到基金持仓"),
        ]
        for result, status, detail in cases:
            with self.subTest(status=status, detail=detail), self._client(FakeRecognizer([result])) as client:
                response = client.post("/api/holdings/entry/recognize", files=self._files())
                self.assertEqual((response.status_code, response.json()["detail"]), (status, detail))

    def test_snapshot_profit_round_trip_preserves_cost_and_does_not_double_count(self) -> None:
        self.storage.add_holding({"code": "110011", "name": "测试基金", "amount": "1000", "cost": "1800"})
        item = {
            "code": "110011", "name": "测试基金", "amount": "900.00",
            "imported_holding_profit": "-100.00", "holding_profit_rate": "-10.00",
            "profit_snapshot_date": "2026-10-23", "profit_source": "支付宝",
            "daily_profit": "-12.34", "daily_profit_date": "2026-10-22",
        }
        response = self.client.post("/api/holdings/entry/confirm", json={"mode": "merge", "items": [item]})
        self.assertEqual(response.status_code, 200, response.text)
        raw = self.storage.get_all_holdings()[0]
        self.assertEqual(raw["cost"], 1800)
        result = self.client.get("/api/holdings").json()
        row = result["rows"][0]
        self.assertEqual(row["holding_profit"], -100)
        self.assertEqual(row["holding_profit_amount_source"], "imported_snapshot")
        self.assertEqual(row["profit_snapshot_date"], "2026-10-23")
        self.assertFalse(row["holding_profit_conflict"])
        self.assertTrue(row["imported_daily_profit"]["dated"])
        self.assertEqual(row["imported_daily_profit"]["return_date"], "2026-10-22")
        today = result["summary"]["today"]
        self.assertEqual(today["imported_dated"], [{"return_date": "2026-10-22", "source": "支付宝", "count": 1, "profit": -12.34}])
        self.assertEqual(today["imported_undated"]["count"], 0)
        self.assertIsNone(today["verifiable"]["profit"])

        # 手工改金额使持有收益快照失效，已注明日期的日收益仍是历史事实。
        self.storage.update_holding(raw["id"], {"amount": "901"})
        changed = self.storage.get_all_holdings()[0]
        self.assertIsNone(changed["imported_holding_profit"])
        self.assertIsNone(changed["profit_snapshot_date"])
        self.assertIsNone(changed["holding_profit_rate"])
        self.assertEqual(changed["daily_profit_date"], "2026-10-22")

    def test_profit_snapshot_requires_provenance_and_valid_dates(self) -> None:
        base = {"code": "110011", "name": "测试基金", "amount": "900"}
        for extra in (
            {"imported_holding_profit": "0"},
            {"imported_holding_profit": "NaN", "profit_snapshot_date": "2026-10-23", "profit_source": "支付宝"},
            {"daily_profit_date": "2026-10-22", "profit_source": "支付宝"},
            {"daily_profit": "0", "daily_profit_date": "2026-02-30", "profit_source": "支付宝"},
            {"daily_profit": "0", "daily_profit_date": "2026-10-22garbage", "profit_source": "支付宝"},
        ):
            with self.subTest(extra=extra):
                response = self.client.post("/api/holdings/entry/preview", json={"mode": "merge", "items": [{**base, **extra}]})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.storage.get_all_holdings(), [])

    def test_merge_invalidates_snapshot_without_reconfirming_it(self) -> None:
        data = {"code": "110011", "name": "测试基金", "amount": "900", "cost": "1800", "imported_holding_profit": "0", "profit_snapshot_date": "2026-10-23", "profit_source": "支付宝"}
        self.storage.add_holding(data)
        self.storage.apply_holdings_entry([{"code": "110011", "name": "测试基金", "amount": "901"}], "merge")
        self.assertIsNone(self.storage.get_all_holdings()[0]["imported_holding_profit"])
        self.assertEqual(self.storage.get_all_holdings()[0]["cost"], 1800)

    def test_preview_is_dry_run_and_rejects_duplicates(self) -> None:
        self.storage.add_holding({"code": "000001", "name": "保留基金", "amount": 500})
        payload = {
            "mode": "replace",
            "items": [{"code": "110011", "name": "新基金", "amount": "1000.005"}],
        }
        preview = self.client.post("/api/holdings/entry/preview", json=payload)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["rows"][0]["amount"], "1000.01")
        self.assertEqual(preview.json()["remove_count"], 1)
        self.assertEqual([row["code"] for row in self.storage.get_all_holdings()], ["000001"])

        duplicate = self.client.post(
            "/api/holdings/entry/preview",
            json={"mode": "merge", "items": [payload["items"][0], payload["items"][0]]},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.json()["detail"], "基金代码 110011 在确认列表中重复")

        binary_float = self.client.post(
            "/api/holdings/entry/preview",
            json={
                "mode": "merge",
                "items": [{"code": "110011", "name": "新基金", "amount": 0.1}],
            },
        )
        self.assertEqual(binary_float.status_code, 400)
        self.assertEqual(binary_float.json()["detail"], "基金代码 110011 缺少有效金额")

    def test_merge_adds_updates_and_preserves_unmentioned_and_optional_fields(self) -> None:
        updated_id = self.storage.add_holding(
            {
                "code": "110011",
                "name": "旧名称",
                "amount": 900,
                "shares": "12.3456",
                "daily_profit": 8.5,
                "weight_pct": 40,
                "amount_nav_date": "2026-09-07",
            }
        )
        self.storage.add_holding({"code": "000001", "name": "未出现基金", "amount": 500})
        response = self.client.post(
            "/api/holdings/entry/confirm",
            json={
                "mode": "merge",
                "confirm_remove": False,
                "remove_codes": [],
                "items": [
                    {"code": "110011", "name": "正式名称", "amount": "1000.00"},
                    {"code": "000002", "name": "新增基金", "amount": "200.00", "shares": "23.4567"},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["add_count"], 1)
        self.assertEqual(response.json()["update_count"], 1)
        rows = {row["code"]: row for row in self.storage.get_all_holdings()}
        self.assertEqual(set(rows), {"110011", "000001", "000002"})
        self.assertEqual(rows["110011"]["id"], updated_id)
        self.assertEqual(rows["110011"]["shares"], "12.3456")
        self.assertEqual(rows["110011"]["daily_profit"], 8.5)
        self.assertIsNone(rows["110011"]["weight_pct"])
        self.assertIsNone(rows["110011"]["amount_nav_date"])
        self.assertEqual(rows["000002"]["shares"], "23.4567")

    def test_replace_requires_exact_deletion_confirmation(self) -> None:
        self.storage.add_holding({"code": "000001", "name": "待删除", "amount": 500})
        request = {
            "mode": "replace",
            "confirm_remove": False,
            "remove_codes": ["000001"],
            "items": [{"code": "110011", "name": "保留", "amount": "1000"}],
        }
        unconfirmed = self.client.post("/api/holdings/entry/confirm", json=request)
        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual([row["code"] for row in self.storage.get_all_holdings()], ["000001"])

        stale = self.client.post(
            "/api/holdings/entry/confirm",
            json={**request, "confirm_remove": True, "remove_codes": ["000002"]},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual([row["code"] for row in self.storage.get_all_holdings()], ["000001"])

        confirmed = self.client.post(
            "/api/holdings/entry/confirm",
            json={**request, "confirm_remove": True},
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["remove_count"], 1)
        self.assertEqual([row["code"] for row in self.storage.get_all_holdings()], ["110011"])
        self.assertTrue(confirmed.json()["refresh"]["has_failure"])


class HoldingsEntryStorageTest(unittest.TestCase):
    def test_batch_failure_rolls_back_earlier_updates(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = LocalStorage(Path(root) / "test.db")
            storage.add_holding({"code": "110011", "name": "原名称", "amount": 100})
            items = [
                {"code": "110011", "name": "已修改", "amount": "200.00"},
                {"code": "000001", "name": "新增", "amount": "300.00"},
            ]
            with mock.patch.object(storage, "_insert_holding", side_effect=RuntimeError("boom")):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    storage.apply_holdings_entry(items, "merge")
            rows = storage.get_all_holdings()
            self.assertEqual([(row["code"], row["name"], row["amount"]) for row in rows], [("110011", "原名称", 100.0)])


class StructuredHoldingsRecognizerTest(unittest.TestCase):
    @staticmethod
    def _config() -> StructuredOcrConfig:
        return StructuredOcrConfig("https://example.test/v1", "fixture-key", "fixture-model")

    def test_openai_compatible_response_returns_funds(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                content = json.dumps({"funds": [{"code": "110011", "amount": "100"}]})
                return json.dumps({"choices": [{"message": {"content": content}}]}).encode()

        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return Response()

        recognizer = StructuredHoldingsRecognizer(self._config(), opener=opener)
        funds = recognizer.recognize_image(b"jpeg", 0)
        self.assertEqual(funds, [{"code": "110011", "amount": "100"}])
        body = json.loads(requests[0][0].data)
        self.assertEqual(body["model"], "fixture-model")
        self.assertTrue(body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_provider_and_invalid_json_fail_explicitly(self) -> None:
        def failed(*args, **kwargs):
            raise urllib.error.URLError("offline")

        with self.assertRaisesRegex(HoldingsRecognitionError, "持仓识图服务失败"):
            StructuredHoldingsRecognizer(self._config(), opener=failed).recognize_image(b"jpeg", 0)

        class InvalidResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return b"not-json"

        with self.assertRaisesRegex(HoldingsRecognitionError, "持仓识图结果无法解析"):
            StructuredHoldingsRecognizer(
                self._config(),
                opener=lambda *args, **kwargs: InvalidResponse(),
            ).recognize_image(b"jpeg", 0)


if __name__ == "__main__":
    unittest.main()
