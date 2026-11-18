import os
import sys

# ================= 模型与 API 配置 =================
MODEL_NAME = "qwen3.5-9b-tsundere"   # 你的傲娇千问
EMBED_MODEL = "nomic-embed-text"     # 向量模型
OLLAMA_API = "http://localhost:11434/api/chat"

# ================= 终端颜色配置 =================
ANSI_GRAY = "\033[90m"
ANSI_RESET = "\033[0m"

# 激活 Windows 终端的 ANSI 转义序列支持
if os.name == 'nt':
    os.system("")

# ================= 路径配置 =================
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