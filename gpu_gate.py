"""G1 负载闸（方案附录 D.4，学自淋雨家「全局判断」）。

「我这一个任务很快」是局部判断；「机器现在能不能再接活」是全局判断。
读 Ollama /api/ps 的显存占用，对照总显存算余量：
- 余量 < MIN_HEADROOM_GB：后台归纳/摘要延后（记 why），不抢前台；
- 拿不到负载信息：按保守档跑并记 why —— 降级不是失败。
P4 视觉模型切换前调同一把闸。
"""
import requests

from config import OLLAMA_API

OLLAMA_PS = OLLAMA_API.rsplit("/api/", 1)[0] + "/api/ps"
TOTAL_VRAM_GB = 8.0        # 3060 Ti 8G；换卡改这里
MIN_HEADROOM_GB = 1.0      # 后台任务要求的最小余量


def vram_usage_gb() -> float | None:
    """当前 Ollama 模型驻留显存（GB）；拿不到返回 None。"""
    try:
        d = requests.get(OLLAMA_PS, timeout=5).json()
        total = 0
        for m in d.get("models", []):
            total += int(m.get("size_vram") or 0)
        return total / (1024 ** 3)
    except Exception:
        return None


def headroom_gb() -> float | None:
    used = vram_usage_gb()
    if used is None:
        return None
    return max(0.0, TOTAL_VRAM_GB - used)


def gate(task: str) -> tuple[bool, str]:
    """返回 (允许派活, why)。前台交互不受此闸限制，只约束后台。"""
    hr = headroom_gb()
    if hr is None:
        return True, "负载信息不可得，按保守档跑（单线程后台，不并发）"
    if hr < MIN_HEADROOM_GB:
        return False, (f"显存余量 {hr:.2f}GB < {MIN_HEADROOM_GB}GB，"
                       f"后台 {task} 延后，不抢前台")
    return True, f"余量 {hr:.2f}GB 充足"