"""主程序入口 V8：闲聊 / Agent 双通道。

结构：
    user input
      ├─ 斜杠指令 → 本地处理
      ├─ 路由判定需要工具 → Agent 阶段（工具纪律、无人设、temp 0.2）
      │                       → 人设阶段（事实底稿 + 文昭语气改写）
      └─ 否则 → 闲聊通道（记忆注入 + think 展示，保持 V7 体验）

为什么要两段式：9B 模型在 1000 token 的强人设下，注意力被"怎么演得像"占满，
工具纪律会输给它自己的角色设定（实测：给它 tools 它会直接编造时间）。
把「执行」和「表演」拆开，两个目标都不牺牲。
"""
import sys
import time

import background_worker
import llm_client
import memory
import core_events
from agent import LAST, needs_agent, run_agent, tools
from agent import file_ops, web, remember  # noqa: F401  注册 fs/web/remember
from memory import handover

# 会话上下文窗口：保留最近多少轮对话（每轮 = 1 条 user + 1 条 assistant）
MAX_HISTORY_TURNS = 8

BANNER = """==================================================
千问娘·文昭 V9 (工具调用 + Agent + 长期记忆 v2)
指令:
  /agent <任务>  强制走 Agent（调工具）
  /chat <内容>   强制走闲聊
  /tools         查看可用工具 | /trace 上次 Agent 轨迹
  /view          查看记忆 | /mem 记忆统计 | /night 手动维护
  /pin 关键词 锁定记忆(永不遗忘) | /unpin 关键词 解锁
  /forget 关键词  遗忘 | /add 内容 | /del 关键词 | exit 退出
=================================================="""


def persona_pass(fact_answer, history):
    """第二段：把工具查到的事实用文昭语气转述，事实一个字都不许改"""
    hint = (
        "（事实底稿：以下都是关于「用户」——也就是正在和你说话的那个人——的真实信息，"
        "不是关于你自己的。这些是系统查出来的，绝对正确）\n"
        f"{fact_answer}\n\n"
        "现在用你一贯的语气把这些事实告诉用户。要求：\n"
        "1. 事实必须原样承认，不能否认、不能说\"数据错了\"、不能当成别人。\n"
        "   如果底稿写「用户叫大林」，那就是他真的叫大林，你只是用嫌弃的语气说出来。\n"
        "2. 只输出你要说的话，不要解释你在做什么，不要分析这段话，不要输出任何英文。"
    )
    # think=False 时模型会把分析写进正文，所以这里用校验+重试的一次性输出，
    # 不再流式打印（流式会把英文思考直接打到屏幕上）
    print(f"{llm_client.ANSI_GRAY}  … 组织措辞中{llm_client.ANSI_RESET}", flush=True)
    answer = llm_client.persona_reply(hint, history)
    if not answer:
        # 人设阶段两次都判废时退化为事实直述，绝不返回空
        answer = fact_answer
    print(f"千问: {answer}")
    return answer


def run_agent_turn(user_input, history):
    """Agent 阶段 + 人设阶段，返回最终展示给用户的文本"""
    t0 = time.time()
    result = run_agent(user_input, verbose=True)

    if not result["answer"] or result["reason"].startswith("模型调用失败"):
        print(f"\n[Agent 失败] {result['reason']}")
        return ""

    answer = persona_pass(result["answer"], history)

    cost = time.time() - t0
    print(
        f"{llm_client.ANSI_GRAY}"
        f"[Agent 完成] 步数={result['steps']} 工具={result['tools_used'] or '无'} "
        f"耗时={cost:.1f}s{llm_client.ANSI_RESET}"
    )
    return answer


