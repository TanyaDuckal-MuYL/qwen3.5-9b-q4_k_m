import uuid
import datetime
import chromadb
import ollama
from config import EMBED_MODEL, DB_PATH

# 初始化向量数据库
client = chromadb.PersistentClient(path=DB_PATH)
collection = client.get_or_create_collection(
    name="qwen_memory",
    metadata={"hnsw:space": "cosine"}
)

def get_embedding(text):
    """调用 Ollama 获取文本向量"""
    response = ollama.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]

def save_memory(fact, tag="general"):
    """写入长期记忆：将核心事实向量化并存入数据库"""
    memory_vector = get_embedding(fact)
    memory_id = f"mem_{uuid.uuid4().hex[:8]}"
    current_time = datetime.datetime.now().isoformat()
    
    collection.add(
        ids=[memory_id],
        embeddings=[memory_vector],
        documents=[fact],
        metadatas=[{"tag": tag, "timestamp": current_time}]
    )
    print(f"\n[记忆已写入] {fact}")

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
    """按关键词删除记忆"""
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