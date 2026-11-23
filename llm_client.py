import base64
import json
import re
import threading
from contextlib import contextmanager

import requests

from config import MODEL_NAME, OLLAMA_API, ANSI_GRAY, ANSI_RESET

# 原生 API 的 base（用于个别调试场景）
OLLAMA_BASE = OLLAMA_API.rsplit("/api/", 1)[0]

# P4 全局模型锁：8G 显存只允许一个大模型驻留。
# 前台推理 / 后台归纳 / 视觉切换 共用这把锁，杜绝并发爆显存。
_MODEL_LOCK = threading.RLock()


def unload(model: str) -> None:
    """keep_alive=0 立即卸载模型，释放显存。"""
    try:
        requests.post(OLLAMA_BASE + "/api/generate",
                      json={"model": model, "keep_alive": 0}, timeout=60)
    except Exception:
        pass


@contextmanager
def vision_slot(vision_model: str):
    """视觉互斥调度：卸主模型 → 视觉推理 → 卸视觉。
    主模型下次调用自动重载（约 6s，AGENT.md 实测）。"""
    with _MODEL_LOCK:
        unload(MODEL_NAME)
        try:
            yield
        finally:
            unload(vision_model)


def vision_chat(model: str, prompt: str, images: list[str]) -> str:
    """视觉模型非流式调用，images 为本地图片路径列表。"""
    imgs = []
    for p in images:
        with open(p, "rb") as f:
            imgs.append(base64.b64encode(f.read()).decode("ascii"))
    with _MODEL_LOCK:
        r = requests.post(OLLAMA_API, json={
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": imgs}],
            "stream": False, "keep_alive": "5m",
            "options": {"num_ctx": 8192, "temperature": 0.2},
        }, timeout=900)   # 视觉冷加载可能很慢，给足超时
        r.raise_for_status()
        return ((r.json().get("message") or {}).get("content") or "").strip()

# 非流式 / think=False 时，模型常在正文开头残留一个空的思考块，需要剥掉
_THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


def strip_think(text):
    """剥掉正文开头的 <think>...</think> 噪音"""
    if not text:
        return ""
    return _THINK_RE.sub("", text).strip()


# 人设阶段用：模型偶尔会在正文里写英文思考过程，或直接空回复
_ASCII_WORD = re.compile(r"[A-Za-z]{2,}")
_SYSTEM_CACHE: dict = {}


def get_model_system():
    """取 Modelfile 里 SYSTEM 人设原文（带缓存）"""
    if "text" in _SYSTEM_CACHE:
        return _SYSTEM_CACHE["text"]
    try:
        r = requests.post(OLLAMA_BASE + "/api/show",
                          json={"model": MODEL_NAME}, timeout=30).json()
        text = r.get("system") or ""
    except Exception:
        text = ""
    _SYSTEM_CACHE["text"] = text
    return text


def persona_reply(hint, history=None, attempts=2, temperature=0.85,
                  num_predict=300):
    """人设改写：raw prompt + 输出校验 + 最多重试一次。

    为什么不用流式：9B 在 think=False 时约 1/3 概率把英文分析写进正文，
    约 1/6 概率直接空回复。流式会把这些垃圾直接打到屏幕上，
    所以这里改成「先拿全、校验、必要时重试、再一次性打印」。
    """
    system = get_model_system()
    base = ([{"role": "system", "content": system}] if system else [])
    base = base + list(history or []) + [{"role": "user", "content": hint}]
    prompt = render_prompt(base, None)

    fallback = ""
    for _ in range(max(1, attempts)):
        try:
            with _MODEL_LOCK:
                data = requests.post(GENERATE_API, json={
                    "model": MODEL_NAME, "prompt": prompt, "raw": True, "stream": False,
                    "keep_alive": "30m",
                    "stop": ["<|im_end|>", "<|im_start|>"],
                    "options": {"temperature": temperature,
                                "num_ctx": 16384, "num_predict": num_predict},
                }, timeout=180).json()
        except Exception:
            continue

        text = strip_think(data.get("response") or "")
        if not text.strip():
            continue
        # 英文成串 = 模型把推理写进正文了，判为不合格
        if "<think>" in text or len(_ASCII_WORD.findall(text)) > 8:
            fallback = fallback or text
            continue
        return text.strip()

    return fallback.strip()


class _HeadBuffer:
    """流式打印时先攒住开头一小段，确认不是 <think> 噪音再吐出来"""

    def __init__(self, limit=120):
        self.buf = ""
        self.limit = limit
        self.done = False

    def feed(self, chunk):
        if self.done:
            return chunk
        self.buf += chunk

        if self.buf.lstrip().startswith("<think>"):
            if "</think>" in self.buf:            # 完整的思考块，整段丢弃
                self.done = True
                out = _THINK_RE.sub("", self.buf)
                self.buf = ""
                return out
            if len(self.buf) < 600:               # 还在思考块里，先不吐
                return ""
            # 超长还没闭合：说明模型在 think=False 下跑飞了，直接丢弃，别让它上屏
            self.done = True
            self.buf = ""
            return ""

        if len(self.buf) >= self.limit:           # 攒够了，正常输出
            self.done = True
            out = self.buf
            self.buf = ""
            return out
        return ""

    def flush(self):
        out = self.buf
        self.buf = ""
        self.done = True
        return out