def show_trace():
    if not LAST:
        print("还没有 Agent 轨迹。先问一个需要工具的问题，比如：现在几点了？")
        return
    print(f"任务: {LAST['task']}")
    print(f"时间: {LAST['when']}")
    for ev in LAST["events"]:
        kind = ev.get("kind")
        if kind == "llm":
            print(f"  [{ev['t']:>5}s] LLM 第{ev.get('step')}轮 → 调用 {ev.get('tool_calls')}")
        elif kind == "tool":
            print(f"  [{ev['t']:>5}s] 工具 {ev['name']}({ev.get('args')})")
            print(f"            ↳ {str(ev.get('out', ''))[:120]}")
        else:
            print(f"  [{ev['t']:>5}s] {kind} {ev}")
    r = LAST["result"]
    print(f"结果: 步数={r.get('steps')} 工具={r.get('tools_used')} "
          f"耗时={r.get('elapsed')}s 状态={r.get('reason')}")
    print(f"答案: {str(r.get('answer', ''))[:300]}")


def handle_command(text, history):
    """处理斜杠指令。返回 True 表示已消费，不再进入对话流程。"""
    cmd = text.lower()

    if cmd in ("/help", "/?"):
        print(BANNER)
        return True
    if cmd == "/tools":
        for name, t in tools.REGISTRY.items():
            print(f"  - {name}: {t.desc}")
        return True
    if cmd == "/skills":
        from agent import skills
        for name, s in skills.all_skills().items():
            print(f"  - {name}: {s['desc']}  工具={s['tools']}")
        return True
    if cmd == "/trace":
        show_trace()
        return True
    if cmd == "/mem":
        st = memory.stats()
        print(f"记忆总数: {st['total']}")
        print(f"  状态: {st['by_status']}")
        print(f"  类型: {st['by_type']}")
        return True
    if cmd == "/night":
        memory.nightly(verbose=True)
        print("[维护完成]")
        return True
    if cmd == "/eps":
        st = memory.episode_stats()
        print(f"情景章节: {st['episodes']} 章，待摘要轮次: {st['pending_turns']}")
        for e in sorted(memory.episodes._episodes(),
                        key=lambda x: x["end_ts"], reverse=True)[:10]:
            print(f"  [{e['date']}] {e['text'][:70]}")
        return True
    if text.startswith("/add "):
        memory.add_manual(text[5:].strip())
        print("[已手动记下]")
        return True
    if text.startswith("/pin "):
        kw = text[5:].strip()
        n = memory.pin(kw)
        print(f"[锁定] {n} 条包含「{kw}」的记忆已豁免遗忘" if n
              else f"未找到包含「{kw}」的记忆")
        return True
    if text.startswith("/unpin "):
        kw = text[7:].strip()
        n = memory.unpin(kw)
        print(f"[解锁] {n} 条记忆恢复常规衰减" if n
              else f"未找到包含「{kw}」的记忆")
        return True
    if text.startswith("/forget "):
        kw = text[8:].strip()
        n = memory.forget(kw)
        print(f"[遗忘] 删除了 {n} 条包含「{kw}」的记忆")
        return True
    if text.startswith("/del "):
        kw = text[5:].strip()
        hits = [f for f in memory.list_facts(500) if kw in f["text"]]
        if not hits:
            print(f"未找到包含「{kw}」的记忆")
            return True
        for f in hits:
            print(f"  {f['meta'].get('status'):<10} {f['text']}")
        if input("确认全部删除？(y/N): ").strip().lower() == "y":
            n = memory.forget(kw)
            print(f"已删除 {n} 条")
        else:
            print("已取消")
        return True
    if text.startswith("/agent "):
        task = text[7:].strip()
        if task:
            answer = run_agent_turn(task, history)
            if answer:
                history.append({"role": "user", "content": task})
                history.append({"role": "assistant", "content": answer})
                background_worker.enqueue_task(task, answer)
        return True
    return False


