# DEBUG_LOG — 缺陷根因与修复记录

按"先加一个会红的测试，再修"的节奏逐层推进。每个缺陷一条：现象 / 假设 / 验证 / 根因 / 修复 / 回归测试。
评测 oracle 为 `eval/public_questions.jsonl`（M01–M06、N01 等期望值即验收标准）。

---

## 第一关 · 数据层（cleaning.py + tools.py）

### D1 — cleaning.py `clean_rows()` 完全没做 KB-001 规范化与剔除

- **现象**：`/api/health` 的 `valid_sales_rows` 旧代码返回 18628（全保留），oracle N01 期望 **18290**；`/api/data_quality` 的清洗台账 `removed` 全为 0，等于没清洗。
- **假设**：前同事把清洗写成"原样搬运"，只把空金额置 0、其余照收，六类剔除与 `REMOVAL_REASONS` 定义从未调用。
- **验证**：写临时探针脚本按 KB-001 在内存里清洗 `pos.db`，得到 18628→18290，剔除构成 `8(日期)+150(空金额)+30(qty≤0)+10(脏门店)+40(脏商品)+100(重复行)=338`，与 oracle 逐项吻合 → 口径理解正确。
- **根因**：`starter/kbqa/cleaning.py:77` `clean_rows()` 仅 `kept.append` 原行，`parse_amount` 遇空/坏金额返回 `cents=0` 而非剔除；`store_id/product_id` 未规范化就判脏外键；无日期解析、无重复行去重。
- **修复**：重写 `clean_rows()`（`cleaning.py` 全文件），实现 §2 规范化（去空格转大写、`date` 三格式含 `DD-MM-YYYY` 日在前、金额去 `¥` 保留符号、`qty` 整数）+ §3 六类顺序剔除；`build_clean_db` 现把 `stores/products` 集合传入做脏外键判断。commit `db19f0e`。
- **回归测试**：`starter/tests/test_metrics.py::test_valid_sales_rows` + 全部 `removed` 计数断言（修复前必红：旧代码全保留 18628、台账全 0）。

### D2 — tools.py `_where()` 闭区间写成左闭右开

- **现象**：`query_metrics` 对 `end` 当日数据整月少算最后一天（如 6 月少 6-30）。
- **假设**：`date < ?` 是漏写 `=`。
- **验证**：契约 `docs/API_CONTRACT.md` §2 明写 `start`/`end` 为**闭区间**；修复后 M01 等月度值精确命中 oracle。
- **根因**：`starter/kbqa/tools.py:53` `clause = ["date >= ?", "date < ?"]`。
- **修复**：改为 `date <= ?`。`daily_metrics` 复用 `_where`，一并修好。
- **回归测试**：`test_metrics.py::test_M01/M02/M03/M04`（闭区间月末数据）、`test_M06_daily`（`S03 2026-06-08~06-12` 每天一条）。

### D3 — tools.py `query_metrics()` 退款不计入净额、退款金额写死 0、订单数错

- **现象**：M01 旧代码 `net_revenue` 不含退款、`refund_amount` 恒为 0、`orders` 用 `COUNT(*)` 数全部行（非有效订单数）；oracle M01 期望 净 156757 / 退款 953 / 订单 4311。
- **假设**：前同事按 v2 口径（退款整表排除）写的，与现行 v3 相反。
- **验证**：KB-001 §4 明确"净营业额=销售行+退款行""退款金额=退款行绝对值""有效订单数=销售行 distinct order_id""销量=销售qty−退款qty"；探针确认退款行 `qty` 在库里为**正值**，故销量须 `销售qty − 退款qty`。
- **根因**：`starter/kbqa/tools.py:97-107` `SUM(amount_cents) WHERE is_refund=0` 且 `refund=0` 写死；`COUNT(*)` 而非 `COUNT(DISTINCT order_id WHERE amount>0)`。
- **修复**：`query_metrics` 改为 `SUM(amount_cents)`（全保留行，含退款）、`SUM(CASE amount<0 THEN -amount END)` 作退款、`COUNT(DISTINCT CASE amount>0 THEN order_id)` 作订单、`SUM(CASE amount>0 THEN qty ELSE 0 END) - SUM(CASE amount<0 THEN qty)` 作销量；`aov = 净营业额/有效订单数`。
- **回归测试**：`test_metrics.py::test_M01..M04`（`refund_amount`/`orders`/`qty`/`aov` 全部精确命中）、`test_M05_empty`（区间外全 0、`aov=null` 不 500）。

> 第一关后端指标口径已对齐 oracle：本机 `pytest starter/tests/test_metrics.py` → **7 passed**。

---

## 第二关 · 装载与检索层（待修，见任务清单）

- L1 loader：只扫顶层、仅认 `.md/.markdown`；漏掉 `KB-022/.txt`、`KB-061/.html`、`KB-062/.txt`，且未递归 `handbook/legacy/notices/...` 子目录；`decode_bytes` 强制 UTF-8 会让 GBK 的 `KB-062` 静默丢字。
- L2 chunker：`range(0, len-text, CHUNK_SIZE)` 步长=CHUNK_SIZE → 每篇尾部被截断。
- L3 index：缓存键只哈希版本号不含知识库内容 → `rebuild=False`（默认）仍读过期缓存；仓库内 `.cache/index.json` 是 25 篇过期索引。
- L4 tokenizer：`tokenize()` 仅 `normalise(text).split()` → 中文无二元组切词，中文检索基本失效。
- L5 retriever：元数据过滤未进算分；先取 top_k 再过滤（契约 §4 不合格）；引用 `hit.doc_id` 挂错文档。
- L6 `/api/health` 的 `kb_docs` 数的是目录文件数（含 README 得 36），应改为实际入索引文档数（oracle 期望 35）。

## 第三关 · 会话与作答层（待修）

- sessions：全局单列表完全忽略 `session_id` → 串线。
- service：`planner.plan(question)` 未传 history → 追问接不上。
- answerer：`_context()` 把整篇文档拼进 answer → 必爆 1200 字上限；引用 `quote` 非原文连续片段。
- hybrid / data_evidence：需打通数据库真实查询与文档引用。

## 第四关 · 可调试性（待修）

- trace 面板前端可视化；`run_eval` 接进 pytest/CI。
