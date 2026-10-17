from __future__ import annotations

import configparser
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

from fund_analysis.simulation.service import DAILY_CLOSE_SLOT, SETTLE_SLOTS
from fund_analysis.simulation.strategy import STRATEGY_TIME, STRATEGY_WINDOW_END

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


class SimulationTimerTest(unittest.TestCase):
    """模拟调度 unit 的静态约束。

    本机与 CI 不一定有 systemd，因此这里只做文本与语义检查；实际
    `systemd-analyze verify` 由 install-release.sh 在服务器上执行。
    """

    SERVICE = ROOT / "deploy/systemd/fund-analysis-simulation.service"
    TIMER = ROOT / "deploy/systemd/fund-analysis-simulation.timer"

    def unit(self, path: Path) -> dict[str, dict[str, str]]:
        config = configparser.ConfigParser(strict=False, interpolation=None)
        config.read(path, encoding="utf-8")
        return {section: dict(config[section]) for section in config.sections()}

    def test_timer_fires_often_enough_for_the_strategy_window(self) -> None:
        """Timer 必须以每 5 分钟为上限、整点对齐地触发。

        策略窗口是周三 [`STRATEGY_TIME`, `STRATEGY_WINDOW_END`)，而 service 层的
        `SETTLE_SLOTS` 只覆盖五个结算时刻、不含 14:00。tick 的周期处理虽然无条件
        执行，但只要 timer 不在这十分钟内触发，周期就会全部落成 `missed`。
        这里只接受 `*:<offset>/<step>:00` 形式的分钟网格；改成固定时刻列表会直接失败。
        """
        timer = self.unit(self.TIMER)["Timer"]
        self.assertEqual(timer["unit"], "fund-analysis-simulation.service")
        oncalendar = timer["oncalendar"]
        fields = oncalendar.split()
        self.assertEqual(len(fields), 3, f"OnCalendar 应为「日期 时间 时区」三段：{oncalendar!r}")
        date_field, time_field, tz_field = fields
        self.assertEqual((date_field, tz_field), ("*-*-*", "Asia/Shanghai"), oncalendar)
        match = re.fullmatch(r"\*:(\d+)/(\d+):(\d+)", time_field)
        self.assertIsNotNone(
            match,
            f"时间字段必须是整点对齐的分钟网格（如 *:00/5:00），当前为 {time_field!r}；"
            "固定时刻列表（如 14:00）会漏掉部分策略窗口或结算 slot",
        )
        assert match is not None
        offset, step, second = (int(group) for group in match.groups())
        self.assertEqual(offset, 0, "分钟偏移必须为 0，否则会错过整点结算 slot")
        self.assertEqual(second, 0, "秒偏移必须为 0，否则会错过整点结算 slot")
        self.assertLessEqual(step, 5, "触发间隔过大，无法覆盖 10 分钟的策略窗口")
        self.assertEqual(60 % step, 0, "步长必须整除 60，否则每小时网格会在整点错位")

        # 网格必须真的落在策略窗口内，并覆盖全部结算 slot
        fires = {hour * 60 + minute for hour in range(24) for minute in range(offset, 60, step)}
        start = STRATEGY_TIME.hour * 60 + STRATEGY_TIME.minute
        end = STRATEGY_WINDOW_END.hour * 60 + STRATEGY_WINDOW_END.minute
        self.assertTrue(
            [fire for fire in fires if start <= fire < end],
            f"没有任何触发落在 [{start}, {end}) 内",
        )
        for slot in (*SETTLE_SLOTS, DAILY_CLOSE_SLOT):
            self.assertIn(slot[0] * 60 + slot[1], fires, f"结算 slot {slot} 未被 timer 覆盖")

    def test_service_uses_absolute_paths_and_no_time_override(self) -> None:
        service = self.unit(self.SERVICE)["Service"]
        argv = shlex.split(service["execstart"])
        # install-release.sh 用 sed 把 current 替换成 release 目录，前缀必须保持不变
        self.assertTrue(argv[0].startswith("/opt/fund-analysis/current/"), argv[0])
        self.assertEqual(argv[1], "simulation")
        self.assertEqual(argv[-1], "tick")
        args = dict(zip(argv[2::2], argv[3::2]))
        # 生产库拒绝指定时间，timer 绝不能传 --at
        self.assertNotIn("--at", argv)
        # 两个库路径都要显式给出：--main-db 的默认值是相对路径，依赖工作目录
        self.assertTrue(args["--sim-db"].startswith("/"))
        self.assertTrue(args["--main-db"].startswith("/"))
        self.assertNotEqual(args["--sim-db"], args["--main-db"])
        self.assertEqual(service["workingdirectory"], "/opt/fund-analysis/current")
        # Environment 允许重复出现，configparser 只保留最后一条，因此检查原始文本
        raw = self.SERVICE.read_text(encoding="utf-8")
        self.assertIn("Environment=TZ=Asia/Shanghai", raw)

    def test_timer_is_not_auto_enabled_by_deploy(self) -> None:
        """模拟调度不随发布首次启用，但已启用过时必须恢复。"""
        self.assertNotIn(
            "systemctl enable --now fund-analysis-simulation.timer",
            SCRIPT,
            "阶段 7.5 的真实自动运行门槛需要人工确认，发布脚本不得首次启用该 timer",
        )
        self.assertIn("fund-analysis-simulation.timer", SCRIPT)  # 至少要在停止列表里
        self.assertIn("UnitFileState", SCRIPT)  # 且有恢复已启用 timer 的分支


if __name__ == "__main__":
    unittest.main()
