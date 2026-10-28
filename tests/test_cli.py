from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from fund_analysis.cli import build_parser, main
from fund_analysis.web.storage import LocalStorage


class CliTest(unittest.TestCase):
    def test_only_web_and_import_commands_are_registered(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["web"]).command, "web")
        self.assertEqual(parser.parse_args(["web"]).port, 8765)
        self.assertEqual(parser.parse_args(["collect", "--scheduled"]).port, 8765)
        self.assertEqual(
            parser.parse_args(["import", "--portfolio", "portfolio.csv"]).command,
            "import",
        )
        with self.assertRaises(SystemExit):
            parser.parse_args(["llm-analyze"])

    def test_import_replaces_holdings(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            csv_path = root_path / "portfolio.csv"
            db_path = root_path / "fund.db"
            csv_path.write_text("code,name,amount,cost\n110011,测试基金,1000,900\n", encoding="utf-8")
            self.assertEqual(main(["import", "--db-path", str(db_path), "--portfolio", str(csv_path)]), 0)
            rows = LocalStorage(db_path).get_all_holdings()
            self.assertEqual([(row["code"], row["amount"]) for row in rows], [("110011", 1000.0)])

    def test_invalid_import_does_not_create_database(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            csv_path = root_path / "bad.csv"
            db_path = root_path / "fund.db"
            csv_path.write_text("code,name\n110011,测试基金\n", encoding="utf-8")
            self.assertEqual(main(["import", "--db-path", str(db_path), "--portfolio", str(csv_path)]), 2)
            self.assertFalse(db_path.exists())

    def test_collect_uses_local_service_and_reports_partial_failure(self) -> None:
        with patch("fund_analysis.cli.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps({
                "message": "partial", "has_failure": True,
                "quote_failures": [{"code": "AU9999", "error": "timeout"}],
            }).encode())
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(["collect", "--scheduled"]), 1)
                self.assertIn("timeout", output.getvalue())
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:8765/api/funds/refresh?scheduled=true")
            self.assertEqual(request.method, "POST")
            opener.return_value.open.side_effect = URLError("service unavailable")
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main(["collect"]), 1)
                self.assertEqual(main(["collect", "--port", "0"]), 2)

    def test_web_pins_proxy_trust_to_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch("uvicorn.run") as run:
            with patch.dict("os.environ", {"FORWARDED_ALLOW_IPS": "*"}):
                self.assertEqual(main(["web", "--db-path", str(Path(root) / "test.db")]), 0)
            self.assertEqual(run.call_args.kwargs["forwarded_allow_ips"], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
