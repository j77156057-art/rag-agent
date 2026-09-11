"""敌人：追击者 Chaser（向下撞玩家）与射手 Shooter（停在高处开火）。"""
import random

from .bullet import Bullet
from .utils import move_toward


class Enemy:
    """敌人基类：向下移动，子类实现横向轨迹与开火策略。"""

    kind = "chaser"

    def __init__(self, x, y, speed, size, score_value):
        self.x = x
        self.y = y
        self.speed = speed
        self.size = size
        self.score_value = score_value
        self.dead = False
        self._fire_cooldown = random.uniform(0.5, 1.5)

    def update(self, dt, player_x, screen_width, screen_height):
        """默认推进；返回本帧发射的子弹（没有则 None）。"""
        self.y += self.speed * dt
        if self.y > screen_height + self.size:
            self.dead = True
        return None


class Chaser(Enemy):
    """追击者：斜向追踪玩家的 x 坐标，撞人型敌人。"""

    kind = "chaser"

    def update(self, dt, player_x, screen_width, screen_height):
        self.y += self.speed * dt
        # 横向以自身速度的一半追踪玩家
        self.x = move_toward(self.x, player_x, self.speed * 0.5 * dt)
        if self.y > screen_height + self.size:
            self.dead = True
        return None


class Shooter(Enemy):
    """射手：下降到警戒线后悬停，周期性向玩家发射子弹。"""

    kind = "shooter"
    HOLD_Y = 120

    def update(self, dt, player_x, screen_width, screen_height):
        if self.y < self.HOLD_Y:
            self.y += self.speed * dt
        self._fire_cooldown -= dt
        if self._fire_cooldown <= 0 and self.y >= self.HOLD_Y:
            self._fire_cooldown = 1.6
            return Bullet(self.x, self.y + self.size / 2, 240, "enemy", size=8)
        return None


def spawn_wave(wave_no, cfg):
    """按波次号生成一队敌人：波次越高数量越多，混入射射手。

    返回 Enemy 列表，x 位置在屏幕内均匀散布并加少量随机抖动。
    """
    ecfg = cfg["enemy"]
    screen = cfg["screen"]
    count = ecfg["wave_size_base"] + wave_no
    size = ecfg["size"]
    enemies = []
    margin = 60
    span = screen["width"] - 2 * margin
    for i in range(count):
        x = margin + span * i / max(1, count - 1) + random.uniform(-20, 20)
        y = -size - random.randint(0, 120)
        speed = random.uniform(ecfg["speed_min"], ecfg["speed_max"])
        # 第 2 波开始，每隔一个生成一名射手
        if wave_no >= 2 and i % 3 == 2:
            enemies.append(Shooter(x, y, speed * 0.6, size, ecfg["score_shooter"]))
        else:
            enemies.append(Chaser(x, y, speed, size, ecfg["score_chaser"]))
    return enemies
