# V8 Agent 改造说明

## 一句话

把「只会在聊天里回忆」的助手，改成「会调工具的 Agent」，且**保住傲娇人设**：
执行阶段按工具纪律办事，回答阶段再换成千问的口气。

## 这次改了什么

| 文件 | 改动 |
|---|---|
| `qwen3.5-9b-wenzhao-Modelfile`（原 tsundere 版已备份 .bak） | **修复 TEMPLATE**：还原被吃掉的 `<tools></tools>`、`<tool_call></tool_call>` 标签，补上 `.ToolCalls` 渲染分支。这是 Agent 能不能跑起来的开关。 |
| `agent/`（新增） | `tools.py` 工具注册表、`loop.py` 主循环、`router.py` 意图路由、`budget.py` 预算、`trace.py` 轨迹 |
| `llm_client.py` | 新增 `chat_once()`（**自己拼 ChatML，不用 Ollama 模板**）、`render_prompt()`、`parse_tool_calls()`、`strip_think()`；`chat_stream()` 增加 `think` 参数与开头噪音缓冲 |
| `main.py` | 重写：指令路由 + 双通道（Agent / 闲聊）+ 两段式人设 |
| `background_worker.py` | 归纳前过滤错误/兜底文案，避免污染长期记忆 |
| `verify_tools.py`（新增） | 四步自检：模板 → 单步工具 → 多步工具 → 路由 |
| `eval/`（新增） | 20 条评测任务 + 判分脚本 |

原文件已备份为 `*.bak`。

## 为什么必须两段式

实测（在修复模板之前）：

- 直接给这套人设 + tools 参数 → 模型完全无视工具，开始演角色，并**编造了一个 2024 年的时间**。
- 同一模型，把工具 schema 明确写进 prompt → 3 秒返回
  `<tool_call>{"name": "get_time", "arguments": {"city": "Tokyo"}}</tool_call>`。

结论：模型有能力，是「强人设」把「工具纪律」压掉了。所以拆成两段：

```
Agent 阶段：system = 工具纪律（短、硬），temperature 0.2，think 关
   ↓ 得到一段「事实底稿」（纯陈述，无角色）
人设阶段：Modelfile 里的傲娇 SYSTEM，temperature 1.0
   ↓ 把底稿翻译成千问的口气
```

代价是多一次调用（约 5~8 秒），换来工具可靠性和角色一致性的双赢。

**人设阶段的坑（实测）**：`think=False` 时模型约 1/3 概率把英文分析写进正文
（"Thinking Process: 1. Analyze the Request..."），约 1/6 概率直接空回复。
所以 `persona_reply()` 做成了「raw prompt → 校验（空 / 含 `<think>` / 英文成串 判废）
→ 最多重试一次 → 一次性打印」。代价是失去打字机效果，换来输出不脏；
闲聊通道仍然是流式的，没有牺牲 V7 的观感。


## 为什么 Agent 不用 Ollama 原生 tools

实测记录（同一台机器、同一个模型、2026-11-20）：

| 方式 | 结果 |
|---|---|
| 修复前模板 + `tools` 参数 | schema 压根没进 prompt（`{{ .Tools }}` 不存在），模型开始编造时间 |
| 修复后模板 + `tools` 参数 | schema 进了 prompt（prompt tokens 916 → 1037，多 121），但 `/api/chat` **稳定**返回 49 token 的 `<think></think>` 后直接 EOS，`tool_calls` 恒空，`think=true/false` 都一样 |
| 手拼 ChatML + 干净 JSON schema（`/api/generate` raw） | **3/3 正确**发出 `<tool_call>`；把工具结果用 `<tool_response>` 回灌后能正常作答；多步任务 6.5s 跑完 |

所以最终形态：

- **Agent 阶段** → `chat_once()` 自己拼 prompt、自己解析 `<tool_call>`，完全绕开 Ollama 模板。
- **闲聊 / 人设阶段** → 仍走 `/api/chat` + Modelfile 模板（这部分工作正常，流式体验也好）。

