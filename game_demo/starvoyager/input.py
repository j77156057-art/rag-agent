"""键盘输入状态：记录当前按下的键，向逻辑层提供方向与射击意图。"""


class InputState:
    """由渲染层（main.py）在按键事件中调用 press/release，逻辑层只读查询。"""

    LEFT_KEYS = {"Left", "a", "A"}
    RIGHT_KEYS = {"Right", "d", "D"}
    SHOOT_KEYS = {"space"}

    def __init__(self):
        self._down = set()

    def press(self, keysym):
        self._down.add(keysym)

    def release(self, keysym):
        self._down.discard(keysym)

    def clear(self):
        self._down.clear()

    def direction(self):
        """返回横向移动方向：-1 左 / 0 停 / 1 右。左右同按视为停。"""
        left = bool(self._down & self.LEFT_KEYS)
        right = bool(self._down & self.RIGHT_KEYS)
        return int(right) - int(left)

    def shooting(self):
        """空格是否按住（按住连射，冷却由 Player 自己管）。"""
        return bool(self._down & self.SHOOT_KEYS)
