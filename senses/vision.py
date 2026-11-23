"""视觉：qwen2.5vl:7b 读图/视频抽帧。

8G 显存装不下主模型+视觉共驻 → 走 llm_client.vision_slot 互斥调度：
卸主模型 → 视觉推理 → 卸视觉 → 主模型下次调用自动重载。
视频：opencv 每秒 1 帧、上限 6 帧，逐帧描述拼成时间线。
"""
import os

VISION_MODEL = "qwen2.5vl:7b"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VID_EXT = {".mp4", ".avi", ".mkv", ".mov", ".webm"}


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMG_EXT:
        return "image"
    if ext in VID_EXT:
        return "video"
    return "other"


def _frames(path: str, max_frames: int = 6) -> list[str]:
    import cv2
    import tempfile
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, int(fps))           # 约每秒 1 帧
    idxs = list(range(0, total, step))[:max_frames]
    out = []
    tmpdir = tempfile.mkdtemp(prefix="frames_")
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = cap.read()
        if not ok:
            continue
        p = os.path.join(tmpdir, f"f{i}.jpg")
        cv2.imwrite(p, frame)
        out.append((i / fps, p))
    cap.release()
    return out


def describe(path: str, question: str = "") -> str:
    """图片/视频 → 中文描述。失败返回 ERROR 文本，调用方如实告知。"""
    import llm_client
    k = kind_of(path)
    if k == "other":
        return f"ERROR: 不支持的媒体类型：{path}"
    q = question or "用中文简洁描述这张图的内容、文字与细节。"
    try:
        if k == "image":
            with llm_client.vision_slot(VISION_MODEL):
                return llm_client.vision_chat(VISION_MODEL, q, [path])
        frames = _frames(path)
        if not frames:
            return "ERROR: 视频无法抽帧"
        parts = []
        with llm_client.vision_slot(VISION_MODEL):
            for t, p in frames:
                d = llm_client.vision_chat(
                    VISION_MODEL, "用中文一句话描述这一帧画面。", [p])
                parts.append(f"[{t:.0f}s] {d}")
        return "视频时间线：\n" + "\n".join(parts)
    except Exception as e:
        return f"ERROR: 视觉推理失败 {type(e).__name__}: {e}"