def chat_turn(user_input, history, on_token=None):
    """闲聊通道：档案层 + 检索层双重注入，再展示原生思维链"""
    prof = memory.profile()
    recalled, items, tone = memory.recall_with_tone(user_input)
    ho = handover.read_handover()

    parts = []
    if ho:
        parts.append(f"【会话交接·复工用】\n{ho}")
    if prof:
        parts.append(f"【档案】\n{prof}")
    if recalled:
        parts.append(f"【相关记忆】\n{recalled}")
    if tone:
        parts.append(f"【记忆清晰度】{tone}")
    eps = memory.episodes.recall_episodes(user_input)
    if eps:
        parts.append(f"【情景回忆·按时间倒排】\n{eps}")
    memory_context = "\n\n".join(parts) if parts else None

    if items:
        print(f"[命中记忆 {len(items)} 条: "
              f"{', '.join(str(round(it['score'], 2)) for it in items)}]")
    elif prof:
        print("[档案已注入，无额外相关记忆]")
    else:
        print("[无相关记忆] 直接使用 Modelfile 人设")

    _thinking, answer = llm_client.chat_stream(user_input, memory_context, history,
                                               think=True, on_token=on_token)
    return answer


def _flush_session(history):
    """退出前：等异步记忆落库 → 压尾章 → 覆盖写交接书（复工用）"""
    background_worker.drain(20.0)
    try:
        ep = memory.episodes.maybe_summarize(force=True)
        if ep:
            print(f"[情景] 会话尾章 {ep['date']} 覆盖 {ep['turns']} 轮")
    except Exception as e:
        print(f"[情景尾章失败] {e}")
    try:
        turns = [{"user": history[i]["content"],
                  "assistant": history[i + 1]["content"] if i + 1 < len(history) else ""}
                 for i in range(0, len(history) - 1, 2)]
        handover.write_handover(turns)
        print("[交接] 已覆盖写 logs/交接.md")
    except Exception as e:
        print(f"[交接书写失败] {e}")


def trim_history(history):
    if len(history) > MAX_HISTORY_TURNS * 2:
        return history[-(MAX_HISTORY_TURNS * 2):]
    return history


def main():
    core_events.install_terminal()
    background_worker.start_worker()
    history = []

    print(BANNER)

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            _flush_session(history)
            break

        if not user_input:
            continue

        # 等上一轮的异步记忆抽取落库，否则这一轮会"失忆"
        # （实测 9B 抽取一轮要 8~15 秒，给足 20 秒；队列空时立即返回）
        background_worker.drain(20.0)

        if user_input.lower() in ("exit", "quit", "退出"):
            print("再见！")
            _flush_session(history)
            break
        if user_input.lower() == "/view":
            facts = memory.list_facts(60)
            print(f"当前记忆 {memory.stats()['total']} 条，显示前 {len(facts)} 条：")
            for f in facts:
                m = f["meta"]
                print(f"  [{m.get('status', '?'):<10}|{m.get('type', '?'):<10}] "
                      f"{f['text']}  (强度 {m.get('storage_strength', 1):.1f}, "
                      f"召回 {m.get('recall_count', 0)} 次)")
            continue

        # /chat <内容>：强制闲聊，绕过路由
        if user_input.lower().startswith("/chat "):
            payload = user_input[6:].strip()
            if payload:
                print("[强制通道] 闲聊")
                remember.set_turn(remember.new_turn())
                answer = chat_turn(payload, history)
                history.append({"role": "user", "content": payload})
                if answer:
                    history.append({"role": "assistant", "content": answer})
                history = trim_history(history)
                if answer:
                    background_worker.enqueue_task(payload, answer,
                                                   turn_id=remember.CURRENT_TURN)
                print("-" * 40)
            continue

        if handle_command(user_input, history):
            history = trim_history(history)
            continue

        remember.set_turn(remember.new_turn())
        use_agent, why = needs_agent(user_input)
        if use_agent:
            print(f"[路由] {why} → Agent 通道")
            answer = run_agent_turn(user_input, history)
        else:
            answer = chat_turn(user_input, history)

        history.append({"role": "user", "content": user_input})
        if answer:
            history.append({"role": "assistant", "content": answer})
        history = trim_history(history)

        # 把「用户事实 + 最终回复」推给后台归纳（工具中间产物不进记忆）
        if answer:
            background_worker.enqueue_task(user_input, answer,
                                           turn_id=remember.CURRENT_TURN)

        print("-" * 40)


if __name__ == "__main__":
    sys.exit(main() or 0)
