"""P3 Web UI：FastAPI + WebSocket，EventBus 的第二个订阅者。

- /            单页界面（立绘 + 聊天流 + 确认弹窗 + 拖拽上传）
- /ws          事件流：status/tool/answer/emo/confirm
- /upload      拖拽文件落沙箱 uploads/，回路径供对话引用
- confirm 事件阻塞等待前端应答（允许/拒绝），与终端 input 同语义

用法：python ui/server.py   （127.0.0.1:8766）
"""
import asyncio
import json
import os
import re
import sys
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse

import core_events
import main as core
import background_worker
from agent import remember
from config import APP_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="wenzhao-ui")

_loop = None
_conns: set = set()          # 多连接广播：新连接不再顶掉旧页面
_pending: dict[str, asyncio.Future] = {}
_history: list[dict] = []
_busy = threading.Lock()
_worker_started = False

_EMO_RE = re.compile(r"\[emo:(\w+)\]")


def _send_threadsafe(msg: dict):
    if _loop is None or not _conns:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(msg), _loop)


async def _broadcast(msg):
    data = json.dumps(msg, ensure_ascii=False)
    for ws in list(_conns):
        try:
            await ws.send_text(data)
        except Exception:
            _conns.discard(ws)


def _subscriber(kind, p):
    if kind == "confirm":
        # 阻塞等待前端应答（worker 线程），超时默认拒绝
        cid = uuid.uuid4().hex[:8]
        fut = asyncio.run_coroutine_threadsafe(_ask_confirm(cid, p), _loop)
        try:
            return bool(fut.result(timeout=120))
        except Exception:
            return False
    if kind == "answer":
        text = p.get("text", "")
        for emo in _EMO_RE.findall(text):
            _send_threadsafe({"kind": "emo", "emo": emo})
        _send_threadsafe({"kind": "answer", "text": text})
        return None
    _send_threadsafe({"kind": kind, **p})
    return None


async def _ask_confirm(cid, p):
    fut = _loop.create_future()
    _pending[cid] = fut
    await _broadcast({"kind": "confirm", "id": cid,
                      "question": p.get("question", "")})
    try:
        return await asyncio.wait_for(fut, timeout=120)
    except asyncio.TimeoutError:
        return False
    finally:
        _pending.pop(cid, None)


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(HERE, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/assets/character.jpg")
def character():
    p = os.path.join(HERE, "assets", "character.jpg")
    if os.path.isfile(p):
        return FileResponse(p)
    return HTMLResponse("no asset", status_code=404)


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "file.bin")
    name = re.sub(r"[^\w.\-一-鿿]", "_", name)
    dest = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex[:6]}_{name}")
    data = await file.read()
    with open(dest, "wb") as f:
        f.write(data)
    return {"path": dest, "name": name, "bytes": len(data)}


def _tts_push(answer: str):
    try:
        from senses import tts
        aid = uuid.uuid4().hex[:8]
        out = os.path.join(UPLOAD_DIR, f"tts_{aid}.mp3")
        if tts.synth(answer, out):
            _send_threadsafe({"kind": "audio", "url": f"/audio/{aid}"})
    except Exception:
        pass


def _run_turn(text: str, attachment: str = ""):
    with _busy:
        try:
            remember.set_turn(remember.new_turn())
            # 附件是图/视频 → 先自动视觉描述，描述并入上下文
            if attachment:
                from senses import vision
                from agent import see_image
                see_image.set_attachment(attachment)
                if vision.kind_of(attachment) in ("image", "video"):
                    _send_threadsafe({"kind": "status",
                                      "text": "[视觉] 正在读图/视频…"})
                    desc = vision.describe(attachment)
                    _send_threadsafe({"kind": "status",
                                      "text": "[视觉] " + desc[:120]})
                    text = f"{text}\n（附件媒体描述：{desc}）"
            use_agent, why = core.needs_agent(text)
            if use_agent:
                _send_threadsafe({"kind": "status", "text": f"[路由] {why}"})
                answer = core.run_agent_turn(text, _history)
                # 修复：Agent 最终答案此前只 print 到终端，网页看不到
                if answer:
                    for emo in _EMO_RE.findall(answer):
                        _send_threadsafe({"kind": "emo", "emo": emo})
                    _send_threadsafe({"kind": "answer", "text": answer})
            else:
                buf = []

                def on_token(t):
                    buf.append(t)
                    for emo in _EMO_RE.findall(t):
                        _send_threadsafe({"kind": "emo", "emo": emo})
                    _send_threadsafe({"kind": "token", "text": t})

                answer = core.chat_turn(text, _history, on_token=on_token)
                # 表情标签可能跨 chunk，结束时再扫一遍全文补发
                for emo in _EMO_RE.findall("".join(buf)):
                    _send_threadsafe({"kind": "emo", "emo": emo})
            _history.append({"role": "user", "content": text})
            if answer:
                _history.append({"role": "assistant", "content": answer})
            core.trim_history(_history)
            if answer:
                background_worker.enqueue_task(text, answer,
                                               turn_id=remember.CURRENT_TURN)
                threading.Thread(target=_tts_push, args=(answer,),
                                 daemon=True).start()
            _send_threadsafe({"kind": "turn_done"})
        except Exception as e:
            _send_threadsafe({"kind": "status", "text": f"[错误] {e}"})
            _send_threadsafe({"kind": "turn_done"})


@app.post("/asr")
async def asr(file: UploadFile = File(...)):
    name = re.sub(r"[^\w.\-]", "_", os.path.basename(file.filename or "a.webm"))
    dest = os.path.join(UPLOAD_DIR, f"asr_{uuid.uuid4().hex[:6]}_{name}")
    with open(dest, "wb") as f:
        f.write(await file.read())
    from senses import asr as asr_mod
    text = await asyncio.to_thread(asr_mod.transcribe, dest)
    return {"text": text}


@app.get("/audio/{aid}")
def audio(aid: str):
    if not re.fullmatch(r"[0-9a-f]{8}", aid):
        return HTMLResponse("bad id", status_code=400)
    p = os.path.join(UPLOAD_DIR, f"tts_{aid}.mp3")
    if os.path.isfile(p):
        return FileResponse(p, media_type="audio/mpeg")
    return HTMLResponse("no audio", status_code=404)


@app.websocket("/ws")
async def ws(sock: WebSocket):
    global _loop, _worker_started
    await sock.accept()
    _loop = asyncio.get_running_loop()
    _conns.add(sock)
    if not _worker_started:
        core_events.subscribe(_subscriber)
        background_worker.start_worker()
        _worker_started = True
    try:
        while True:
            raw = await sock.receive_text()
            msg = json.loads(raw)
            if msg.get("type") == "user":
                threading.Thread(target=_run_turn,
                                 args=(msg.get("text", ""),
                                       msg.get("attachment", "")),
                                 daemon=True).start()
            elif msg.get("type") == "confirm_reply":
                fut = _pending.get(msg.get("id"))
                if fut and not fut.done():
                    fut.set_result(bool(msg.get("allow")))
    except WebSocketDisconnect:
        pass
    finally:
        _conns.discard(sock)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8766, log_level="warning")