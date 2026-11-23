"""EventBus：进度/确认/答案的统一事件出口。

终端订阅者把事件渲染成原来的 ANSI 打印（观感不变）；
P3 的 Web UI 只需再挂一个 WebSocket 订阅者，核心代码零改动。

事件 kind：
  status  普通进度（灰字）
  tool    工具调用与结果摘要
  answer  最终答案（人设段）
  confirm 写操作人工确认请求（订阅者返回 True/False）
"""
import threading

_lock = threading.Lock()
_subs = []


def subscribe(fn):
    with _lock:
        _subs.append(fn)


def unsubscribe(fn):
    with _lock:
        if fn in _subs:
            _subs.remove(fn)


def emit(kind, **payload):
    with _lock:
        subs = list(_subs)
    for fn in subs:
        try:
            fn(kind, payload)
        except Exception:
            pass


def confirm(question: str) -> bool:
    """写操作确认：依次问订阅者，任一明确拒绝即拒绝；无人应答默认拒绝。"""
    with _lock:
        subs = list(_subs)
    answered = False
    for fn in subs:
        try:
            r = fn("confirm", {"question": question})
        except Exception:
            continue
        if r is None:
            continue
        answered = True
        if not r:
            return False
    return answered


def _terminal(kind, p):
    from config import ANSI_GRAY, ANSI_RESET
    if kind == "confirm":
        try:
            ans = input(p["question"] + " (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans == "y"
    if kind == "status":
        print(f"{ANSI_GRAY}{p.get('text', '')}{ANSI_RESET}")
    elif kind == "tool":
        print(f"{ANSI_GRAY}  · {p.get('text', '')}{ANSI_RESET}")
    elif kind == "answer":
        print(p.get("text", ""))
    return None


def install_terminal():
    subscribe(_terminal)