# 千问娘·文昭 · 使用方法

> 本文件为 ReadMe.md 的更新版（原文件受系统权限保护无法原地修改）。
> 人设「文昭」按《AI娘人设讨论之千问》文案 + 虚拟恋人设定；模型 `qwen3.5-9b-wenzhao`。
> 设计总纲见 [千问娘助手改造方案.md](千问娘助手改造方案.md)，记忆细节见 MEMORY.md。

本地 9B（Q4）驱动的电脑助手：终端 + Web 界面双形态，能读写文件、联网、
读图读视频、听会说，带长期记忆与情景摘要，写操作必须人工确认。

---

## 0. 环境准备（一次性）

```powershell
# Python 3.14（全局默认已切到 3.14；依赖装在该解释器下）
pip install chromadb ollama requests fastapi uvicorn websockets python-multipart ^
            send2trash bs4 trafilatura faster-whisper edge-tts opencv-python Pillow

# Ollama 模型
ollama create qwen3.5-9b-wenzhao -f qwen3.5-9b-wenzhao-Modelfile   # 主模型+人设
ollama pull bge-m3          # 记忆向量（中文）
ollama pull qwen2.5vl:7b    # 视觉（读图/视频，P4）
```

硬件基线：RTX 3060 Ti 8G / 32G 内存。主模型与视觉模型**不共驻**，
由模型调度器自动卸载切换（切换约 6s，属预期）。

## 1. 终端形态

```powershell
python main.py
```

- 直接说话：闲聊走流式人设通道；命中工具意图自动走 Agent。
- 指令：`/agent <任务>` 强制工具 | `/chat <内容>` 强制闲聊 | `/tools` `/skills`
  `/view` `/mem` `/eps`（情景章节）| `/pin 关键词` 锁定记忆 `/unpin` 解锁
  `/add` `/del` `/forget` | `/night` 夜间维护（衰减+归并+体积治理）| `exit` 退出（自动写交接+尾章）。
- 写文件/删除/记记忆会弹 `(y/N)` 确认，拒绝即改道。

## 2. Web 界面（推荐日常使用）

```powershell
python ui/server.py        # 监听 127.0.0.1:8766
```

浏览器打开 **http://127.0.0.1:8766**：

- 左侧立绘 + 表情徽章（随回复实时切换：温和/脸红/得意/走神/两眼放光/无奈）。
- 右侧聊天流：打字机流式；工具步骤灰字显示；**写操作弹确认窗，点「允许/拒绝」**。
- 📎 选择文件 / 直接拖拽进页面 → 上传到 `uploads/`；
  **拖图片/视频会自动先读图**，描述并入对话；也可在输入框引用路径追问。
- 🎤 点一下开始录音、再点一下停止 → 本地转写成文字自动发送（需浏览器麦克风权限）。
- 「语音」勾选时，她的回复会自动朗读（edge-tts 晓伊音色，需联网；离线降级本机语音）。

## 3. OpenAI 兼容接口（把文昭当后端模型）

```powershell
python openai_server.py    # 127.0.0.1:8765
```

aider / Qwen Code 等客户端把 base_url 指到 `http://127.0.0.1:8765/v1` 即可：
`/v1/models`、`/v1/chat/completions`（SSE 流式 / 非流式 / 带 tools 返回标准 tool_calls）。
人设随 Modelfile 走，换外壳她还是文昭。

## 4. MCP（给外部客户端暴露工具）

```powershell
python mcp_server.py       # stdio JSON-RPC，暴露 fs/web（只读；写操作服务端拒绝）
```

本地 Agent 接外部 MCP：在 `config.py` 的 `MCP_SERVERS` 填
`{"名字": ["python", "服务脚本.py"]}`，工具以 `mcp_<名字>_<工具>` 自动注册。

## 5. 多模态能力说明（P4）

| 能力 | 入口 | 实现 | 限制 |
|---|---|---|---|
| 听 | UI 🎤 / `senses.asr.transcribe` | faster-whisper small/int8，CPU | 首次用下载模型；浏览器需麦克风权限 |
| 说 | UI 语音勾选 / `senses.tts.synth` | edge-tts（联网）→ pyttsx3 兜底 | 离线音质机械 |
| 看图 | 拖图自动 / `see_image` 工具 | qwen2.5vl:7b | 切换模型约 6s |
| 看视频 | 拖视频自动 | 每秒 1 帧≤6 帧逐帧描述 | 长视频只采样前 6 秒 |

## 6. 验收与回归

```powershell
python -m memory.validate_all     # 记忆 30 条
python -m memory.validate_guard   # G0 反例（含必然失败 canary）
python -m memory.validate_g1      # 体积红线/checksum/负载闸
python -m memory.validate_p1      # 技能/沙箱/双写
python -m memory.validate_p2      # MCP/OpenAI
python -m memory.validate_p3      # Web UI 事件流/确认两路
python -m memory.validate_p4      # TTS/ASR/视觉/调度
python verify_tools.py            # 工具链路
python verify_persona.py          # 人设四项
```

## 7. 已知限制

- 8G 显存：主模型+视觉不共驻，读图时主模型短暂卸载（约 6s 重载）。
- 9B 小模型：列表复述/参数别名靠确定性兜底纠正；偶发漏调工具由 nudge 补。
- edge-tts 依赖网络与微软服务；ASR 首次需下载 whisper-small。
- 写操作（fs 写删、remember）永远要人工确认，MCP 服务端直接拒绝写。
- 全局 PATH 已切 3.14；**已开的旧终端仍是旧解释器**，新开终端才生效。
- Web UI 已改多连接广播：开新标签/重连不会顶掉旧页面（旧版单连接会失聪，已修）。
- 界面经 ComputerUse 真实验收：闲聊流式+表情、工具步骤可见、写操作确认弹窗
  允许后落盘、Agent 答案气泡可见、TTS 自动播放，均通过。
- 自动化输入中文时 SendInput 可能丢字，验收改用 UIA set_value 完整写入。