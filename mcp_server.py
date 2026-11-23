"""MCP server（stdio JSON-RPC 2.0，零第三方依赖）。

把 fs/web 工具以 MCP 协议暴露，外部 MCP 客户端可即插即用。
安全约定：服务端无人值守，fs 的 write/delete 一律拒绝（只读开放）；
写操作必须走带人工确认的本地 Agent 通道。

用法：python mcp_server.py   （stdin/stdout 行分隔 JSON-RPC）
"""
import json
import sys

from agent import tools as tool_mod
from agent import file_ops, web  # noqa: F401  注册 fs/web

PROTOCOL = "2024-11-05"
SERVER_TOOLS = ["fs", "web"]


def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, msg):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}}


def handle(msg: dict):
    method = msg.get("method", "")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "wenzhao-workbench", "version": "1.0"},
        })
    if method in ("notifications/initialized", "initialized"):
        return None  # notification，无回包
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        out = []
        for n in SERVER_TOOLS:
            t = tool_mod.REGISTRY.get(n)
            if t:
                out.append({"name": t.name, "description": t.desc,
                            "inputSchema": t.schema})
        return _result(req_id, {"tools": out})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name not in SERVER_TOOLS:
            return _error(req_id, -32602, f"unknown tool {name}")
        if name == "fs" and (args.get("op") or args.get("action")) in ("write", "delete"):
            return _result(req_id, {"content": [{"type": "text",
                           "text": "ERROR: MCP 服务端拒绝写操作，请走本地确认通道"}],
                           "isError": True})
        out = tool_mod.call(name, args)
        return _result(req_id, {"content": [{"type": "text", "text": out}],
                                "isError": out.startswith("ERROR")})
    if req_id is not None:
        return _error(req_id, -32601, f"method not found: {method}")
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()