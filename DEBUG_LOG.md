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

## 第二关 · 装载与检索层（已修，commit `258e209`）

评测用 `starter/tests/test_retrieval.py` 直接构造 `Retriever`，按 `eval/public_questions.jsonl` 的 R01–R15 逐题断言 gold 文档进 top_k=5 且恰好返回 5 条。修复前 8 红（R01/R03/R04/R05/R10/R11/R13/R15），修复后 **15 passed**；`/api/health` 的 `kb_docs` 从 36 降到 35、`valid_sales_rows` 18290 与 N01 对齐。

### L1 — loader 后缀白名单 + 强制 UTF-8（R03/R04/R05 根因）

- **现象**：R03/R04/R05 的 gold 文档 `KB-062(.txt)`、`KB-022(.txt)`、`KB-061(.html)` 始终不在 top-5；`/api/health` 的 `kb_docs` 只有 32（只认了 .md）。
- **假设**：loader 的后缀白名单漏了 .txt/.html，且 `decode_bytes` 强制 UTF-8 把 GBK 汉字吞了。
- **验证**：`loader.py:12` `SUPPORTED_SUFFIXES = {".md",".markdown"}`；`decode_bytes` 用 `raw.decode("utf-8", errors="ignore")`。`KB-062` 是 GBK 旧 OA 文件，UTF-8+ignore 把汉字静默丢光。
- **根因**：后缀白名单 + 编码假设。
- **修复**：`SUPPORTED_SUFFIXES` 加 `.txt/.html/.htm`；`decode_bytes` 改 `utf-8 → gb18030 → utf-8(errors="replace")`，与评测脚本 `eval/run_eval.py:decode_bytes` 同口径。`load_knowledge_base` 本就用 `rglob` 递归，修完即扫到全 35 份。
- **回归测试**：`test_retrieval.py` 的 R03/R04/R05（gold 进 top-5）；`/api/health` `kb_docs==35`（N01）。

### L2 — chunker 尾部截断

- **现象**：`KB-061`(FAQ.html) 里“小程序开发票”那一段在最尾部，R05 检索整段缺失。
- **根因**：`chunker.py:41` `range(0, len(text) - CHUNK_SIZE, CHUNK_SIZE)`，上界少一个 CHUNK_SIZE，尾部不足一块被整段丢掉。
- **修复**：上界改为 `len(text)`；`CHUNKER_VERSION` 升 `chunker-3`。
- **回归测试**：R05（KB-061 进 top-5，且命中尾部片段）。

### L3 — index 缓存键不含知识库内容

- **现象**：`make run`（`rebuild=False`）直接读 `.cache/index.json` 这份过期（25 篇）索引，知识库变了也不重建。
- **根因**：`index.py:content_key` 只哈希 `INDEX_VERSION/CHUNKER_VERSION/TOKENIZER_VERSION` 三个版本号，不含知识库内容。
- **修复**：`content_key` 追加对 `kb_dir` 下每个文件的「相对路径|大小|mtime」指纹；任一文件增删/改内容/整目录替换，键都变 → 缓存失效。`TOKENIZER_VERSION` 升 `tokenizer-3`。
- **回归测试**：替换知识库后不显式 rebuild，`/api/health` 的 `index_key` 变化且 `kb_docs` 跟随新目录。

### L4 — tokenizer 只按空白切（中文检索全面失效，最核心）

- **现象**：R01/R06/R10/R11/R13/R15 等中文问句几乎全红；英文别名桥接偶然救回几题（R02/R07/R08/R09/R12/R14 原本就绿）。
- **根因**：`tokenizer.py:tokenize` 只 `normalise(text).split()`，中文无空格 → 整句成一个 token，BM25 无法对齐中文文档。
- **修复**：`tokenize` 对 CJK 连续字做二元组切分（「退款」→`退款`），英文/数字仍按空白整词保留，别名桥接（`distinctive_tokens`/`strict_mentions`）照常工作。
- **回归测试**：`test_retrieval.py` 全部 15 题（中文问句 gold 进 top-5）。

### L5 — retriever 三处缺陷（契约 §4 与引用正确性）

- **L5a 版本过滤名不副实**：`_eligible` 判 `meta.get("status") == "已废止"`，但 `loader.meta()` 写入的键是 `"state"`，所以 `KB-002/KB-010/KB-012` 这些已废止版从未被过滤，`KB-002` 会混进结果。
  - **修复**：`_eligible` 改用 `meta.get("state")`。
- **L5b 先取 top-k 再过滤（契约 §4 不合格）**：原实现对所有片段打分、取满 top_k、最后才 `hits = [h for h if doc_id not in excluded]`，被过滤的文档若已在 top_k 里，结果就不足 5 条 → `run_eval` 的 `results_count` 检查必红。
  - **修复**：先按 `_eligible` 算 `excluded`，`allowed` 只留没被排除的片段位置，打分/取 top-k/补齐都只在 `allowed` 内做，保证恰好返回 top_k 条合格片段。
- **L5c `doc_id` 被错挂**：`search` 里有 `hit.doc_id = ordered[len(hits)].doc_id`，把命中片段的 doc_id 覆盖成排序列表里的另一篇文档 → 同一 doc_id 重复出现、且 chat 的 `citations.doc_id` 会指向错误文档。
  - **修复**：删除该行；`Hit` 本就带正确 `doc_id`。
- **回归测试**：`test_retrieval.py`（恰好 5 条 + gold 进 top-5）；chat 链路后续用 `doc_id` 挂引用时不再错位（见第三关）。

### L6 — `/api/health` 的 `kb_docs` 数文件而非文档（N01 根因）

- **现象**：`kb_docs` 返回 36（含 `knowledge_base/README.md`），N01 期望 35。
- **根因**：`service.py:health` 用 `kb_dir.rglob("*")` 数文件，把没有 `KB-xxx` 编号的说明文件也算进去了；契约 §1 明确 `kb_docs` 是“实际进入索引的文档数”。
- **修复**：`kb_docs` 改为 `len(self.index.docs_meta)`（入索引文档数）。
- **回归测试**：N01（`kb_docs==35`）。

## 第三关 · 会话与作答层（待修）

- sessions：全局单列表完全忽略 `session_id` → 串线。
- service：`planner.plan(question)` 未传 history → 追问接不上。
- answerer：`_context()` 把整篇文档拼进 answer → 必爆 1200 字上限；引用 `quote` 非原文连续片段。
- hybrid / data_evidence：需打通数据库真实查询与文档引用。

## 第四关 · 可调试性（待修）

- trace 面板前端可视化；`run_eval` 接进 pytest/CI。
