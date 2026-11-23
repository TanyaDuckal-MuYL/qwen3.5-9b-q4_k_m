import background_worker
import memory_manager
import llm_client

# 会话上下文窗口：保留最近多少轮对话（每轮 = 1 条 user + 1 条 assistant）
MAX_HISTORY_TURNS = 8

if __name__ == "__main__":
    # 启动后台单线程队列 Worker
    background_worker.start_worker()

    # 【新增】本次会话的多轮上下文列表，跨 while 循环持续累积
    history = []

    print("=" * 50)
    print("傲娇千问 V7.1 (工程化 + 队列防并发 + 长期记忆 + 多轮上下文) 已启动")
    print("指令: /view 查看记忆 | /add 内容 添加记忆 | /del 关键词 删除记忆 | exit 退出")
    print("=" * 50)

    while True:
        user_input = input("\n你: ").strip()

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd in ['exit', 'quit', '退出']:
            print("再见！")
            break
        if cmd == '/view':
            memory_manager.view_all_memories()
            continue
        if user_input.startswith('/add '):
            memory_manager.save_memory(user_input[5:].strip())
            continue
        if user_input.startswith('/del '):
            memory_manager.delete_memory_by_keyword(user_input[5:].strip())
            continue

        memory_context = memory_manager.retrieve_memory(user_input)
        if memory_context:
            print("[命中记忆] 已注入相关记忆作为补充 system")
        else:
            print("[无相关记忆] 直接使用 Modelfile 人设")

        # 【改动】把会话历史 history 一并传入，模型才能“记得”刚才说的话
        thinking_buffer, answer_buffer = llm_client.chat_stream(
            user_input, memory_context, history
        )

        # 【新增】本轮对话结束后，追加进上下文窗口
        history.append({"role": "user", "content": user_input})
        if answer_buffer:
            history.append({"role": "assistant", "content": answer_buffer})

        # 截断：只保留最近 MAX_HISTORY_TURNS 轮，防止上下文无限膨胀、显存爆表
        if len(history) > MAX_HISTORY_TURNS * 2:
            history = history[-(MAX_HISTORY_TURNS * 2):]

        # 将总结任务推入后台队列，主线程立即返回等待下一次 input()
        if answer_buffer:
            background_worker.enqueue_task(user_input, answer_buffer)

        print("-" * 40)
