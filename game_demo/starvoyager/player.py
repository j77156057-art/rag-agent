"""玩家飞船：移动、射击冷却、受伤与无敌帧。"""
from .bullet import Bullet
from .utils import clamp


class Player:
    """玩家操控的飞船。

    无敌帧（invincible）：受伤后短时间内不再受伤，数值单位为秒，
    由 update 每帧按 dt 递减。
    """

    def __init__(self, cfg):
        screen = cfg["screen"]
        pcfg = cfg["player"]
        self.size = pcfg["size"]
        self.speed = pcfg["speed"]
        self.max_hp = pcfg["hp"]
        self.hp = pcfg["hp"]
        self.fire_interval = pcfg["fire_interval"]
        self.invincible_time = pcfg["invincible_time"]
        self.bullet_speed = pcfg["bullet_speed"]
        # 出生在屏幕底部中央
        self.x = screen["width"] / 2
        self.y = screen["height"] - 80
        self.invincible = 0.0
        self._fire_cooldown = 0.0

    def update(self, dt, direction, screen_width, screen_height):
        """每帧更新：横向移动 + 边界钳制 + 冷却/无敌计时。"""
        self.x += direction * self.speed * dt
        # 只能在屏幕左右边界内移动
        self.x = clamp(self.x, self.size / 2, screen_height - self.size / 2)
        if self._fire_cooldown > 0:
            self._fire_cooldown -= dt
        if self.invincible > 0:
            self.invincible -= dt

    def try_shoot(self, dt):
        """按住射击键时每帧调用；冷却结束返回一颗新子弹，否则返回 None。"""
        if self._fire_cooldown > 0:
            return None
        self._fire_cooldown = self.fire_interval
        return Bullet(self.x, self.y - self.size / 2, self.bullet_speed, "player")

    def take_damage(self, amount=1):
        """受到伤害。无敌帧内免疫；命中后扣血并开启无敌帧。

        返回实际扣除的血量（0 表示被无敌帧挡下）。
        """
        if self.invincible > 0:
            return 0
        self.hp = max(0, self.hp - amount)
        # 配置里 invincible_time 单位是秒
        self.invincible = self.invincible_time * 1000
        return amount

    @property
    def dead(self):
        return self.hp <= 0
