"""服务端采集窗口；时间槽复用数据源运行记录，不增加调度表。"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .market_quotes import market_session
from .trading_calendar import CALENDAR_CN_STOCK, CALENDAR_HK_STOCK, is_trading_day


def collection_slot(now: datetime) -> datetime | None:
    """返回当前采集时间槽起点。未知日历报错，不猜交易日。"""
    if now.tzinfo is None:
        raise ValueError("collection time must include timezone")
    local = now.astimezone(ZoneInfo("Asia/Shanghai"))
    clock = local.time()
    domestic = is_trading_day(CALENDAR_CN_STOCK, local.date())
    interval = None
    if domestic:
        if time(9, 30) <= clock < time(11, 35) or time(13) <= clock < time(15, 35):
            interval = 5
        elif time(19) <= clock:
            interval = 10
        elif time(8) <= clock < time(8, 5) or (
            16 <= local.hour <= 18 and local.minute < 5
        ):
            interval = 5
    # 前一个国内交易日的跨午夜披露补刷，包括周六与长假第一天。
    if time(0, 10) <= clock < time(0, 15) and is_trading_day(
        CALENDAR_CN_STOCK, local.date() - timedelta(days=1)
    ):
        interval = 5
    if interval is None and (
        market_session("NDX", now)[0] == "trading"
        or (
            is_trading_day(CALENDAR_HK_STOCK, local.date())
            and (time(9, 30) <= clock < time(12, 5) or time(13) <= clock < time(16, 5))
        )
    ):
        interval = 30
    if interval is None:
        return None
    return local.replace(minute=local.minute // interval * interval, second=0, microsecond=0)
