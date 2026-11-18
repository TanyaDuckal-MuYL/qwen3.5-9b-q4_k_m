import os
import sys
import json
import requests
import chromadb
import ollama
import uuid
import datetime  # 【新增】用于修复记忆写入的时间戳 Bug

# 激活 Windows 终端的 ANSI 转义序列支持
if os.name == 'nt':
    os.system("")

# ================= 配置区 =================
MODEL_NAME = "qwen3.5-9b-tsundere"   # 你的傲娇千问
EMBED_MODEL = "nomic-embed-text"     # 向量模型
OLLAMA_API = "http://localhost:11434/api/chat"

ANSI_GRAY = "\033[90m"
ANSI_RESET = "\033[0m"

# ================= 路径改造核心 =================
def get_app_dir():
    """获取 exe 或脚本所在的真实物理目录"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()
MEMORY_DIR = os.path.join(APP_DIR, "memory")
os.makedirs(MEMORY_DIR, exist_ok=True)
DB_PATH = os.path.join(MEMORY_DIR, "chroma_db")

# 初始化向量数据库
client = chromadb.PersistentClient(path=DB_PATH)
collection = client.get_or_create_collection(
    name="qwen_memory",
    metadata={"hnsw:space": "cosine"}
)

# ================= 记忆管理模块 =================
def get_embedding(text):
    """调用 Ollama 获取文本向量"""
    response = ollama.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]

def retrieve_memory(query, limit=3, distance_threshold=0.4):
    """语义检索：根据用户提问去向量库捞取最相关的记忆"""
    if collection.count() == 0:
        return None

    try:
        query_vector = get_embedding(query)
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=min(limit, collection.count()),
            include=["documents", "distances"]
        )

        documents = results['documents'][0] if results['documents'] else []
        distances = results['distances'][0] if results['distances'] else []

        relevant_memories = [
            doc for doc, dist in zip(documents, distances)
            if dist < distance_threshold
        ]

        return "\n".join([f"- {m}" for m in relevant_memories]) if relevant_memories else None

    except Exception as e:
        print(f"[记忆检索异常] {e}")
        return None

def save_memory(fact, tag="general"):
    """写入长期记忆：将核心事实向量化并存入数据库"""
    memory_vector = get_embedding(fact)
    memory_id = f"mem_{uuid.uuid4().hex[:8]}"
    
    # 【修复】使用标准库获取 ISO 格式时间，替代原来不存在的函数
    current_time = datetime.datetime.now().isoformat()
    
    collection.add(
        ids=[memory_id],
        embeddings=[memory_vector],
        documents=[fact],
        metadatas=[{"tag": tag, "timestamp": current_time}]
    )
    print(f"[记忆已写入] {fact}")

def view_all_memories(limit=50):
    """查看当前库里存了什么"""
    count = collection.count()
    print(f"当前记忆总数: {count}")
    if count == 0:
        return

    all_data = collection.get(limit=limit)
    docs = all_data.get('documents') or []
    ids = all_data.get('ids') or []
    metas = all_data.get('metadatas') or []

    for i, (doc, id_, meta) in enumerate(zip(docs, ids, metas), start=1):
        tag = meta.get('tag', '-') if isinstance(meta, dict) else '-'
        print(f"  [{i}] ID={id_[:8]} | tag={tag} | 内容={doc}")

def delete_memory_by_keyword(keyword):
    """按关键词删除记忆（先列出候选，再确认删除）"""
    all_data = collection.get()
    docs = all_data.get('documents') or []
    ids = all_data.get('ids') or []

    matched = [(id_, doc) for id_, doc in zip(ids, docs) if keyword in doc]
    if not matched:
        print(f"未找到包含「{keyword}」的记忆")
        return

    print(f"匹配到 {len(matched)} 条记忆:")
    for id_, doc in matched:
        print(f"  ID={id_[:8]} | {doc}")

    confirm = input("确认全部删除？(y/N): ").strip().lower()
    if confirm == 'y':
        collection.delete(ids=[id_ for id_, _ in matched])
        print(f"已删除 {len(matched)} 条记忆")
    else:
        print("已取消删除")

# ================= 自动总结模块 =================
def summarize_and_save_memory(user_input, model_output):
    """【新增】后台调用模型总结对话，并自动存入记忆库"""
    if not model_output.strip():
        return

    summary_prompt = f"""你是一个后台记忆提取程序。请分析以下用户与AI的对话，提取出关于用户的客观事实、偏好、个人信息或重要决定。
要求：
1. 用一句简练的陈述句总结（例如：“用户喜欢吃苹果”、“用户的名字是张三”）。
2. 如果对话只是日常寒暄、没有实质性信息，或者无法提取有效事实，请仅回复“无”。
3. 不要包含任何解释，不要使用傲娇或任何角色扮演语气，直接输出总结内容或“无”。

用户输入：{user_input}
AI回复：{model_output}

总结："""

    try:
        # 使用 requests 发起非流式调用，避免在终端产生多余的输出
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "system", "content": "你是一个无情的后台信息提取工具，只输出客观事实总结，绝不带入任何角色设定。"},
                {"role": "user", "content": summary_prompt}
            ],
            "stream": False,
            "options": {"temperature": 0.1}  # 降低温度，保证总结的客观和稳定
        }
        
        resp = requests.post(OLLAMA_API, json=payload)
        resp.raise_for_status()
        data = resp.json()
        
        summary = data.get('message', {}).get('content', '').strip()
        
        # 过滤掉无效总结（如模型回复了“无”或包含“无”的废话）
        if summary and summary != "无" and "无" not in summary[:2]:
            save_memory(summary, tag="auto_summary")
            print(f"{ANSI_GRAY}[自动记忆] 已归纳并存入: {summary}{ANSI_RESET}")
            
    except Exception as e:
        print(f"{ANSI_GRAY}[自动记忆失败] {e}{ANSI_RESET}")

# ================= Ollama 对话模块 =================
def chat_with_qwen_native(user_input, memory_context):
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

# ================= 主循环 =================
if __name__ == "__main__":
    print("=" * 50)
    print("傲娇千问 V6.0 (自动归纳记忆 + 长期记忆 + 思维链) 已启动")
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
            view_all_memories()
            continue
        if user_input.startswith('/add '):
            save_memory(user_input[5:].strip())
            continue
        if user_input.startswith('/del '):
            delete_memory_by_keyword(user_input[5:].strip())
            continue

        memory_context = retrieve_memory(user_input)
        if memory_context:
            print("[命中记忆] 已注入相关记忆作为补充 system")
        else:
            print("[无相关记忆] 直接使用 Modelfile 人设")

        # 获取对话结果
        thinking_buffer, answer_buffer = chat_with_qwen_native(user_input, memory_context)
        
        # 【新增】对话结束后，自动触发后台总结并写入数据库
        if answer_buffer:
            summarize_and_save_memory(user_input, answer_buffer)
            
        print("-" * 40)