def chat_stream(user_input, memory_context, history=None, think=True,
                temperature=None, num_predict=400, on_token=None):
    """使用原生 API 获取完整思考 + 回答（流式）

    think=True  : 展示原生思维链（纯聊天时用，人设更活）
    think=False : 不展示思考，用于 Agent 的人设改写阶段（省时间、避免干扰）
    """
    with _MODEL_LOCK:
        messages = []

        # 0) 人设 SYSTEM 必须显式注入：/api/chat 不会自动带 Modelfile 的 SYSTEM，
        #    不注入时人设只靠历史残留，表情标签等规范会失效（实测根因）
        persona = get_model_system()
        if persona:
            messages.append({"role": "system", "content": persona})

        # 1) 长期记忆：作为额外的 system 注入（放在最前面，权重高于历史）
        if memory_context:
            memory_prompt = (
                f"（你脑海中浮现出一些关于这个用户的记忆碎片：{memory_context}）\n"
                "这些是关于他的记忆碎片。若附带了【记忆清晰度】，就按它提示的语气来说：\n"
                "记得清楚就自然笃定，印象模糊就迟疑些，几乎想不起就坦白示弱撒娇。\n"
                "但无论哪一档，记忆里的**事实本身不许否认或改写**——你只是记不真切，不是它没发生。\n"
                "现在用文昭一贯的语气继续和他对话，自然一点，别让他发现你在偷偷回忆。"
            )
            messages.append({"role": "system", "content": memory_prompt})

        # 2) 把本会话的多轮历史原样接上
        if history:
            messages.extend(history)

        # 3) 当前这轮的用户输入
        messages.append({"role": "user", "content": user_input})

        options = {"num_ctx": 16384, "num_predict": num_predict}
        if temperature is not None:
            options["temperature"] = temperature

        payload = {
            "model": MODEL_NAME,
            "messages": messages,
            "think": bool(think),
            "stream": True,
            "keep_alive": "30m",   # 避免每次冷加载 6 秒
            # num_ctx 必须和 Agent 侧一致（16384），否则 Ollama 会重载模型
            "options": options,
        }

        thinking_buffer = ""
        answer_buffer = ""
        head = _HeadBuffer()
        labelled = False

        try:
            resp = requests.post(OLLAMA_API, json=payload, stream=True, timeout=300)
            resp.raise_for_status()

            for line in resp.iter_lines():
                if not line:
                    continue

                data = json.loads(line.decode('utf-8'))
                message = data.get('message') or {}

                thinking_chunk = message.get('thinking')
                if thinking_chunk:
                    if not thinking_buffer:
                        print(f"{ANSI_GRAY}[思考中] ", end="", flush=True)
                    thinking_buffer += thinking_chunk
                    print(f"{ANSI_GRAY}{thinking_chunk}", end="", flush=True)

                content_chunk = message.get('content')
                if content_chunk:
                    answer_buffer += content_chunk
                    visible = head.feed(content_chunk)
                    if visible:
                        if not labelled:
                            if thinking_buffer:
                                print(ANSI_RESET)
                            print("千问: ", end="", flush=True)
                            labelled = True
                        print(visible, end="", flush=True)
                        if on_token:
                            on_token(visible)

                if data.get('done'):
                    break

            tail = head.flush()
            if tail:
                if not labelled:
                    if thinking_buffer:
                        print(ANSI_RESET)
                    print("千问: ", end="", flush=True)
                    labelled = True
                print(tail, end="", flush=True)
                if on_token:
                    on_token(tail)
            if thinking_buffer:
                print(ANSI_RESET, end="")

        except requests.exceptions.ConnectionError:
            print("\n[错误] 无法连接到 Ollama 服务，请确保已运行 `ollama serve`")
        except Exception as e:
            print(f"\n[API调用失败] {e}")

        print()  # 换行收尾
        return thinking_buffer, strip_think(answer_buffer)


GENERATE_API = OLLAMA_BASE + "/api/generate"

# <tool_call> ... </tool_call> 里的 JSON
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)


def _tools_block(tools):
    """把工具 schema 渲染成 Qwen 认识的 <tools> 段（纯 JSON，不用模板变量）"""
    sigs = "\n".join(json.dumps(t["function"], ensure_ascii=False) for t in tools)
    return (
        "\n\n# Tools\n\n"
        "You are provided with function signatures within <tools></tools> XML tags:\n"
        f"<tools>\n{sigs}\n</tools>\n\n"
        "For each function call, return a json object with function name and "
        "arguments within <tool_call></tool_call> XML tags:\n"
        "<tool_call>\n"
        '{"name": <function-name>, "arguments": <args-json-object>}\n'
        "</tool_call>"
    )


