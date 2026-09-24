"""指标回归测试：按 KB-001 清洗后，公开题库 M01-M06 / N01 的期望值必须命中。

这些数字是评审 oracle（eval/public_questions.jsonl），写死在这里当回归网：
换一套数据/知识库重建后，只要口径实现退化，这里会先红。
只依赖标准库，不拉 fastapi，跑得快、能单独验证数据层。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kbqa.cleaning import build_clean_db
from kbqa.tools import DataTools

ROOT = Path(__file__).resolve().parents[1]  # starter/
SRC = ROOT.parent / "data" / "pos.db"  # 作业包根/data/pos.db


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("clean") / "clean.db"
    build_clean_db(SRC, db)
    return db


def test_valid_sales_rows(clean_db: Path) -> None:
    # N01：清洗后明细行数（销售行 + 退款行）应为 18290。
    assert DataTools(clean_db).valid_sales_rows() == 18290


def _check(clean_db, start, end, store, product, net, refund, orders, qty, aov):
    m = DataTools(clean_db).query_metrics(start, end, store, product)
    assert m["net_revenue"] == pytest.approx(net, abs=0.01), m
    assert m["refund_amount"] == pytest.approx(refund, abs=0.01), m
    assert m["orders"] == orders, m
    assert m["qty"] == qty, m
    if aov is None:
        assert m["aov"] is None, m
    else:
        assert m["aov"] == pytest.approx(aov, abs=0.01), m


def test_M01(clean_db):
    # 6 月全店：净营业额含退款、退款金额=退款行绝对值、订单数=有效订单数。
    _check(clean_db, "2026-06-01", "2026-06-30", None, None, 156757.0, 953.0, 4311, 6496, 36.36)


def test_M02(clean_db):
    # 7 月 S02：store_id 大小写/空格规范化后仍能命中。
    _check(clean_db, "2026-07-01", "2026-07-31", "S02", None, 41740.0, 107.0, 875, 1395, 47.7)


def test_M03(clean_db):
    # 8 月 P21：按商品筛选。
    _check(clean_db, "2026-08-01", "2026-08-31", None, "P21", 11024.0, 16.0, 461, 689, 23.91)


def test_M04(clean_db):
    # 单日单店单商品：退款金额应为 0。
    _check(clean_db, "2026-06-18", "2026-06-18", "S02", "P06", 3625.0, 0.0, 53, 125, 68.4)


def test_M05_empty(clean_db):
    # 数据区间之外（9 月）：数值全 0，aov 为 null，不能 500。
    m = DataTools(clean_db).query_metrics("2026-09-01", "2026-09-30")
    assert m["net_revenue"] == 0.0
    assert m["orders"] == 0
    assert m["aov"] is None


def test_M06_daily(clean_db):
    # S03 2026-06-08~06-12：只有 06-12 有营业；区间内每天一条，无营业为 0。
    d = DataTools(clean_db).daily_metrics("2026-06-08", "2026-06-12", "S03")
    days = {x["date"]: x for x in d["days"]}
    assert len(days) == 5
    assert days["2026-06-12"]["net_revenue"] == pytest.approx(998.0, abs=0.01)
    assert days["2026-06-12"]["orders"] == 27
    assert days["2026-06-12"]["aov"] == pytest.approx(36.96, abs=0.01)
    for dt in ("2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"):
        assert days[dt]["net_revenue"] == 0.0
        assert days[dt]["orders"] == 0
