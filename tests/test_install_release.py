from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "deploy/install-release.sh").read_text()


class InstallReleaseTest(unittest.TestCase):
    def test_failed_releases_preserve_healthy_rollback_target(self) -> None:
        # 在临时目录执行实际切换段；跳过依赖安装，替代 systemd 与上游服务。
        switch = SCRIPT[SCRIPT.index("# 当前版本不健康"):]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            for name in ("A", "B", "C", "D", "E"):
                (base / name).mkdir()
            (base / "current").symlink_to(base / "A")
            prefix = '''set -euo pipefail
sudo() { return 0; }
systemctl() { echo loaded; }
sleep() { :; }
curl() { [[ $(readlink "$base/current") == "$base/A" || "$healthy_new" == yes ]]; }
mv() { "$test_python" -c 'import os,sys; os.replace(sys.argv[1], sys.argv[2])' "$2" "$3"; }
'''

            def deploy(name: str, healthy: bool = False) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["bash", "-c", prefix + switch],
                    env={**os.environ, "base": str(base), "release": str(base / name),
                         "release_id": name, "test_python": sys.executable,
                         "healthy_new": "yes" if healthy else "no"},
                    capture_output=True, text=True, timeout=10, check=False,
                )

            result = deploy("B")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("Startup check failed", result.stderr)
            for name in ("C", "D"):
                result = deploy(name)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("Current release is unhealthy", result.stderr)
                self.assertEqual((base / "current").resolve(), base / "B")
                self.assertEqual((base / "previous").resolve(), base / "A")
            (base / "current").unlink()
            (base / "current").symlink_to(base / "A")
            result = deploy("E", healthy=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((base / "current").resolve(), base / "E")
            self.assertEqual((base / "previous").resolve(), base / "A")

    def test_health_requests_timeout_on_hanging_response(self) -> None:
        commands = re.findall(
            r"curl [^\n;]*?http://127\.0\.0\.1:8765/api/funds",
            SCRIPT + (ROOT / "README.md").read_text(),
        )
        self.assertGreaterEqual(len(commands), 4)
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            server.settimeout(10)
            done = threading.Event()

            def hang() -> None:
                with server.accept()[0]:
                    done.wait(10)

            worker = threading.Thread(target=hang, daemon=True)
            worker.start()
            try:
                # 所有启动、回滚检查必须使用相同的有限超时；实际验证一次挂起响应。
                self.assertEqual(len(set(commands)), 1)
                args = shlex.split(commands[0])
                self.assertEqual(args[args.index("--connect-timeout") + 1], "2")
                self.assertEqual(args[args.index("--max-time") + 1], "5")
                args[-1] = f"http://127.0.0.1:{server.getsockname()[1]}/api/funds"
                result = subprocess.run(args + ["--noproxy", "*"], capture_output=True, timeout=8, check=False)
                self.assertEqual(result.returncode, 28, result.stderr)
            finally:
                done.set()
                worker.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
