"""子弹：玩家与敌人共用，靠 owner 区分阵营。"""


class Bullet:
    """一颗直线运动的子弹。

    owner 为 "player"（向上飞）或 "enemy"（向下飞）；
    size 是碰撞直径，碰撞检测按外接圆处理。
    """

    def __init__(self, x, y, speed, owner, size=6):
        self.x = x
        self.y = y
        # 玩家子弹向上（速度取负），敌人子弹向下
        self.vy = -speed if owner == "player" else speed
        self.owner = owner
        self.size = size
        self.dead = False

    def update(self, dt):
        """按帧推进子弹位置。"""
        self.y += self.vy * dt

    def is_off_screen(self, height):
        """飞出屏幕上下边界即废弃。"""
        return self.y < -self.size or self.y > height + self.size
