"""把原始 sales 导进 var/clean.db，指标都查这张表。

清洗规则严格按知识库 KB-001（指标口径手册 v3）：
- §2 规范化：store_id/product_id 去空格转大写；date 三种格式
  (YYYY-MM-DD / YYYY/M/D / DD-MM-YYYY 且日在前)；amount 去 ¥ 后按数字解析(保留符号)；qty 整数。
- §3 剔除(按顺序)：日期不可解析 → 空金额(不回填) → qty≤0 → 脏门店 → 脏商品 → 七字段完全相同的重复行(只留 1)。
- 退款行 = 清洗后 amount<0 的行；用 is_refund 标记，金额字段保留原符号。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional, Set

#: 金额里的 `¥` 去掉再按数字解析。
_CURRENCY = str.maketrans("", "", "¥￥ \t　")

REMOVAL_REASONS = (
    "1_unparseable_date",
    "2_empty_amount",
    "3_qty_le_zero",
    "4_store_not_in_stores",
    "5_product_not_in_products",
    "6_duplicate_row",
)


def parse_amount(value: Optional[str]) -> tuple[Optional[int], str]:
    """返回 (分, 状态)。状态取值：`ok`、`empty`、`bad`。

    KB-001 §2.3 与 §3.2：`¥38.00` 与 `38.00` 是同一个金额，带符号的行是可恢复的，必须保留。
    空金额按 §3.2 直接剔除、不回填；无法解析的金额也视为无效、剔除（不拿 unit_price 反推）。
    """
    text = (value or "").translate(_CURRENCY)
    if not text:
        return None, "empty"
    try:
        cents = int((Decimal(text) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None, "bad"
    return cents, "ok"


def parse_qty(value: Optional[str]) -> Optional[int]:
    """KB-001 §2.4：按整数解析。解析不了的按 0，会被 §3.3 剔除。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def parse_date(value: Optional[str]) -> Optional[date]:
    """KB-001 §2.2：接受三种格式。

    - YYYY-MM-DD
    - YYYY/M/D
    - DD-MM-YYYY：第三种是旧 POS 导出，**日在前、月在后**，例如 `25-07-2026` 是 7 月 25 日。
      用“第 3 段是 4 位年份”来识别这种格式，避免和 YYYY-MM-DD 搞混。
    """
    s = (value or "").strip()
    if not s:
        return None
    if "/" in s:
        try:
            y, m, d = s.split("/")
            return date(int(y), int(m), int(d))
        except (ValueError, TypeError):
            return None
    parts = s.split("-")
    if len(parts) == 3:
        try:
            if len(parts[2]) == 4:  # DD-MM-YYYY，日在前
                d, m, y = parts
                return date(int(y), int(m), int(d))
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, TypeError):
            return None
    return None


def norm_id(value: Optional[str]) -> str:
    """KB-001 §2.1：store_id/product_id 去首尾空白并转大写，先规范化再判脏外键。"""
    return (value or "").strip().upper()


@dataclass
class CleaningReport:
    raw_rows: int = 0
    kept_rows: int = 0
    kept_sales_rows: int = 0
    kept_refund_rows: int = 0
    removed: dict = field(default_factory=lambda: {k: 0 for k in REMOVAL_REASONS})
    note_unparseable_amount: int = 0

    def as_dict(self) -> dict:
        return {
            "raw_rows": self.raw_rows,
            "removed": dict(self.removed, note_unparseable_amount=self.note_unparseable_amount),
            "kept_rows": self.kept_rows,
            "kept_sales_rows": self.kept_sales_rows,
            "kept_refund_rows": self.kept_refund_rows,
        }


