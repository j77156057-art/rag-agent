"""数值配置中心：从 config/balance.json 读取全部平衡性数值。

设计为数据驱动：调数值不用改代码，只改 JSON。文件缺失时回退到内置默认值。
"""
import json
import os

# 游戏仓库根目录（本文件位于 <root>/starvoyager/config.py）
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BALANCE_PATH = os.path.join(ROOT_DIR, "config", "balance.json")

_DEFAULTS = {
    "screen": {"width": 800, "height": 600, "fps": 60},
    "player": {
        "speed": 320,
        "size": 28,
        "hp": 3,
        "fire_interval": 0.25,
        "invincible_time": 1.5,
        "bullet_speed": 520,
    },
    "enemy": {
        "spawn_interval": 1.2,
        "speed_min": 60,
        "speed_max": 160,
        "size": 30,
        "score_chaser": 100,
        "score_shooter": 250,
        "wave_size_base": 4,
    },
    "combo": {"timeout": 2.0},
}


def load_balance(path=BALANCE_PATH):
    """加载 balance.json；解析失败或文件缺失时回退默认值。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return dict(_DEFAULTS)
    # 浅合并：JSON 里缺的段落用默认值补齐
    merged = dict(_DEFAULTS)
    for key, val in data.items():
        if isinstance(val, dict):
            merged[key] = {**_DEFAULTS.get(key, {}), **val}
        else:
            merged[key] = val
    return merged
