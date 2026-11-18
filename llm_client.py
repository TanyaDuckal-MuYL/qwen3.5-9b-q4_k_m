import json
import requests
from config import MODEL_NAME, OLLAMA_API, ANSI_GRAY, ANSI_RESET

def chat_stream(user_input, memory_context):
    """使用原生 API 获取完整思考 + 回答（流式）"""
    messages = []

    if memory_context:
        memory_prompt = (
            "【你的长期记忆】\n"
            "关于用户，你记起了以下信息：\n"
            f"{memory_context}\n"
            "请结合这些记忆回复用户。"
        )
        messages.append({"role": "system", "content": memory_prompt})

    messages.append({"role": "user", "content": user_input})

    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "think": True,    
        "stream": True    
    }

    thinking_buffer = ""
    answer_buffer = ""

    try:
        resp = requests.post(OLLAMA_API, json=payload, stream=True)
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
                if thinking_buffer and not answer_buffer:
                    print(f"{ANSI_RESET}\n千问: ", end="", flush=True)
                elif not answer_buffer and not thinking_buffer:
                    print("千问: ", end="", flush=True)
                answer_buffer += content_chunk
                print(content_chunk, end="", flush=True)

            if data.get('done'):
                break

        if thinking_buffer and not answer_buffer:
            print(ANSI_RESET, end="")

    except requests.exceptions.ConnectionError:
        print("\n[错误] 无法连接到 Ollama 服务，请确保已运行 `ollama serve`")
    except Exception as e:
        print(f"\n[API调用失败] {e}")

    print()  # 换行收尾
    return thinking_buffer, answer_buffer

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
        "options": {"temperature": 0.1}
    }
    
    resp = requests.post(OLLAMA_API, json=payload)
    resp.raise_for_status()
    data = resp.json()
    
    return data.get('message', {}).get('content', '').strip()