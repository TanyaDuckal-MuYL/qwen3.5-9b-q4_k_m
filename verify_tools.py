"""三步验证：模板 → 循环 → 路由。

用法（在项目根目录）：
    python verify_tools.py

三步全 PASS 才算 Agent 打通。
"""
import json
import sys
import time

import requests

from config import MODEL_NAME, OLLAMA_API

PASS, FAIL = "PASS", "FAIL"
results = []


def _check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}{('  → ' + detail) if detail else ''}")


def stage1_template():
    """工具 schema 是否能被模型看见并触发调用（raw prompt 模式）"""
    import time as _t

    from agent import tools
    from llm_client import chat_once

    sw = _t.time()
    msg = chat_once(
        [{"role": "system", "content": "你是任务执行器。需要时间就必须调用工具，禁止编造。"},
         {"role": "user", "content": "现在东京几点了？"}],
        tools=tools.schemas(), temperature=0.2, num_ctx=8192,
    )
    calls = msg.get("tool_calls") or []
    detail = f"{_t.time() - sw:.1f}s, tool_calls={json.dumps(calls, ensure_ascii=False)}"
    if not calls:
        detail += f" | raw={str(msg.get('raw'))[:160]}"
    _check("stage1 工具注入 (raw prompt)", bool(calls), detail)
    return bool(calls)


def stage2_loop():
    """完整 Agent 循环：能否调用工具并拿到真实结果"""
    from agent.loop import run_agent

    res = run_agent("现在几点了？", verbose=False)
    ok = "get_time" in res["tools_used"] and res["answer"]
    _check("stage2 循环调用 get_time", ok,
           f"{res['elapsed']}s, tools={res['tools_used']}, answer={res['answer'][:80]}")
    return ok


def stage3_multi():
    """多步任务：算 + 时间，验证上下文里能记住上一步"""
    from agent.loop import run_agent

    res = run_agent("帮我算 888*777，再告诉我今天几号", verbose=False)
    used = set(res["tools_used"])
    ok = {"calc", "get_time"} <= used and "689976" in res["answer"].replace(",", "").replace(" ", "")
    _check("stage3 多步任务", ok,
           f"{res['elapsed']}s, tools={res['tools_used']}, answer={res['answer'][:100]}")
    return ok


def stage4_router():
    """路由规则：闲聊不该触发工具，工具类意图不该漏判"""
    from agent.router import needs_agent

    cases = [
        ("你好呀", False),
        ("现在几点了", True),
        ("帮我算一下 12*34", True),
        ("你还记得我喜欢什么吗", True),
        ("讲个笑话", False),
        ("别用工具，直接告诉我现在几点", True),
    ]
    bad = [(t, needs_agent(t)[0]) for t, want in cases if needs_agent(t)[0] != want]
    _check("stage4 路由规则", not bad, f"误判: {bad}" if bad else f"{len(cases)} 条全对")
    return not bad


if __name__ == "__main__":
    print(f"模型: {MODEL_NAME}\n" + "=" * 60)
    ok1 = stage1_template()
    ok2 = stage2_loop()
    ok3 = stage3_multi()
    ok4 = stage4_router()
    print("=" * 60)
    if all([ok1, ok2, ok3, ok4]):
        print("全部通过：Agent 已打通。可以运行 python main.py 或 python eval/run_eval.py")
        sys.exit(0)
    print("有失败项。stage1 失败通常是 Modelfile 模板没生效：")
    print("  1) 重新 ollama create qwen3.5-9b-wenzhao -f qwen3.5-9b-wenzhao-Modelfile")
    print("  2) ollama show qwen3.5-9b-wenzhao --template  确认含 <tools></tools>")
    sys.exit(1)
