from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from fund_analysis.web.app import create_app
from fund_analysis.web.storage import LocalStorage


class WebAppTest(unittest.TestCase):
    def test_import_does_not_create_default_database(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
            result = subprocess.run(
                [sys.executable, "-c", "import fund_analysis.web.app"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(root) / "data" / "fund_analysis.db").exists())

    def test_spa_fallback_api_404_and_legacy_redirects(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            dist = root_path / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<main>valuation spa</main>", encoding="utf-8")
            with patch("fund_analysis.web.app.DIST_DIR", dist):
                client = TestClient(create_app(storage=LocalStorage(root_path / "test.db")))
                for canonical_path in ("/valuation", "/holdings", "/import", "/unknown"):
                    self.assertIn("valuation spa", client.get(canonical_path).text)
                self.assertEqual(client.get("/api").status_code, 404)
                self.assertEqual(client.get("/api/unknown").status_code, 404)
                expected_redirects = {
                    "/": "/valuation",
                    "/funds": "/valuation",
                    "/assets/holdings": "/holdings",
                    "/trans/import": "/import",
                    "/review/reports": "/valuation",
                    "/ai-runs": "/valuation",
                }
                for source, target in expected_redirects.items():
                    redirect = client.get(source, follow_redirects=False)
                    self.assertEqual(redirect.status_code, 308)
                    self.assertEqual(redirect.headers["location"], target)
                client.close()


if __name__ == "__main__":
    unittest.main()
