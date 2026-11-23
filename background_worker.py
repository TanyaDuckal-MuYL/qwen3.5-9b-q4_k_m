import queue
import threading
import time
from config import ANSI_GRAY, ANSI_RESET
import llm_client
import memory

# 创建一个线程安全的队列
task_queue = queue.Queue()

# 会话标识：进程启动时生成，L0 日志用它区分批次
SESSION_ID = "s_" + str(int(time.time()))

# 这些内容不值得记进长期记忆：Agent 的兜底文案、错误信息、极短回复
SKIP_PREFIXES = ("ERROR", "（没有拿到", "（步数", "[Agent 失败]", "（工具")


def _should_skip(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 4:
        return True
    return s.startswith(SKIP_PREFIXES)


def _worker_loop():
    """
    【核心防并发机制】
    后台只有一个线程在死循环运行。
    它通过 queue.get() 阻塞等待任务。前一个任务没处理完（没调用 get 下一个），
    后续任务就会安全地堆积在队列中，绝不会同时开启多个线程或并发调用大模型。

    注意：Agent 的工具调用是前台同步执行的，与这里共用同一个 Ollama 实例，
    所以 Agent 运行时本队列可能排队变慢，这是预期行为（保护 8G 显存不被打爆）。
    """
    while True:
        # 阻塞等待，直到主线程放入新任务
        user_input, model_output, turn_id = task_queue.get()

        try:
            # 只归纳「用户说的话 + 最终回复」，工具中间产物不参与
            if _should_skip(model_output):
                task_queue.task_done()
                continue

            # G1 负载闸：余量不足把任务放回队尾延后（不丢），不抢前台
            import gpu_gate
            ok, why = gpu_gate.gate("记忆归纳")
            if not ok:
                print(f"{ANSI_GRAY}[负载闸] {why}{ANSI_RESET}")
                task_queue.put((user_input, model_output, turn_id))
                task_queue.task_done()
                time.sleep(5)
                continue

            # L0：对话原文只追加落盘（情景摘要的原料）
            memory.episodes.log_turn(user_input, model_output, SESSION_ID)

            # 抽取 → 同轮去重（remember 已记的不再记）→ 决策 → 入库
            from memory import decide, extract, store
            cands = extract.extract(user_input, model_output)
            if turn_id and cands:
                tool_facts = [f for f in store.all_facts(None)
                              if f["meta"].get("source") == "tool"
                              and f["meta"].get("turn_id") == turn_id]
                kept = []
                for c in cands:
                    # 同轮去重只比谓词+对象，忽略极性：
                    # 「用户对香菜过敏」(pos) 与抽取候选 (过敏,neg) 是同一件事
                    cp, _ = decide._canon(c.predicate, c.polarity)
                    dup = any(
                        cp == decide._canon(t["meta"].get("predicate"),
                                            t["meta"].get("polarity", "pos"))[0]
                        and decide._norm(c.obj)
                        and decide._norm(c.obj) == decide._norm(t["meta"].get("obj"))
                        for t in tool_facts)
                    if not dup:
                        kept.append(c)
                cands = kept
            ops = decide.apply(cands) if cands else []
            for op in ops:
                icon = {"ADD": "＋", "REINFORCE": "↻", "SUPERSEDE": "⇄",
                        "MERGE": "⇶"}.get(op["op"], "?")
                print(f"{ANSI_GRAY}[记忆] {icon} {op['op']:<9} "
                      f"{str(op.get('text', ''))[:36]}{ANSI_RESET}")

            # L3：每 20 轮压一章情景摘要（后台串行，不抢前台显存）
            ep = memory.episodes.maybe_summarize()
            if ep:
                print(f"{ANSI_GRAY}[情景] 新章节 {ep['date']} "
                      f"覆盖 {ep['turns']} 轮{ANSI_RESET}")

        except Exception as e:
            print(f"{ANSI_GRAY}[自动记忆后台异常] {e}{ANSI_RESET}")
        finally:
            # 标记当前任务已完成，允许队列处理下一个
            task_queue.task_done()


def start_worker():
    """启动后台守护线程"""
    thread = threading.Thread(target=_worker_loop, daemon=True)
    thread.start()


def enqueue_task(user_input, model_output, turn_id=""):
    """主线程调用此方法，将任务安全地推入队列"""
    if model_output and model_output.strip():
        task_queue.put((user_input, model_output, turn_id))


def drain(timeout=8.0):
    """等待队列清空。

    为什么需要：记忆抽取是异步的，如果用户下一句马上问「我叫什么」，
    上一轮的抽取可能还没跑完，就会检索不到。每轮开始前拖一下队列，
    最多等 timeout 秒。
    """
    import time
    deadline = time.time() + timeout
    while task_queue.unfinished_tasks and time.time() < deadline:
        time.sleep(0.15)