def render_prompt(messages, tools=None, think=True):
    """自己拼 ChatML。

    为什么不直接用 /api/chat + 模板：实测本模型的 Modelfile 模板渲染 tools 时会
    让模型稳定输出空内容，而同样内容用干净 JSON 手拼 prompt 时 100% 能正确发出
    tool_call。自己拼的好处：完全可控、能加 stop、不依赖 Ollama 模板演进。
    """
    out = []
    tools_attached = False

    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""

        if role == "system":
            extra = ""
            if tools and not tools_attached:
                extra = _tools_block(tools)
                tools_attached = True
            out.append(f"<|im_start|>system\n{content}{extra}<|im_end|>\n")

        elif role == "user":
            out.append(f"<|im_start|>user\n{content}<|im_end|>\n")

        elif role == "assistant":
            body = content.strip()
            for c in (m.get("tool_calls") or []):
                fn = c.get("function") or {}
                body += ("\n<tool_call>\n"
                         + json.dumps({"name": fn.get("name"),
                                       "arguments": fn.get("arguments") or {}},
                                      ensure_ascii=False)
                         + "\n</tool_call>")
            out.append(f"<|im_start|>assistant\n{body}<|im_end|>\n")

        elif role == "tool":
            out.append("<|im_start|>user\n<tool_response>\n"
                       f"{content}\n</tool_response><|im_end|>\n")

    if tools and not tools_attached:
        out.insert(0, "<|im_start|>system\n" + _tools_block(tools).lstrip() + "<|im_end|>\n")

    # 关键：以 assistant 前缀结尾，模型直接从"回答/调工具"开始。
    # think=False：预填「思考已结束」，后台摘要类任务不浪费 token 在思维链上
    # （实测不关时 240 token 全被思考吃光，正文为空，done_reason=length）
    pre = "<|im_start|>assistant\n" + "<think>\n\n</think>\n"
    out.append("<|im_start|>assistant\n" if think else pre)
    return "".join(out)


def parse_tool_calls(text):
    """从模型输出里抠 <tool_call>{...}</tool_call>，容错解析"""
    calls = []
    for m in _TOOL_CALL_RE.finditer(text or ""):
        raw = m.group(1).strip()
        obj = None
        try:
            obj = json.loads(raw)
        except Exception:
            s, e = raw.find("{"), raw.rfind("}")
            if s >= 0 and e > s:
                try:
                    obj = json.loads(raw[s:e + 1])
                except Exception:
                    obj = None
        if not isinstance(obj, dict):
            continue
        fn = obj.get("function") if isinstance(obj.get("function"), dict) else obj
        name = fn.get("name") or obj.get("name")
        args = fn.get("arguments", obj.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name:
            calls.append({"function": {"name": name, "arguments": args or {}}})
    return calls


def chat_once(messages, tools=None, temperature=0.2, num_ctx=16384,
              num_predict=400, timeout=300, think=True):
    """Agent 用的非流式调用（raw prompt 模式）。

    返回 {"content": 去掉 tool_call 的正文, "tool_calls": [...], "raw": 原始输出}
    """
    payload = {
        "model": MODEL_NAME,
        "prompt": render_prompt(messages, tools, think=think),
        "raw": True,
        "stream": False,
        "keep_alive": "30m",
        "stop": ["<|im_end|>", "<|im_start|>"],
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    with _MODEL_LOCK:
        resp = requests.post(GENERATE_API, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    raw = data.get("response") or ""
    return {
        "content": strip_think(_TOOL_CALL_RE.sub("", raw)).strip(),
        "tool_calls": parse_tool_calls(raw),
        "raw": raw,
    }


def generate_summary(user_input, model_output):
    """非流式调用模型，提取对话中的客观事实"""
    summary_prompt = f"""你是一个后台记忆提取程序。请分析以下用户与AI的对话，提取出关于用户的客观事实、偏好、个人信息或重要决定。
要求：
1. 用一句简练的陈述句总结（例如：“用户喜欢吃苹果”、“用户的名字是张三”）。
2. 如果对话只是日常寒暄、没有实质性信息，或者无法提取有效事实，请仅回复“无”。
3. 不要包含任何解释，不要使用傲娇或任何角色扮演语气，直接输出总结内容或“无”。

用户输入：{user_input}
AI回复：{model_output}

总结："""

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": "你是一个无情的后台信息提取工具，只输出客观事实总结，绝不带入任何角色设定。"},
            {"role": "user", "content": summary_prompt}
        ],
        "stream": False,
        # 后台归纳要快：上下文砍到 4096、最多 120 token，否则会和前台的
        # Agent 抢显存/排队，实测能把一轮交互拖到 90 秒
        "options": {"temperature": 0.1, "num_ctx": 16384, "num_predict": 120}
    }
    with _MODEL_LOCK:
        resp = requests.post(OLLAMA_API, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()

    return strip_think(data.get('message', {}).get('content', ''))
