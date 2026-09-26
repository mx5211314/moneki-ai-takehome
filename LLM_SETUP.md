# LLM 接入说明

按 `docs/API_CONTRACT.md` §7.4 的骨架写。

## 1. 用了什么

| 项 | 值 |
|---|---|
| 厂商 | DeepSeek |
| 模型 | `deepseek-flash`（完全由 `LLM_MODEL` 决定，代码里没有写死任何模型名） |
| 协议 | **OpenAI 兼容 Chat Completions**（`POST {base}/chat/completions`） |
| SDK | **不用 SDK**，`kbqa/llm.py` 直接用标准库 `urllib.request` 发请求 |

不用 SDK 是刻意的：少一个依赖，也少一处版本地雷；而且这样"请求到底长什么样"
完全由我们自己控制，预检里的 P4（只用了 DeepSeek 文档列出的顶层参数）才有意义。

## 2. 配置从哪里读

全部从**环境变量**读，`kbqa/config.py: load_settings()`，代码里没有任何默认值以外的硬编码：

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `LLM_BASE_URL` | 空 | 接口地址，**原样使用**：不补 `/v1`、不截路径（契约 §7.2） |
| `LLM_API_KEY` | 空 | 密钥，只进 `Authorization: Bearer`，**不写进任何日志、trace 或数据库** |
| `LLM_MODEL` | 空 | 模型名，原样放进请求体的 `model` 字段 |
| `LLM_TIMEOUT` | `120` | 单次模型调用超时（秒），契约 §7.3 要求不小于 120 |
| `CHAT_BUDGET` | `150` | `/api/chat` 整体预算（秒），契约 §7.3 要求 180 秒内返回 |

`live = bool(LLM_API_KEY and LLM_BASE_URL and LLM_MODEL)`——三者都有才进入 live 模式，
否则 mock 降级。

## 3. 怎么换成你们的

只需要这三个环境变量，**不用改代码、不用重新执行重建命令**（索引和清洗表与大模型的配置无关）：

```bash
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY=sk-你的Key
export LLM_MODEL=deepseek-flash

# 然后正常启动即可
cd starter && make run
```

**改完需要重启服务**（配置在启动时读一次）；**不需要**重新 `make rebuild`。

如果你们要走代理来观察流量（契约 §7.2 要求"能看到你发给模型的完整请求"）：

```bash
python3 eval/llm_gateway.py proxy --upstream https://api.deepseek.com --log llm_traffic.jsonl
# 把上面 export 的 LLM_BASE_URL 换成它打印出来的地址
```

## 4. 怎么看到发给模型的请求

三种办法，任选：

1. **代理（推荐，契约给的）**：`eval/llm_gateway.py proxy --log llm_traffic.jsonl`，
   每一次请求和响应都会按 JSONL 落盘。`llm_traffic.jsonl` 已在 `.gitignore` 里，不会入库。
2. **trace**：每次 `/api/chat` 都会把"这一步发给模型的提示词、返回了什么、花了多久"
   写进 trace，用 `GET /api/trace/{trace_id}` 就能回放（`trace_id` 在 chat 的响应里）。
3. **前端调试面板**：看板上的 trace 面板直接渲染这些步骤。

脱敏样例（取自 `llm_traffic.jsonl` 的结构，Key 已打码）：

```json
{"ts": "2026-09-26T04:20:11Z", "url": "http://127.0.0.1:8100/ds-gw/chat/completions",
 "request": {"model": "deepseek-flash", "messages": [
   {"role": "system", "content": "你是连锁餐饮经营助手……"},
   {"role": "user", "content": "你们的退款规则是怎么规定的？"}],
   "tools": [{"type": "function", "function": {"name": "search_kb", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}}}}],
   "max_tokens": 2048},
 "headers": {"authorization": "Bearer sk-************"}}
```

## 5. 没有 Key 时会怎样

服务**照常启动**，`/api/health` 返回 `llm_mode: "mock"`。四个接口的表现：

| 接口 | mock 模式下 |
|---|---|
| `/api/health` | 正常，`llm_mode = "mock"` |
| `/api/metrics/summary`、`/api/metrics/daily` | 正常（本来就走数据库，与模型无关） |
| `/api/retrieve` | 正常（走 BM25，与模型无关） |
| `/api/chat` | 走**规则作答**链路：数字仍来自真实数据库查询、文档说法仍带逐字引用，只是"组织语言"由规则模板完成，不经过模型 |