模板修正是必要的（没有它 schema 进不去），但**只修模板不够**，这点在别的机器/版本上可能不一样，
所以 `verify_tools.py` 保留成可重复跑的回归测试。

## 目录结构

```
qwen3.5_9b/
├── main.py                  # 入口：指令 + 路由 + 双通道
├── llm_client.py            # chat_stream(流式/人设) + chat_once(Agent/非流式)
├── agent/
│   ├── __init__.py
│   ├── tools.py             # 工具注册表（memory_search / get_time / calc）
│   ├── loop.py              # ReAct 精简循环
│   ├── router.py            # 规则路由（命中才走 Agent）
│   ├── budget.py            # 步数/调用数/时间 三重上限
│   └── trace.py             # JSONL 轨迹 → logs/
├── verify_tools.py          # 打通性自检
├── eval/
│   ├── tasks.jsonl          # 20 条任务
│   └── run_eval.py          # 判分
├── logs/                    # trace_YYYYMMDD.jsonl / eval_*.json
└── *.bak                    # 原始文件备份
```

## 怎么用

```powershell
cd C:\OllamaModels\qwen3.5_9b

# 1) 重建模型（模板改过了，必须重建）
ollama create qwen3.5-9b-wenzhao -f qwen3.5-9b-wenzhao-Modelfile

# 2) 自检（四步全 PASS 才算通）
python verify_tools.py

# 3) 交互
python main.py

# 4) 评测（建议后台跑，3~6 分钟）
python eval/run_eval.py
```

交互里新增：

```
/agent 现在几点，顺便算一下 88*99   # 强制走 Agent
/chat 你好                          # 强制闲聊
/tools                              # 看工具列表
/trace                              # 看上一次 Agent 每一步干了什么
```

默认自动路由：命中「时间/算术/用户记忆/帮我查」才走 Agent，其余照旧闲聊，
不会让每句"你好"都多等两秒。

## 关键参数在哪调

| 想要 | 改哪 |
|---|---|
| 允许更多步数 | `agent/budget.py` 的 `max_steps`（默认 4） |
| 更快/更慢 | 想改 `num_ctx` 必须**三处同时改**：Modelfile、`main.py` 的 `chat_stream`、`agent/loop.py` 的 `chat_once`。不一致会导致模型反复重载 |
| 更听话 / 更自由 | `agent/loop.py` 的 `temperature=0.2`（工具阶段别超过 0.4） |
| 路由误判 | `agent/router.py` 的正则 |
| 工具输出长度 | `agent/loop.py` 的 `MAX_TOOL_OUTPUT=1200` |

## 加一个工具（三步）

```python
# agent/tools.py 末尾
@tool("doc_search", "检索本地文档库。问到知识库里的资料时使用。",
      {"type": "object",
       "properties": {"query": {"type": "string"}},
       "required": ["query"]})
def doc_search(query: str) -> str:
    ...
    return "片段1\n片段2"      # 控制在 1200 字符内
```

不用改循环、不用改主程序，schema 会自动进 prompt。**但工具总数请保持 ≤ 8**，
超了 9B 的选错率会明显上升。

## 性能基线（本机实测）

| 项 | 值 |
|---|---|
| 硬件 | RTX 3060 Ti 8 GB / Xeon E5-2666 v3 / 32 GB |
| 模型 | Qwen3.5-9B Q4_K_M，32k ctx，6.3 GB 全驻显存 |
| 生成速度 | ~28 tok/s |
| 单步工具调用（raw 模式，模型不先想一遍） | 3~5 s |
| 多步 Agent（2 个工具 + 汇总） | **6.5 s**（实测） |
| 再叠一次傲娇人设改写 | +6~8 s |
| **一轮完整交互**（工具 + 人设改写，含偶发重试） | **17~26 s** |

