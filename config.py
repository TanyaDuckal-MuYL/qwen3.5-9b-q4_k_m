import os
import sys

# ================= 模型与 API 配置 =================
MODEL_NAME = "qwen3.5-9b-wenzhao"   # 千问娘「文昭」
# 记忆向量模型：bge-m3 多语言 1024 维，中文语义显著优于 nomic-embed-text
# 换模型后必须：重建 collection + 重跑 python -m memory.calibrate
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
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

# ================= MCP 服务 =================
# {名字: [命令, 参数...]}；"python" 会被替换为当前解释器。
# 默认空：不配置就零开销。示例：{"self": ["python", "mcp_server.py"]}
MCP_SERVERS = {}

# ================= 文件沙箱 =================
# fs 工具只允许在这些目录内读写；用户要开放更多目录时在此追加。
# 代码层强制校验（realpath 归一 + 前缀比对），不靠模型自觉。
FS_ROOTS = [APP_DIR]