降级策略：**不允许因为没有模型就编答案**。数字一律来自工具查询、写进 `data_evidence`；
文档事实一律来自检索到的原文、带逐字 `quote`；都没有就如实拒答。
本仓库 `EVAL_REPORT.md` 里的分数，全部是 mock 模式下跑出来的。

## 6. 依赖与安装

- **没有额外依赖**：`kbqa/llm.py` 只用标准库，`requirements.txt` 里不会因接模型而多出一行。
- **没有模型文件需要下载**：不跑本地模型，首次启动不需要下载任何权重。
- **首次启动耗时**：不受模型影响（`make rebuild` 建清洗表 + 索引约几秒）。

## 7. 自测结果

走 OpenAI 兼容协议，跑了 `eval/llm_gateway.py preflight`（假模型，不花钱、不需要真 Key）：

```
预检假模型已启动：http://127.0.0.1:8100/ds-gw

开始检查 http://127.0.0.1:8007 ……
  [normal] 你们的退款规则是怎么规定的？ → HTTP 200，1.77 秒
  [hang]   你们的退款规则是怎么规定的？ → HTTP 200，120.61 秒
  （共 16 类场景 × 2 个问题 = 32 次问答，全部 HTTP 200）

编号  检查项                                                            结果  说明
----------------------------------------------------------------------------------
P1    服务确实把请求发到了注入的 LLM_BASE_URL（含路径前缀）             通过  共观察到 60 次 POST /ds-gw/chat/completions。
P2    请求里的 model 等于注入的 LLM_MODEL                               通过  全部请求都用了 deepseek-flash。
P3    注入的 Key 以 Authorization: Bearer 发送                          通过  全部请求都带了正确的 Bearer Key。
P4    只用了 DeepSeek 文档列出的顶层参数                                通过  只出现了 DeepSeek 文档列出的顶层参数。
P5    max_tokens 不设，或不小于 2048                                    通过  max_tokens 都不小于 2048。
P6    没有访问 {prefix}/chat/completions 之外的任何路径                 通过  只访问了 POST /ds-gw/chat/completions，没有碰任何别的路径。
P7    工具定义规范，且每一个工具调用都以 role=tool + tool_call_id 回传  通过  工具定义规范，44 个工具调用的结果都正确回传了。
P8    每个场景下 /api/chat 都返回 HTTP 200 与字段完整的合法 JSON        通过  32 次问答全部返回 200 和字段完整的 JSON。
P9    模型不可用时给出结构化 refusal，answer 从不是空串                 通过  模型不可用的场景下都给了结构化 refusal 或有据可查的回答，answer 从不是空串。
P10   思考内容没有漏进 answer / citations / data_evidence               通过  32 次回答里，思考标记都没有出现在任何对外字段里。
P11   /api/chat 在时限内返回（含长时间无响应的场景）                    通过  最慢的一次是 120.66 秒，都在 180 秒以内。
P12   注入环境变量后 /api/health 报告 llm_mode = live                   通过  llm_mode = live。
P13   多轮工具调用之间 reasoning_content 原样回传（没有触发 400）       通过  18 次多轮请求都原样回传了 reasoning_content。
P14   保持连接的空行与 SSE 注释没有把服务弄坏                           通过  正文前的空行和 SSE 的 `: keep-alive` 注释都被正确跳过了，slow 场景照常给出回答。

预检通过：在 OpenAI 兼容这条路线上，我们能原样接上你的服务。
```

完整报告见 `preflight_report.md` / `preflight_report.json`。

## 8. 已知限制

1. **没有真 Key，live 模式的实际回答质量未经真实模型验证过**。预检只能证明
   "接得规范、异常场景不崩"，证明不了 `deepseek-flash` 真实作答的准确率。
   本仓库所有分数都是 mock 降级模式下跑出的。
2. **多轮工具调用依赖 `reasoning_content` 回传**（P13 已验证原样回传），
   如果换成不返回 `reasoning_content` 的兼容服务，多轮链路会退化——
   目前没有为这种情况做兜底。
3. **`hang` 场景要等到 120 秒超时**（P11 显示 120.66 秒），虽然仍在 180 秒预算内，
   但余量不大；如果你们的网络比预检环境更慢，有可能逼近时限。
4. mock 模式下回答是模板拼装的，**措辞不如模型自然**，长句可能生硬；
   引用与数字的准确性不受影响。
