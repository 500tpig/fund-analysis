from __future__ import annotations

import os
from pathlib import Path


DEFAULT_DB_PATH = Path("data/fund_analysis.db")

# 写请求可信 Host 名（不含端口）。loopback 覆盖本机与 SSH 隧道；
# Cloudflare Tunnel 子域和局域网主机名必须由环境变量显式追加。
DEFAULT_WRITE_HOSTS = ("127.0.0.1", "localhost", "::1")
WRITE_HOSTS_ENV = "FUND_ANALYSIS_WRITE_HOSTS"


def resolve_write_hosts() -> tuple[str, ...]:
    """返回可信 Host 名：内置 loopback，加上环境变量列出的主机名。"""
    hosts = list(DEFAULT_WRITE_HOSTS)
    for item in os.environ.get(WRITE_HOSTS_ENV, "").split(","):
        name = item.strip().lower()
        if name and name not in hosts:
            hosts.append(name)
    return tuple(hosts)
