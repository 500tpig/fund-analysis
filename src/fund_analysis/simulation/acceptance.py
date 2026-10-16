"""阶段 6 隔离验收：按 simulation-plan 第 6 节的算例逐笔核对资金与份额。

每个算例使用独立 fixture 模拟库；规则来自第 2.5 节 ``fixture_qdii_c_v1`` 及其明示测试变体，
净值是人造输入。结果证明计算与状态流转，不代表支付宝实际交易规则。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .rules import (
    FIXTURE_QDII_C_V1,
    SHANGHAI,
    UNLIMITED,
    fixture_variant,
    holding_days,
    load_rule,
)
from .service import SimulationError, SimulationService, limit_scaled_amount
from .store import SimulationStore

FIXTURE_CODE = "016665"


def at(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=SHANGHAI)


@dataclass(frozen=True)
class Check:
    case: str
    title: str
    label: str
    expected: str
    actual: str

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


class Case:
    def __init__(self, root: Path, case_id: str, title: str, rules: list[dict[str, Any]] | None = None):
        self.case_id = case_id
        self.title = title
        self.path = root / f"{case_id}.db"
        self.service = SimulationService(SimulationStore(self.path, environment="fixture"))
        for rule in rules or [FIXTURE_QDII_C_V1]:
            self.service.import_rule(rule)
        self.checks: list[Check] = []
        self.account_id = ""

    def reopen(self) -> None:
        """模拟进程重启：丢弃内存对象，重新打开同一模拟库。"""
        self.service = SimulationService(SimulationStore(self.path, environment="fixture"))

    def open_account(self, when: datetime) -> dict[str, Any]:
        account, _ = self.service.create_account("initial", now=when)
        self.account_id = account["id"]
        return account

    def nav(self, code: str, day: date, value: str, observed: datetime) -> None:
        self.service.record_nav(code, day, value, now=observed)

    def buy(self, key: str, code: str, amount: str, when: datetime) -> dict[str, Any]:
        return self.service.submit_order(self.account_id, key, side="buy", code=code, amount=amount, now=when)[0]

    def sell(self, key: str, code: str, shares: str, when: datetime) -> dict[str, Any]:
        return self.service.submit_order(self.account_id, key, side="sell", code=code, shares=shares, now=when)[0]

    def settle(self, when: datetime) -> dict[str, Any]:
        return self.service.settle(self.account_id, now=when).to_dict()

    def view(self) -> dict[str, Any]:
        return self.service.account_view(self.account_id)

    def order(self, order_id: str) -> dict[str, Any]:
        return self.service.order_detail(order_id)

    def check(self, label: str, expected: Any, actual: Any) -> None:
        self.checks.append(Check(self.case_id, self.title, label, str(expected), str(actual)))

    def check_balances(self, prefix: str, **expected: str) -> None:
        view = self.view()
        names = {
            "A": view["balances"]["available"],
            "F": view["balances"]["frozen"],
            "R": view["balances"]["receivable"],
            "D": view["balances"]["dividend_receivable"],
            "H": view["balances"]["holdings_value"],
            "V": view["balances"]["total"],
        }
        for key, value in expected.items():
            self.check(f"{prefix} {key}", value, names[key])

    def finish(self) -> list[Check]:
        problems = self.service.verify()
        self.check("账本重放核对", "无问题", "；".join(problems) or "无问题")
        return self.checks


def _reason(order: dict[str, Any]) -> str:
    return f"{order['status']}/{order['reason_code']}"


def _error_code(func: Any) -> str:
    try:
        func()
    except SimulationError as exc:
        return exc.code
    return "无错误"


def _seed(case: Case, code: str, *days: tuple[date, str, datetime]) -> None:
    for day, value, observed in days:
        case.nav(code, day, value, observed)


# 2026-10-09 净值在 10-13 18:00 应披露，是 10-14 14:00 下单时应有的最新净值。
FRESH = (date(2026, 10, 9), "1.2000", at(2026, 10, 13, 18, 30))


def case_c01(root: Path) -> list[Check]:
    case = Case(root, "C01", "初始化")
    first = case.open_account(at(2026, 10, 14, 9))
    second, created = case.service.create_account("initial", now=at(2026, 10, 14, 9, 1))
    case.check("同键重复初始化返回同一账户", first["id"], second["id"])
    case.check("重复初始化未新建", False, created)
    case.check_balances("初始化", A="200000.00", V="200000.00")
    case.check(
        "换键再建账户",
        "active_account_exists",
        _error_code(lambda: case.service.create_account("another", now=at(2026, 10, 14, 9, 2))),
    )
    return case.finish()


def _buy_and_confirm(case: Case, nav: str, amount: str = "1000.00", code: str = FIXTURE_CODE) -> dict[str, Any]:
    _seed(case, code, FRESH)
    order = case.buy(f"buy-{code}", code, amount, at(2026, 10, 14, 14))
    case.nav(code, date(2026, 10, 14), nav, at(2026, 10, 15, 20))
    case.settle(at(2026, 10, 16, 18))
    return case.order(order["id"])


def case_c02_c06(root: Path) -> list[Check]:
    case = Case(root, "C02-C06", "申购、赎回与到账")
    case.open_account(at(2026, 10, 14, 9))
    _seed(case, FIXTURE_CODE, FRESH)
    order = case.buy("buy-1", FIXTURE_CODE, "1000.00", at(2026, 10, 14, 14))
    case.check("C02 受理状态", "awaiting_nav", order["status"])
    case.check_balances("C02 受理后", A="199000.00", F="1000.00", V="200000.00")
    case.nav(FIXTURE_CODE, date(2026, 10, 14), "1.2500", at(2026, 10, 15, 20))
    case.settle(at(2026, 10, 16, 17, 59))
    case.check("C02 确认日前不确认", "awaiting_confirmation", case.order(order["id"])["status"])
    case.settle(at(2026, 10, 16, 18))
    detail = case.order(order["id"])
    case.check("C02 确认份额", "800.00", detail["confirmed_shares"])
    case.check("C02 申购费", "0.00", detail["fee"])
    case.check_balances("C02 确认后", A="199000.00", F="0.00", H="1000.00", V="200000.00")

    sell = case.sell("sell-1", FIXTURE_CODE, "100.00", at(2026, 10, 19, 14))
    case.check("C05 赎回受理", "awaiting_nav", sell["status"])
    case.nav(FIXTURE_CODE, date(2026, 10, 19), "1.3000", at(2026, 10, 20, 20))
    case.settle(at(2026, 10, 21, 18))
    sold = case.order(sell["id"])
    case.check("C05 持有天数 10-16→10-21", 5, holding_days(date(2026, 10, 16), date(2026, 10, 21)))
    case.check("C05 赎回毛额", "130.00", sold["gross_amount"])
    case.check("C05 赎回费", "1.95", sold["fee"])
    case.check("C05 应收", "128.05", sold["net_amount"])
    case.check("C05 状态", "awaiting_payment", sold["status"])
    case.check_balances("C05 确认后", A="199000.00", H="910.00", R="128.05", V="200038.05")
    case.check("C05 到账日", "2026-11-02", sold["payment_due_date"])

    case.nav(FIXTURE_CODE, date(2026, 10, 29), "1.3000", at(2026, 10, 30, 20))
    case.settle(at(2026, 11, 2, 18))
    case.check("C06 到账状态", "settled", case.order(sell["id"])["status"])
    case.check_balances("C06 到账后", A="199128.05", R="0.00", V="200038.05")
    case.settle(at(2026, 11, 2, 18, 30))
    case.check_balances("C06 重复结算", A="199128.05", R="0.00", V="200038.05")
    return case.finish()


def case_c03(root: Path) -> list[Check]:
    rule = fixture_variant("fixture_c03_fee", "990003", "C03 外扣申购费测试变体", subscription_fee_rate="0.001")
    case = Case(root, "C03", "外扣申购费", [rule])
    case.open_account(at(2026, 10, 14, 9))
    detail = _buy_and_confirm(case, "1.2500", code="990003")
    case.check("净申购额", "999.00", detail["net_amount"])
    case.check("申购费", "1.00", detail["fee"])
    case.check("确认份额", "799.20", detail["confirmed_shares"])
    case.check_balances("确认后", H="999.00", V="199999.00")
    return case.finish()


def case_c04(root: Path) -> list[Check]:
    case = Case(root, "C04", "份额取整")
    case.open_account(at(2026, 10, 14, 9))
    detail = _buy_and_confirm(case, "1.2345")
    case.check("确认份额", "810.04", detail["confirmed_shares"])
    case.check("取整残差（份额价值−净申购额）", "-0.005620", detail["rounding_delta"])
    case.check_balances("确认后", H="999.99", V="199999.99")
    return case.finish()


def case_c07(root: Path) -> list[Check]:
    case = Case(root, "C07", "撤销与失败")
    case.open_account(at(2026, 10, 14, 9))
    _seed(case, FIXTURE_CODE, FRESH)
    first = case.buy("buy-1", FIXTURE_CODE, "1000.00", at(2026, 10, 14, 14))
    cancelled = case.service.cancel_order(first["id"], "cancel-1", first["version"], now=at(2026, 10, 14, 14, 30))
    case.check("有效撤单状态", "cancelled", cancelled["status"])
    case.check_balances("撤单后", A="200000.00", F="0.00", V="200000.00")
    second = case.buy("buy-2", FIXTURE_CODE, "1000.00", at(2026, 10, 14, 14, 40))
    case.check("撤单释放当日额度", "awaiting_nav", second["status"])
    case.check(
        "15:00 后撤单",
        "cancel_deadline_passed",
        _error_code(lambda: case.service.cancel_order(second["id"], "cancel-2", second["version"], now=at(2026, 10, 14, 15))),
    )
    failed = case.service.fail_order(
        second["id"], reason="渠道不受理", evidence="fixture：模拟渠道返回不受理", now=at(2026, 10, 15, 9)
    )
    case.check("确认失败状态", "failed", failed["status"])
    case.check_balances("失败后", A="200000.00", F="0.00", V="200000.00")
    case.check("无份额", 0, len(case.service.positions_view(case.account_id)))
    return case.finish()


def case_c08(root: Path) -> list[Check]:
    case = Case(root, "C08", "现金分红")
    case.open_account(at(2026, 10, 14, 9))
    _buy_and_confirm(case, "1.2500")
    case.nav(FIXTURE_CODE, date(2026, 10, 19), "1.2500", at(2026, 10, 19, 21))
    case.settle(at(2026, 10, 19, 23))
    case.check_balances("除息前", A="199000.00", H="1000.00", D="0.00", V="200000.00")
    payload = dict(
        announcement_key="fixture-div-1", code=FIXTURE_CODE, per_share="0.02", tax="0.00",
        record_date="2026-10-19", ex_date="2026-10-20", pay_date="2026-10-23",
        evidence="fixture：人造分红公告",
    )
    case.service.register_dividend(case.account_id, now=at(2026, 10, 19, 23, 10), **payload)
    case.nav(FIXTURE_CODE, date(2026, 10, 20), "1.2300", at(2026, 10, 20, 21))
    case.settle(at(2026, 10, 21, 0, 30))
    case.check_balances("除息后", A="199000.00", H="984.00", D="16.00", V="200000.00")
    _, created = case.service.register_dividend(case.account_id, now=at(2026, 10, 21, 1), **payload)
    case.check("重复公告不新建", False, created)
    case.settle(at(2026, 10, 23, 18))
    case.check_balances("发放后", A="199016.00", D="0.00")
    case.settle(at(2026, 10, 23, 19))
    case.check_balances("重复结算", A="199016.00", D="0.00")
    return case.finish()


def case_c09(root: Path) -> list[Check]:
    case = Case(root, "C09", "净值延迟与重启")
    case.open_account(at(2026, 10, 14, 9))
    _seed(case, FIXTURE_CODE, FRESH)
    order = case.buy("buy-1", FIXTURE_CODE, "1000.00", at(2026, 10, 14, 14))
    case.nav(FIXTURE_CODE, date(2026, 10, 15), "1.3000", at(2026, 10, 16, 12))
    case.settle(at(2026, 10, 16, 18))
    case.check("T 日净值缺失时保持待净值", "awaiting_nav", case.order(order["id"])["status"])
    case.check_balances("等待期间", A="199000.00", F="1000.00")
    case.reopen()
    case.nav(FIXTURE_CODE, date(2026, 10, 14), "1.2500", at(2026, 10, 19, 10))
    case.settle(at(2026, 10, 19, 10, 30))
    detail = case.order(order["id"])
    case.check("只用 T 日净值", "1.2500", detail["nav_value"])
    case.check("确认份额", "800.00", detail["confirmed_shares"])
    case.check("归属确认日", "2026-10-16", detail["confirmation_due_date"])
    confirmed_local = datetime.fromisoformat(detail["confirmed_at"]).astimezone(SHANGHAI).date()
    case.check("实际处理日期", "2026-10-19", confirmed_local.isoformat())
    return case.finish()


def case_c10(root: Path) -> list[Check]:
    case = Case(root, "C10", "限额缩减")
    case.open_account(at(2026, 10, 14, 9))
    _seed(case, FIXTURE_CODE, FRESH)
    case.buy("buy-800", FIXTURE_CODE, "800.00", at(2026, 10, 14, 10))
    remaining = case.service.remaining_daily_buy(case.account_id, FIXTURE_CODE, now=at(2026, 10, 14, 10, 1))
    case.check("日剩余额度", "200.00", remaining)
    accepted, skipped = limit_scaled_amount(Decimal("500.00"), remaining, Decimal("10"))
    case.check("计划 500 受理", "200.00", accepted)
    case.check("计划 500 跳过", "300.00", skipped)
    rejected = case.buy("buy-500", FIXTURE_CODE, "500.00", at(2026, 10, 14, 10, 2))
    case.check("人工整笔超限", "rejected/daily_limit", _reason(rejected))
    case.buy("buy-180", FIXTURE_CODE, "180.00", at(2026, 10, 14, 10, 3))
    remaining = case.service.remaining_daily_buy(case.account_id, FIXTURE_CODE, now=at(2026, 10, 14, 10, 4))
    case.check("已预留 180 后剩余", "20.00", remaining)
    case.check("只受理 20", "20.00", limit_scaled_amount(Decimal("500.00"), remaining, Decimal("10"))[0])
    case.check("余 5 不受理", "0.00", limit_scaled_amount(Decimal("500.00"), Decimal("5.00"), Decimal("10"))[0])
    return case.finish()


def _unlimited(rule_id: str, code: str, name: str, **extra: Any) -> dict[str, Any]:
    return fixture_variant(rule_id, code, name, max_daily_buy=UNLIMITED, **extra)


def case_c11(root: Path) -> list[Check]:
    codes = ["990011", "990012", "990013", "990014", "990015"]
    rules = [_unlimited(f"fixture_c11_{code}", code, f"C11 测试基金 {code}") for code in codes]
    case = Case(root, "C11", "余额不足", rules)
    case.open_account(at(2026, 10, 14, 9))
    for code in codes:
        _seed(case, code, FRESH)
    for code, amount in zip(codes[:4], ["50000.00", "50000.00", "50000.00", "49900.00"]):
        case.buy(f"buy-{code}", code, amount, at(2026, 10, 14, 10))
    case.check_balances("预留后", A="100.00")
    rejected = case.buy("buy-990015", "990015", "500.00", at(2026, 10, 14, 10, 5))
    case.check("计划 500 余额 100", "rejected/insufficient_cash", _reason(rejected))
    case.check_balances("拒绝后不冻资", A="100.00")
    return case.finish()


def case_c12(root: Path) -> list[Check]:
    case = Case(root, "C12", "单只 25%", [_unlimited("fixture_c12", "990021", "C12 测试基金")])
    case.open_account(at(2026, 10, 14, 9))
    _buy_and_confirm(case, "1.0000", amount="49500.00", code="990021")
    case.check_balances("持仓", H="49500.00", V="200000.00")
    case.buy("buy-400", "990021", "400.00", at(2026, 10, 19, 14))
    over = case.buy("buy-200", "990021", "200.00", at(2026, 10, 19, 14, 1))
    case.check("再买 200", "rejected/position_limit", _reason(over))
    edge = case.buy("buy-100", "990021", "100.00", at(2026, 10, 19, 14, 2))
    case.check("再买 100 恰好 25%", "awaiting_nav", edge["status"])
    return case.finish()


def case_c13(root: Path) -> list[Check]:
    rules = [
        _unlimited("fixture_c13_a", "990031", "C13 指数基金 A", fund_type="index", index_group="TEST_INDEX"),
        _unlimited("fixture_c13_b", "990032", "C13 指数基金 B", fund_type="index", index_group="TEST_INDEX"),
    ]
    case = Case(root, "C13", "同指数 40%", rules)
    case.open_account(at(2026, 10, 14, 9))
    for code in ("990031", "990032"):
        _seed(case, code, FRESH)
    case.buy("a", "990031", "40000.00", at(2026, 10, 14, 10))
    case.buy("b", "990032", "39900.00", at(2026, 10, 14, 10))
    case.check("同组 A 再买 200", "rejected/position_limit", _reason(case.buy("a2", "990031", "200.00", at(2026, 10, 14, 10, 1))))
    case.check("换代码 B 再买 200", "rejected/position_limit", _reason(case.buy("b2", "990032", "200.00", at(2026, 10, 14, 10, 2))))
    case.check("再买 100 恰好 40%", "awaiting_nav", case.buy("b3", "990032", "100.00", at(2026, 10, 14, 10, 3))["status"])
    return case.finish()


def case_c14(root: Path) -> list[Check]:
    case = Case(root, "C14", "回撤锁定", [_unlimited("fixture_c14", "990041", "C14 测试基金")])
    case.open_account(at(2026, 10, 14, 9))
    _buy_and_confirm(case, "1.0000", amount="50000.00", code="990041")
    case.nav("990041", date(2026, 10, 15), "0.4000", at(2026, 10, 19, 18, 30))
    result = case.service.valuate(case.account_id, now=at(2026, 10, 19, 19))
    case.check("V=170000", "170000.00", result["total"])
    case.check("回撤", "0.150000", result["drawdown"])
    account = case.view()
    case.check("锁定状态", "risk_paused", account["status"])
    case.nav("990041", date(2026, 10, 16), "0.3800", at(2026, 10, 20, 18, 30))
    case.service.valuate(case.account_id, now=at(2026, 10, 20, 19))
    case.check_balances("V=169000", V="169000.00")
    case.check(
        "回撤中恢复",
        "drawdown_locked",
        _error_code(lambda: case.service.resume(case.account_id, "resume-1", case.view()["version"], now=at(2026, 10, 20, 19, 5))),
    )
    stale = case.service.valuate(case.account_id, now=at(2026, 10, 23, 19))
    case.check("缺失快照质量", "incomplete", stale["quality"])
    case.check("缺失快照不改高点", "200000.00", case.view()["peak"])
    return case.finish()


def case_c15(root: Path) -> list[Check]:
    case = Case(root, "C15", "费龄分段")
    rule = load_rule(FIXTURE_QDII_C_V1)
    buy_confirm = date(2026, 10, 16)
    for days, rate in ((6, "0.015"), (7, "0.005"), (29, "0.005"), (30, "0")):
        sell_confirm = date.fromordinal(buy_confirm.toordinal() + days)
        case.check(f"{days} 天费率", rate, rule.redemption_rate(holding_days(buy_confirm, sell_confirm)))
    return case.finish()


def case_c16(root: Path) -> list[Check]:
    case = Case(root, "C16", "多批次 FIFO")
    case.open_account(at(2026, 9, 14, 9))
    case.nav(FIXTURE_CODE, date(2026, 9, 9), "1.2500", at(2026, 9, 11, 18, 30))
    long_buy = case.buy("long", FIXTURE_CODE, "125.00", at(2026, 9, 14, 14))
    case.nav(FIXTURE_CODE, date(2026, 9, 14), "1.2500", at(2026, 9, 15, 20))
    case.settle(at(2026, 9, 16, 18))
    case.nav(FIXTURE_CODE, date(2026, 10, 9), "1.2500", at(2026, 10, 13, 18, 30))
    short_buy = case.buy("short", FIXTURE_CODE, "125.00", at(2026, 10, 14, 14))
    case.nav(FIXTURE_CODE, date(2026, 10, 14), "1.2500", at(2026, 10, 15, 20))
    case.settle(at(2026, 10, 16, 18))
    case.check("长持批次份额", "100.00", case.order(long_buy["id"])["confirmed_shares"])
    case.check("短持批次份额", "100.00", case.order(short_buy["id"])["confirmed_shares"])
    sell = case.sell("sell-150", FIXTURE_CODE, "150.00", at(2026, 10, 19, 14))
    case.nav(FIXTURE_CODE, date(2026, 10, 19), "1.3000", at(2026, 10, 20, 20))
    case.settle(at(2026, 10, 21, 18))
    detail = case.order(sell["id"])
    case.check("先卖长持 100 份", "100.00", detail["allocations"][0]["shares"])
    case.check("再卖短持 50 份", "50.00", detail["allocations"][1]["shares"])
    case.check("赎回毛额", "195.00", detail["gross_amount"])
    case.check("赎回费（50×1.30×1.5%）", "0.98", detail["fee"])
    case.check("应收", "194.02", detail["net_amount"])
    case.check("剩余短持份额", "50.00", case.service.positions_view(case.account_id)[0]["shares"])
    return case.finish()


def case_dates(root: Path) -> list[Check]:
    case = Case(root, "D01", "开放日、截止与夏令时")
    case.open_account(at(2026, 3, 9, 9))
    case.nav(FIXTURE_CODE, date(2026, 3, 4), "1.2000", at(2026, 3, 6, 18, 30))
    dst = case.buy("dst-spring", FIXTURE_CODE, "100.00", at(2026, 3, 9, 14))
    case.check("美东夏令时开始后 nav_date", "2026-03-09", dst["nav_date"])
    case.check("确认时点仍为北京时间 18:00", "2026-03-11T10:00:00.000000+00:00", dst["confirmation_due_at"])
    case.nav(FIXTURE_CODE, date(2026, 9, 22), "1.2000", at(2026, 9, 24, 18, 30))
    case.check(
        "09-25 国内休市",
        "rejected/not_open_day",
        _reason(case.buy("cn-holiday", FIXTURE_CODE, "100.00", at(2026, 9, 25, 10))),
    )
    case.nav(FIXTURE_CODE, date(2026, 10, 9), "1.2000", at(2026, 10, 13, 18, 30))
    case.check("14:59:59 受理", "awaiting_nav", case.buy("cut-1", FIXTURE_CODE, "100.00", at(2026, 10, 14, 14, 59, 59))["status"])
    case.check("15:00:00 拒绝", "rejected/after_cutoff", _reason(case.buy("cut-2", FIXTURE_CODE, "100.00", at(2026, 10, 14, 15))))
    case.nav(FIXTURE_CODE, date(2026, 10, 28), "1.2000", at(2026, 10, 30, 18, 30))
    autumn = case.buy("dst-autumn", FIXTURE_CODE, "100.00", at(2026, 11, 2, 14))
    case.check("美东夏令时结束后 nav_date", "2026-11-02", autumn["nav_date"])
    case.check(
        "11-26 美国休市",
        "rejected/not_open_day",
        _reason(case.buy("us-holiday", FIXTURE_CODE, "100.00", at(2026, 11, 26, 10))),
    )
    case.check(
        "确认日跨入未覆盖的 2027",
        "rejected/calendar_unknown",
        _reason(case.buy("year-end", FIXTURE_CODE, "100.00", at(2026, 12, 30, 10))),
    )
    case.check(
        "未核验基金 016702",
        "rejected/rule_incomplete",
        _reason(case.buy("no-rule", "016702", "100.00", at(2026, 12, 30, 10, 1))),
    )
    return case.finish()


CASES = (
    case_c01,
    case_c02_c06,
    case_c03,
    case_c04,
    case_c07,
    case_c08,
    case_c09,
    case_c10,
    case_c11,
    case_c12,
    case_c13,
    case_c14,
    case_c15,
    case_c16,
    case_dates,
)


def run_acceptance(output_dir: Path) -> list[Check]:
    """在一个新的空目录中逐个运行算例，并返回全部核对项。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise FileExistsError(f"验收目录必须为空：{output_dir}")
    checks: list[Check] = []
    for case in CASES:
        checks.extend(case(output_dir))
    return checks
