# 长期记忆系统 v2

> 替换了原来的「去重 + 时间衰减」向量搜索。核心变化：记忆有了**状态**（新增/强化/作废/打盹/归档）
> 和**判定**（该不该记、和旧记忆什么关系）。

## 一句话

`memory.remember_turn()` 负责记，`memory.recall()` 负责想，`memory.nightly()` 负责忘。

## 目录

```
memory/
├── schema.py        # 类型、谓词基数、半衰期、权重、阈值（所有魔法数字）
├── store.py         # Chroma 读写 + L0 操作日志（logs/memory_ops.jsonl）
├── extract.py       # 模型抽取：对话 → 候选 ops（严格 JSON + 重试）
├── decide.py        # 决策矩阵：REINFORCE / SUPERSEDE / ADD
├── query.py         # 查询意图 → 谓词（弥补 nomic 的中文短板）
├── score.py         # 五因子打分 + MMR + 注入预算
├── decay.py         # 强度衰减、召回强化、三档遗忘
├── consolidate.py   # 夜间归并（同主题碎片 → 几条 canonical）
├── calibrate.py     # 阈值标定（换 embedding 后必跑）
├── episodes.py      # L0 对话原文 + L3 情景摘要（时间线倒排检索）
├── validate.py      # 12 条基础验收
├── validate_mp2.py  # MERGE / plan 截止日 / pin 6 条
├── validate_mp3.py  # 强度话术 4 条
├── validate_mp4.py  # 情景层 8 条
└── validate_all.py  # 统一回归入口（30 条）
```

> V4 遗留（`memory_middleware.py`、`peek_memory.py`、`test_vector_db..py`、
> 根目录 `memory_manager.py`）已于 2026-11-22 经用户确认送入回收站，主库单一入口。
>
> **隔离红线**：`memory/__init__.py` 不得顶层 import 任何会拉起 store 的模块
> （episodes 已改懒加载）。否则 `-m memory.xxx` 的包初始化会先于脚本设置
> `MEM_COLLECTION` 绑定 store，测试将写进真实库。store/episodes 另有
> `ensure_collection()` 重绑定保护兜底。

## 数据模型

每条记忆的 metadata：

| 字段 | 说明 |
|---|---|
| `type` | identity / relation / preference / habit / event / plan / boundary / transient |
| `status` | active（可检索）/ dormant（打盹）/ archived（归档）/ superseded（被覆盖） |
| `subject` / `predicate` / `obj` | 结构化三元组，判重靠它 |
| `polarity` | pos / neg（"不喜欢"是 neg） |
| `importance` | 模型给 0~1 |
| `storage_strength` | 存储强度，每次召回增加 |
| `recall_count` | 被召回次数 |
| `created_ts` / `updated_ts` / `last_recall_ts` | 时间戳 |
| `superseded_by` | 被谁取代（版本链） |
| `evidence` | 用户原话片段 |

## 写入：三种结果

```
抽取（模型） → 决策（规则） → 入库
```

| 结果 | 触发条件 | 动作 |
|---|---|---|
| **REINFORCE** | 同谓词 + 同对象 + 同极性（重复陈述） | 只加强度与召回计数，不新增 |
| **SUPERSEDE** | 同谓词同对象但极性相反，或单值谓词换了值 | 新行 active，旧行 `status=superseded` |
| **ADD** | 多值谓词的新值 / 全新主题 | 并列新增 |

### 谓词归一（防止同一件事来回覆盖）

| 别名 | 归一为 |
|---|---|
| 叫 / 全名 / 昵称 / 称呼 / 大名 | 名字 |
| 家在 / 所在地 / 家乡 | 住在 |
| 忌口 | 过敏 |
| 工作 | 职业 |
| 爱好 / 讨厌 / 不喜欢 / 反感 | 喜欢（讨厌类极性命为 neg） |

没有这张表时，「用户叫大林」和「用户名字叫大林」会互相 SUPERSEDE，每提一次就多一条废记录。

**关键决策：判重不靠向量距离。** 实测 `nomic-embed-text` 把「喜欢喝奶茶」和
「讨厌喝奶茶」编码成相同向量（距离 0.000），靠距离判重会把偏好搅成一团。
所以主依据是 `谓词 + 对象 + 极性` 的字符串比对，向量距离只在没有对象时兜底。

### 谓词基数（schema.py）

- **单值**：名字、生日、年龄、职业、住在、计划、关系、宠物 → 新值覆盖旧值
- **多值**：喜欢、讨厌、爱好、常吃、常喝、习惯 → 并列累积

否定归一：`讨厌 X` / `受不了 X` / `不喜欢 X` 统一成 `(喜欢, neg)`，
所以「喜欢咖啡」遇到「不喜欢咖啡」必然触发 SUPERSEDE。

## 检索：先按意图缩范围，再打分

纯向量检索在 nomic 上不可用（同一个 query 对三条不相关记忆的相似度几乎一样），
所以分两步：

