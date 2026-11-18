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


def get_embedding(text):
    """调用 Ollama 获取文本向量"""
    response = ollama.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]


def save_memory(fact, tag="general", dedup_threshold=0.1):
    """写入长期记忆：将核心事实向量化并存入数据库

    【新增·去重】写入前先比对该向量的最近邻:
      - 若存在余弦距离 < dedup_threshold 的近似记忆, 视为同一件事,
        不新增, 仅把那条旧记忆的 timestamp 刷新为当前时间(等价于"复习").
      - 否则正常新增.
    """
    memory_vector = get_embedding(fact)
    current_time = datetime.datetime.now().isoformat()

    # 1. 去重检查: 取距离最近的一条
    existing = collection.query(
        query_embeddings=[memory_vector],
        n_results=1,
        include=["distances"]
    )

    existing_ids = existing.get("ids") or []
    existing_dist = existing.get("distances") or []

    if (existing_ids and existing_ids[0]
            and existing_dist and existing_dist[0]
            and existing_dist[0][0] < dedup_threshold):
        # 命中近似记忆: 刷新时间戳即可, 不新增冗余
        dup_id = existing_ids[0][0]
        collection.update(
            ids=[dup_id],
            metadatas=[{"tag": tag, "timestamp": current_time}]
        )
        print(f"\n[记忆已更新] 检测到近似记忆, 已刷新时间戳: {fact}")
        return

    # 2. 无重复: 正常新增
    memory_id = f"mem_{uuid.uuid4().hex[:8]}"
    collection.add(
        ids=[memory_id],
        embeddings=[memory_vector],
        documents=[fact],
        metadatas=[{"tag": tag, "timestamp": current_time}]
    )
    print(f"\n[记忆已写入] {fact}")


def retrieve_memory(query, limit=3, distance_threshold=0.4, decay_factor=0.05):
    """语义检索: 根据用户提问去向量库捞取最相关的记忆

    【新增·时间衰减】在语义相似度基础上叠加指数时间权重再排序:
        final_score = (1 - distance) * exp(-decay_factor * days_ago)
    语义相当时, 近期记忆排在前面; 久未命中的旧记忆自然沉底(不会被删除).
    """
    if collection.count() == 0:
        return None

    try:
        query_vector = get_embedding(query)
        # 多召回一些候选, 便于按综合得分重排后再截取 limit 条
        results = collection.query(
            query_embeddings=[query_vector],
            n_results=min(limit * 2, collection.count()),
            include=["documents", "distances", "metadatas"]
        )

        documents = results['documents'][0] if results['documents'] else []
        distances = results['distances'][0] if results['distances'] else []
        metadatas = results['metadatas'][0] if results['metadatas'] else []

        now = datetime.datetime.now()
        scored_memories = []

        for doc, dist, meta in zip(documents, distances, metadatas):
            if dist >= distance_threshold:
                continue

            # 语义得分: 距离越小越好, 映射到 0~1
            semantic_score = 1 - dist

            # 时间得分: 指数衰减, decay_factor=0.05 约 14 天权重减半
            timestamp_str = (meta or {}).get('timestamp')
            if timestamp_str:
                try:
                    mem_time = datetime.datetime.fromisoformat(timestamp_str)
                    days_ago = max((now - mem_time).days, 0)
                except ValueError:
                    days_ago = 0
            else:
                days_ago = 0
            time_weight = math.exp(-decay_factor * days_ago)

            scored_memories.append((semantic_score * time_weight, doc))

        # 按综合得分降序, 取前 limit 条
        scored_memories.sort(key=lambda x: x[0], reverse=True)
        top_memories = [doc for _score, doc in scored_memories[:limit]]

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
