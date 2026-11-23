"""OpenAI 兼容接口：/v1/models + /v1/chat/completions（流式/非流式）。

让 aider/Qwen Code 等轻 harness 把文昭当后端模型调；人设随 Modelfile 走。
- 不带 tools：走 /api/chat 模板通道，人格完整，支持 SSE 流式。
- 带 tools：走 chat_once raw 通道，返回标准 OpenAI tool_calls。

用法：python openai_server.py   （默认 127.0.0.1:8765）
"""
import json
import time
import uuid

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse

import llm_client
from config import MODEL_NAME

app = FastAPI(title="wenzhao-openai-compat")


def _now():
    return int(time.time())


def _cid():
    return "chatcmpl-" + uuid.uuid4().hex[:12]


@app.get("/v1/models")
def models():
    return {"object": "list",
            "data": [{"id": MODEL_NAME, "object": "model",
                      "created": _now(), "owned_by": "wenzhao"}]}


def _to_messages(body):
    msgs = []
    for m in body.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content") or ""
        if isinstance(content, list):  # 多模态占位：只取文本部分
            content = " ".join(c.get("text", "") for c in content
                               if isinstance(c, dict))
        msgs.append({"role": role, "content": content})
    return msgs


@app.post("/v1/chat/completions")
def chat(body: dict):
    msgs = _to_messages(body)
    tools = body.get("tools")
    stream = bool(body.get("stream"))
    cid = _cid()

    if tools:
        r = llm_client.chat_once(msgs, tools=tools,
                                 temperature=float(body.get("temperature", 0.7)))
        tcs = []
        for i, c in enumerate(r.get("tool_calls") or []):
            fn = c.get("function") or {}
            tcs.append({"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                        "index": i,
                        "function": {"name": fn.get("name", ""),
                                     "arguments": json.dumps(
                                         fn.get("arguments") or {},
                                         ensure_ascii=False)}})
        message = {"role": "assistant", "content": r.get("content") or None}
        if tcs:
            message["tool_calls"] = tcs
        return {"id": cid, "object": "chat.completion", "created": _now(),
                "model": MODEL_NAME,
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if tcs else "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                          "total_tokens": 0}}

    if stream:
        def gen():
            payload = {"model": MODEL_NAME, "messages": msgs, "stream": True,
                       "keep_alive": "30m",
                       "options": {"num_ctx": 16384,
                                   "temperature": float(body.get("temperature", 1.0))}}
            first = True
            with requests.post(llm_client.OLLAMA_API, json=payload,
                               stream=True, timeout=300) as resp:
                for line in resp.iter_lines():
                    if not line:
                        continue
                    d = json.loads(line.decode("utf-8"))
                    chunk = (d.get("message") or {}).get("content") or ""
                    if chunk:
                        delta = {"content": chunk}
                        if first:
                            delta["role"] = "assistant"
                            first = False
                        yield ("data: " + json.dumps(
                            {"id": cid, "object": "chat.completion.chunk",
                             "created": _now(), "model": MODEL_NAME,
                             "choices": [{"index": 0, "delta": delta,
                                          "finish_reason": None}]},
                            ensure_ascii=False) + "\n\n")
                    if d.get("done"):
                        yield ("data: " + json.dumps(
                            {"id": cid, "object": "chat.completion.chunk",
                             "created": _now(), "model": MODEL_NAME,
                             "choices": [{"index": 0, "delta": {},
                                          "finish_reason": "stop"}]},
                            ensure_ascii=False) + "\n\n")
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    payload = {"model": MODEL_NAME, "messages": msgs, "stream": False,
               "keep_alive": "30m",
               "options": {"num_ctx": 16384,
                           "temperature": float(body.get("temperature", 1.0))}}
    d = requests.post(llm_client.OLLAMA_API, json=payload, timeout=300).json()
    content = (d.get("message") or {}).get("content") or ""
    return {"id": cid, "object": "chat.completion", "created": _now(),
            "model": MODEL_NAME,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": d.get("prompt_eval_count", 0),
                      "completion_tokens": d.get("eval_count", 0),
                      "total_tokens": d.get("prompt_eval_count", 0)
                      + d.get("eval_count", 0)}}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")