1. **查询意图识别**（`query.py`）：把「我叫什么」映射到谓词「名字」，
   把「我有什么忌口」映射到「过敏」。谓词是封闭集合，规则比向量稳。
   识别到谓词但库里没有 → 直接返回空（"不知道"），**绝不要把全库倒出来**。
2. **范围内打分**：五因子加权 + MMR 去冗余，见下。

```
score = 0.50×相似度 + 0.20×重要性 + 0.15×记忆强度 + 0.10×新鲜度 + 0.05×置信度
```

- 低于 `MIN_SCORE=0.28` 不进 prompt
- **MMR** 去冗余（λ=0.7），避免 top-5 全是同一件事的不同说法
- 预算：检索 ≤5 条 / 1200 字符；档案层 ≤5 条 / 400 字符
- 命中即强化：`storage_strength += 1.5 × (1 - 当前强度)`

常驻档案层（`profile()`）：identity / boundary / importance≥0.8 的记忆每轮直接注入，
不靠相似度碰运气——「我叫什么」这种问题必须 100% 命中。

## 遗忘：三档衰减 + 两个豁免

| 档位 | 条件 | 结果 |
|---|---|---|
| 打盹 | 强度 < 0.20 | `status=dormant`，不再检索，保留 |
| 归档 | dormant 90 天且强度 < 0.05 | `status=archived` |
| 删除 | archived 365 天且 `recall_count==0` | 物理删除 |

- **豁免**：`identity` / `boundary` 永不衰减、永不删除；`pinned` 同样豁免
- 强度 = 存储强度 × `0.5^(天数/半衰期)`，半衰期按类型：身份 ∞、偏好 180 天、
  事件 30 天、临时状态 1 天
- `memory.nightly()` = 状态整理 + 碎片归并，可手动 `/night` 触发

## 归并

同一 `subject+predicate` 超过 6 条时，让模型炼成 ≤3 条更准确的说法，
原条目全部 `superseded`。这是防止「50 条用户喜欢奶茶」的唯一有效手段。

实测：9 条「用户喜欢吃X」→ 2 条：
> 用户喜欢苹果、香蕉、西瓜、葡萄、橙子、草莓、芒果和桃子等水果。

## 阈值标定

```powershell
python -m memory.calibrate
```

用 12 组同义对 + 12 组异构对量距离分布，输出 `memory/thresholds.json`（schema 自动加载）。

**当前结论：nomic-embed-text 在中文上不可用**（同义 P95 = 0.263，异构 P05 = 0.000，
分布完全重叠）。系统已做成不依赖 embedding 质量，但换 `bge-m3` 后语义召回会明显变好：

```powershell
ollama pull bge-m3
# 然后：删掉 memory/thresholds.json，重跑 memory.calibrate，重建 collection
```

## 验收

```powershell
python -m memory.validate
```

12 条用例覆盖：身份入库、档案命中、多值并列、改名覆盖、偏好反转、重复强化、
寒暄过滤、一次性提问过滤、事件沉底、身份抗衰减、碎片归并、关键词遗忘。

**当前结果：12/12 通过。**

## 怎么调

| 想要 | 改哪 |
|---|---|
| 记更多/更少 | `extract.py` 的 SYSTEM 提示（宁缺毋滥那几条） |
| 更早遗忘 | `schema.py` 的 `HALF_LIFE` / `STRENGTH_DORMANT` |
| 更严格判重 | `schema.py` 的 `D_DUP`（只影响无对象的兜底路径） |
| 更多记忆进 prompt | `RECALL_K` / `RECALL_CHAR_BUDGET` |
| 归并更积极 | `CONSOLIDATE_MIN_GROUP`（现在 6） |

## 已知限制

1. **embedding 仍是 nomic**：中文语义召回偏弱（尤其是近义词、不同说法）。
   换 `bge-m3` 需要联网，本次环境拉取失败。缓解手段是查询意图路由（谓词规则）。
2. **抽取依赖 9B 模型的判断**：约 10~20% 的轮次会漏抽或抽错谓词。
   谓词错会导致「该覆盖的变成并列」，因此 `validate.py` 要定期跑。
3. **单值谓词覆盖较激进**：同一谓词下第二个不同对象会直接覆盖旧值。
   如果用户的某个「单值」其实是多值（比如"宠物"其实有两只），
   把 `schema.PREDICATE_CARDINALITY` 里那一项改成 `MULTI`。
4. **记忆写入是异步的**：`main.py` 每轮开始前 `background_worker.drain(20)`
   等队列清空，否则用户接着问「我叫什么」会检索不到（实测抽取要 8~15 秒）。
5. **人设与事实会打架**：模型曾把「用户叫大林」当成说它自己，并否认。
   已在 `persona_pass` 的提示里明确「底稿讲的是对方，不是你，不许否认」。
6. **没有 L3 情景摘要**：跨多轮的事件时间线还检索不好（比如"上周我们聊的那个项目"）。
