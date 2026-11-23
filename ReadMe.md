# 傲娇千问 V7.1 (工程化 + 队列防并发 + 智能记忆)

> **V8 更新**：已加入 Agent 工具调用（`memory_search` / `get_time` / `calc`）+ 两段式人设，
> 详见 [AGENT.md](AGENT.md)。Agent 部分不依赖 Ollama 的模板工具机制，自己拼 ChatML。

一个基于 Ollama 本地大模型, 带有长期记忆、自动归纳、思维链展示的工程化 AI 助手终端.

## 核心特性

- 工程化架构: 模块化设计, 分离配置、LLM 交互、记忆管理与后台任务, 高内聚低耦合.
- 队列防并发: 采用单消费者队列 (Single-Consumer Queue) 处理后台记忆总结, 杜绝并发导致的显存溢出或数据库锁冲突.
- 智能记忆去重: 写入新记忆前自动做向量最近邻比对, 语义距离小于阈值则刷新旧记忆时间戳 (模拟复习), 避免数据库冗余膨胀.
- 记忆时间衰减: 检索时引入指数衰减算法, 综合得分 = 语义相似度 x 时间权重, 让近期记忆优先, 久未提及的旧记忆自然沉底.
- 原生思维链 (Think): 以灰色字体展示大模型原生思考过程, 推理透明可见.
- 流式输出: 对话流式返回, 打字机效果, 前台零延迟.

## 目录结构

project_root/
    main.py                 # 主程序入口, 处理用户交互和命令路由
    config.py               # 全局配置 (模型名、API、路径、颜色常量)
    memory_manager.py       # 向量数据库管理 (增删改查 + 去重 + 衰减)
    llm_client.py           # Ollama API 交互 (流式对话、非流式总结)
    background_worker.py    # 后台队列与单线程 Worker (防并发核心)

## 快速开始

1. 环境准备
   - 确保已安装并运行 Ollama.
   - 拉取所需模型:
       ollama pull qwen3.5-9b-tsundere   # 自定义傲娇模型
       ollama pull nomic-embed-text      # 记忆向量化 Embedding 模型
   - 安装 Python 依赖:
       pip install requests chromadb ollama

2. 运行程序
       python main.py

## 交互指令

- /view            查看当前记忆库中的所有记忆
- /add <内容>      手动添加一条记忆 (同样走去重逻辑)
- /del <关键词>    删除包含指定关键词的记忆
- exit / quit / 退出   安全退出程序

## 记忆机制说明

自动归纳
每次对话结束后, 后台队列静默调用大模型提取对话中的客观事实 (如"用户喜欢秀秀"), 经单线程排队后写入向量数据库, 不阻塞前台.

去重机制 (save_memory)
存入前先用新向量做一次 query. 若最近邻余弦距离小于 dedup_threshold (默认 0.1, 即语义几乎一致), 则不新增, 而是调用 collection.update 把那条旧记忆的 timestamp 刷新为当前时间, 等价于复习, 旧记忆重新变新鲜.

衰减机制 (retrieve_memory)
检索时排序不只看语义匹配度, 还会乘以时间衰减系数 exp(-decay_factor x days_ago) (decay_factor 默认 0.05, 约 14 天权重减半). 最终得分 = (1 - 距离) x 时间权重. 因此即使两条记忆语义相似度相同, 昨天聊过的也会排在一个月前聊过的前面; 衰减只调排序, 不会删除任何记忆.

可调参数
- save_memory(..., dedup_threshold=0.1): 越小越严格, 越大越容易把相近内容判为重复.
- retrieve_memory(..., decay_factor=0.05): 越大遗忘越快、越偏好新记忆; 设为 0 即关闭时间衰减.