所以能接受的上限就是「2 次工具调用 / 25 秒」。想更快只有一条路：
把判断用代码写死，让模型只负责填参数。

## 评测结果（20 条任务，本机）

判分口径：工具选择正确（该调的调了、不该调的没调）+ 答案含关键事实。

| 版本 | 通过率 | 工具错误 | 平均步数 | 平均延迟 |
|---|---|---|---|---|
| 只有 prompt 约束 | 17/20 = 85% | 3 | 1.65 | 5.8 s |
| + 确定性纠正（nudge） | 19/20 = 95% | 1 | 2.80 | 9.2 s |
| + 空回合重试 / 按工具逐个纠正 | 19/20 = 95% | 1 | 2.00 | 7.7 s |
| + 代码兜底（模型拒调时替它调） | **20/20 = 100%** | **0** | **1.95** | **7.2 s** |

> 样本只有 20 条，100% 只说明「这 20 条里没有失败」，不等于泛化。
> 真实使用攒到 50 条以上再重测，别把这个数字当结论。


**确定性兜底机制**（9B 模型的必需品，不是可选项）：

1. `router.suggested_tools()` 从任务里判断「必须调用哪些工具」（可能多个）。
2. 模型没调就直接作答时，补一条 system 强行纠正（每个工具纠一次）。
3. 纠正两次仍不调，就由**代码替它调**（只对 calc / get_time / memory_search
   这类确定性、无副作用的工具；写操作永不这么做），把结果塞回上下文让它复述。

实测收益：`(345+678)*12` 从「心算答 6906（错）」变成调用 calc 得 12276；
`15%7` 从「答 3（错）」变成调用 calc 得 1。

## 已知边界

1. **记忆与 RAG 是两件事**：`memory_search` 现在查的是长期记忆（v2，见
   [MEMORY.md](MEMORY.md)），不是文档知识库。要做文档问答，需要另建 collection
   并写 ingest（分块 → embed → 检索 → rerank），再加一个 `doc_search` 工具。
2. **显存**：统一 16384 上下文后占用约 5.76 GB / 8 GB，留了 ~2 GB 余量。
   别同时开两个 Agent 进程；`num_ctx` 全项目必须一致，否则 Ollama 会反复重载模型
   （实测能让一轮交互从 16 秒变成 90 秒）。
3. **评测集只有 20 条**：能反映趋势，不足以证明泛化。等真实使用积累 query 后补到 50 条。
4. **工具都是只读的**：写文件 / 执行命令这类，接入前必须先做 `dry_run` + 人工确认。
   兜底机制（代码替模型调工具）同样只适用于只读工具，绝不能推广到写操作。
5. **模型仍会漏调工具**：`小样本里仍会偶发「该调没调」，兜底机制能兜住算术/时间/记忆，
   但兜不住需要新工具的场景（比如问天气，没有工具就只能说不知道）。
6. **中文标点/别名**：路由是正则，遇到奇怪说法（「现在这个点」「几时」）可能漏判，
   漏判后 Agent 仍会自己决定，不会报错，只是少了强制纠正。

## 症状对照表（踩坑记录，修一个补一行）

| 症状 | 根因 | 修法 |
|---|---|---|
| 测试后真实库出现测试数据 | `-m` 包初始化先于环境变量绑定 store | episodes 懒加载 + `ensure_collection` 重绑定 |
| 摘要正文恒空 `done_reason=length` | 思维链吃光 num_predict | `chat_once(think=False)` 预填结束标记 |
| plan 记忆集体误判过期 | `due_ts` 兜底成 now() | `due_ts` 兜底必须为 0 |
| 一轮交互从 16s 变 90s | num_ctx 三处不一致致模型反复重载 | Modelfile/chat_stream/chat_once 统一 16384 |
| 原生 tools 通道 tool_calls 恒空 | 本模型模板渲染问题 | Agent 走 raw 自拼 ChatML（chat_once） |
