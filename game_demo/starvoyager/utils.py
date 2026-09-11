"""通用数学工具。"""


def clamp(value, low, high):
    """把 value 限制在 [low, high] 区间内。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def move_toward(value, target, max_delta):
    """以 max_delta 为步长把 value 朝 target 逼近（用于敌人横向追踪玩家）。"""
    if abs(target - value) <= max_delta:
        return target
    return value + max_delta if target > value else value - max_delta
