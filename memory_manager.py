import uuid
import datetime
import math
import chromadb
import ollama
from config import EMBED_MODEL, DB_PATH

# 初始化向量数据库
client = chromadb.PersistentClient(path=DB_PATH)
collection = client.get_or_create_collection(
    name="qwen_memory",
    metadata={"hnsw:space": "cosine"}
)

# ================= 可调阈值（集中放这里方便微调） =================
DEDUP_THRESHOLD = 0.12       # 去重：语义距离小于此值判为同一条，刷新时间戳
RECALL_DISTANCE_THRESHOLD = 0.30   # 【调严】命中阈值：原 0.4 太松，问“你的名字”也会捞出“喜欢秀秀”
RECALL_DECAY_FACTOR = 0.05   # 时间衰减系数，约 14 天权重减半


def get_embedding(text):
    """调用 Ollama 获取文本向量"""
    response = ollama.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]

def save_memory(fact, tag="general"):
    """写入长期记忆：带去重机制
    存之前先做一次相似度检索，命中高度相似则刷新时间戳（复习），否则新增。
    """
    memory_vector = get_embedding(fact)
    current_time = datetime.datetime.now().isoformat()

    existing = collection.query(
        query_embeddings=[memory_vector],
        n_results=1,
        include=["distances"]  # 注意：query 默认就会返回 ids，不要把 "ids" 放进 include
    )

    if existing['ids'] and existing['distances'] and existing['distances'][0] \
            and existing['distances'][0][0] < DEDUP_THRESHOLD:
        existing_id = existing['ids'][0][0]
        collection.update(
            ids=[existing_id],
            metadatas=[{"timestamp": current_time, "tag": tag}]
        )
        print(f"\n[记忆已更新] 发现相似记忆，已刷新时间戳: {fact}")
        return

    memory_id = f"mem_{uuid.uuid4().hex[:8]}"
    collection.add(
        ids=[memory_id],
        embeddings=[memory_vector],
        documents=[fact],
        metadatas=[{"tag": tag, "timestamp": current_time}]
    )
    print(f"\n[记忆已写入] {fact}")

def retrieve_memory(query, limit=3,
                    distance_threshold=RECALL_DISTANCE_THRESHOLD,
                    decay_factor=RECALL_DECAY_FACTOR):
    """语义检索：带时间衰减
    综合得分 = 语义得分(1-距离) * exp(-decay_factor * 距今天数)
    """
    if collection.count() == 0:
        return None

    try:
        query_vector = get_embedding(query)
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=min(limit * 2, collection.count()),
            include=["documents", "distances", "metadatas"]
        )

        documents = results['documents'][0] if results['documents'] else []
        distances = results['distances'][0] if results['distances'] else []
        metadatas = results['metadatas'][0] if results['metadatas'] else []

        scored_memories = []
        now = datetime.datetime.now()

        for doc, dist, meta in zip(documents, distances, metadatas):
            # 先用“命中阈值”做硬过滤：低于相似度门槛的直接丢弃
            if dist >= distance_threshold:
                continue

            semantic_score = 1 - dist

            timestamp_str = (meta or {}).get('timestamp')
            try:
                mem_time = datetime.datetime.fromisoformat(timestamp_str) if timestamp_str else now
            except Exception:
                mem_time = now
            days_ago = max((now - mem_time).total_seconds() / 86400.0, 0)

            time_weight = math.exp(-decay_factor * days_ago)
            final_score = semantic_score * time_weight
            scored_memories.append((final_score, doc))

        scored_memories.sort(key=lambda x: x[0], reverse=True)
        top_memories = [doc for _, doc in scored_memories[:limit]]

        return "\n".join([f"- {m}" for m in top_memories]) if top_memories else None

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