def open_readonly(path: Path) -> sqlite3.Connection:
    """打开数据库。"""
    conn = sqlite3.connect(path.as_posix(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def clean_rows(
    rows: Iterable[sqlite3.Row],
    stores: Set[str],
    products: Set[str],
) -> tuple[list[tuple], CleaningReport]:
    """KB-001 §2/§3：规范化 + 按序剔除，返回 (保留行, 清洗台账)。

    保留行的字段顺序与 sales_clean 表一致：
    (order_id, date, store_id, product_id, qty, amount_cents, payment, is_refund)。
    """
    report = CleaningReport()
    kept: list[tuple] = []
    seen: set[tuple] = set()
    for row in rows:
        report.raw_rows += 1
        parsed_date = parse_date(row["date"])
        if parsed_date is None:
            report.removed["1_unparseable_date"] += 1
            continue
        cents, status = parse_amount(row["amount"])
        if status == "empty":
            report.removed["2_empty_amount"] += 1
            continue
        if status == "bad":
            # 无法解析的金额不是合法金额，剔除、不回填；记一笔便于数据质量面板说明。
            report.removed["2_empty_amount"] += 1
            report.note_unparseable_amount += 1
            continue
        qty = parse_qty(row["qty"])
        if qty is None or qty <= 0:
            report.removed["3_qty_le_zero"] += 1
            continue
        store_id = norm_id(row["store_id"])
        product_id = norm_id(row["product_id"])
        if store_id not in stores:
            report.removed["4_store_not_in_stores"] += 1
            continue
        if product_id not in products:
            report.removed["5_product_not_in_products"] += 1
            continue
        order_id = (row["order_id"] or "").strip()
        payment = (row["payment"] or "").strip()
        # §3.6：七字段规范化后完全一致才算重复行；共用订单号的不同商品行要保留。
        key = (order_id, parsed_date.isoformat(), store_id, product_id, qty, cents, payment)
        if key in seen:
            report.removed["6_duplicate_row"] += 1
            continue
        seen.add(key)
        kept.append(
            (
                order_id,
                parsed_date.isoformat(),
                store_id,
                product_id,
                qty,
                cents,
                payment,
                1 if cents < 0 else 0,
            )
        )
    report.kept_rows = len(kept)
    report.kept_refund_rows = sum(1 for r in kept if r[-1])
    report.kept_sales_rows = report.kept_rows - report.kept_refund_rows
    return kept, report


_SCHEMA = """
CREATE TABLE stores (store_id TEXT PRIMARY KEY, store_name TEXT, category TEXT, district TEXT);
CREATE TABLE products (product_id TEXT PRIMARY KEY, product_name TEXT,
                       product_category TEXT, unit_price REAL);
CREATE TABLE sales_clean (
    order_id TEXT, date TEXT, store_id TEXT, product_id TEXT,
    qty INTEGER, amount_cents INTEGER, payment TEXT, is_refund INTEGER
);
CREATE INDEX idx_clean_date ON sales_clean(date);
CREATE INDEX idx_clean_store ON sales_clean(store_id);
CREATE INDEX idx_clean_product ON sales_clean(product_id);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def build_clean_db(source: Path, target: Path) -> CleaningReport:
    """从只读的源库重建清洗表。返回清洗台账，供 `/api/health` 与数据质量面板使用。"""
    if not source.exists():
        raise FileNotFoundError("找不到源数据库：%s" % source)
    src = open_readonly(source)
    try:
        stores = [tuple(r) for r in src.execute("SELECT store_id, store_name, category, district FROM stores")]
        products = [
            tuple(r)
            for r in src.execute(
                "SELECT product_id, product_name, product_category, unit_price FROM products"
            )
        ]
        store_ids = {r[0] for r in stores}
        product_ids = {r[0] for r in products}
        rows, report = clean_rows(
            src.execute("SELECT order_id, date, store_id, product_id, qty, amount, payment FROM sales"),
            store_ids,
            product_ids,
        )
    finally:
        src.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    out = sqlite3.connect(target)
    try:
        out.executescript(_SCHEMA)
        out.executemany("INSERT INTO stores VALUES (?,?,?,?)", stores)
        out.executemany("INSERT INTO products VALUES (?,?,?,?)", products)
        out.executemany("INSERT INTO sales_clean VALUES (?,?,?,?,?,?,?,?)", rows)
        out.execute(
            "INSERT INTO meta VALUES ('cleaning_report', ?)",
            (json.dumps(report.as_dict(), ensure_ascii=False),),
        )
        out.execute("INSERT INTO meta VALUES ('source_db', ?)", (source.name,))
        out.commit()
    finally:
        out.close()
    return report
