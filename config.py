# -*- coding: utf-8 -*-
"""
配置：环境变量加载、SMB 路径、VL 模型参数。
"""
import os
from pathlib import Path

# 加载 .env
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# ══════════════════════════════════════════════
# SMB 视频源路径（从环境变量加载，避免硬编码内部地址）
# ══════════════════════════════════════════════
SMB_VIDEO_DIR = os.environ.get("SMB_VIDEO_DIR", "")

# 支持的视频扩展名
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv"}

# ══════════════════════════════════════════════
# VL 模型配置
# ══════════════════════════════════════════════

# 模型2（mimo-v2.5，默认）
AI_VISION_API_KEY = os.environ.get("AI_VISION2_API_KEY", "")
AI_VISION_URL = os.environ.get("AI_VISION2_URL", "")
AI_VISION_MODEL = os.environ.get("AI_VISION2_MODEL", "mimo-v2.5")

# 模型1（doubao-seed）
AI_VISION1_API_KEY = os.environ.get("AI_VISION_API_KEY", "")
AI_VISION1_URL = os.environ.get("AI_VISION_URL", "")
AI_VISION1_MODEL = os.environ.get("AI_VISION_MODEL", "doubao-seed-2-0-lite-260428")

# ══════════════════════════════════════════════
# 视频理解精细度参数
# ══════════════════════════════════════════════
# fps: 每秒抽帧数 [0.1, 10]，默认2，越高越精细但token越多
MIMO_FPS = int(os.environ.get("MIMO_FPS", "5"))
# media_resolution: 帧分辨率档次 "default" | "max"，max提升小物体/细节识别
MIMO_MEDIA_RESOLUTION = os.environ.get("MIMO_MEDIA_RESOLUTION", "default")

# ══════════════════════════════════════════════
# 输出目录
# ══════════════════════════════════════════════
OUTPUT_DIR = Path(__file__).parent / "output"
COMPRESSED_DIR = Path(__file__).parent / "compressed"
