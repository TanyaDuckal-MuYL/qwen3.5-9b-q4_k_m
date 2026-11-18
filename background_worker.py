import queue
import threading
from config import ANSI_GRAY, ANSI_RESET
import llm_client
import memory_manager

# 创建一个线程安全的队列
task_queue = queue.Queue()

def _worker_loop():
    """
    【核心防并发机制】
    后台只有一个线程在死循环运行。
    它通过 queue.get() 阻塞等待任务。前一个任务没处理完（没调用 get 下一个），
    后续任务就会安全地堆积在队列中，绝不会同时开启多个线程或并发调用大模型。
    """
    while True:
        # 阻塞等待，直到主线程放入新任务
        user_input, model_output = task_queue.get()
        
        try:
            # 1. 调用大模型进行非流式总结
            summary = llm_client.generate_summary(user_input, model_output)
            
            # 2. 过滤无效总结并写入数据库
            if summary and summary != "无" and "无" not in summary[:2]:
                memory_manager.save_memory(summary, tag="auto_summary")
                print(f"{ANSI_GRAY}[自动记忆] 已归纳并存入: {summary}{ANSI_RESET}")
                
        except Exception as e:
            print(f"{ANSI_GRAY}[自动记忆后台异常] {e}{ANSI_RESET}")
        finally:
            # 标记当前任务已完成，允许队列处理下一个
            task_queue.task_done()

def start_worker():
    """启动后台守护线程"""
    thread = threading.Thread(target=_worker_loop, daemon=True)
    thread.start()

def enqueue_task(user_input, model_output):
    """主线程调用此方法，将任务安全地推入队列"""
    if model_output and model_output.strip():
        task_queue.put((user_input, model_output))