"""游戏主逻辑：状态机、波次生成、碰撞结算与逐帧更新（纯逻辑，无 tkinter）。"""
from .config import load_balance
from .enemy import spawn_wave
from .player import Player
from .score import ScoreBoard
from . import collision


class Game:
    """整局游戏的状态持有者。状态：playing / game_over。"""

    def __init__(self, cfg=None, save_path="highscore.json"):
        self.cfg = cfg or load_balance()
        screen = self.cfg["screen"]
        self.width = screen["width"]
        self.height = screen["height"]
        self.player = Player(self.cfg)
        self.score_board = ScoreBoard(save_path)
        self.bullets = []
        self.enemy_bullets = []
        self.enemies = []
        self.wave_no = 0
        self.state = "playing"
        self.is_record = False
        self._spawn_timer = self.cfg["enemy"]["spawn_interval"]

    def update(self, dt, inp):
        """推进一帧：输入 -> 实体移动 -> 生成 -> 碰撞结算 -> 清理。"""
        if self.state != "playing":
            return

        # 1. 玩家移动与射击
        self.player.update(dt, inp.direction(), self.width, self.height)
        if inp.shooting():
            bullet = self.player.try_shoot(dt)
            if bullet:
                self.bullets.append(bullet)

        # 2. 波次生成（间隔到了就刷下一波）
        self._spawn_timer -= dt
        if self._spawn_timer <= 0:
            self.wave_no += 1
            self.enemies.extend(spawn_wave(self.wave_no, self.cfg))
            self._spawn_timer = self.cfg["enemy"]["spawn_interval"]

        # 3. 敌人与子弹移动
        new_enemy_shots = []
        for enemy in self.enemies:
            shot = enemy.update(dt, self.player.x, self.width, self.height)
            if shot:
                new_enemy_shots.append(shot)
        self.enemy_bullets.extend(new_enemy_shots)
        for bullet in self.bullets:
            bullet.update(dt)
        for bullet in self.enemy_bullets:
            bullet.update(dt)

        # 4. 碰撞结算：玩家子弹打敌人
        for bullet, enemy in collision.pair_bullets_enemies(self.bullets, self.enemies):
            bullet.dead = True
            enemy.dead = True
            self.score_board.add_kill(enemy, self.cfg["combo"]["timeout"])

        # 5. 碰撞结算：敌人子弹 / 敌人本体打到玩家
        for bullet in collision.pair_enemy_bullets_player(self.enemy_bullets, self.player):
            bullet.dead = True
            self._hurt_player()
        for enemy in collision.enemies_reach_player(self.enemies, self.player):
            enemy.dead = True
            self._hurt_player()

        self.score_board.update(dt)

        # 6. 清理死亡/出界实体
        self._cleanup()

        if self.player.dead:
            self._game_over()

    def _hurt_player(self):
        """玩家受到一次伤害时的统一入口。"""
        self.player.take_damage(1)

    def _cleanup(self):
        """移除死亡与飞出屏幕的子弹、敌人。"""
        for bullet in self.bullets:
            if bullet.dead or bullet.is_off_screen(self.height):
                self.bullets.remove(bullet)
        for bullet in self.enemy_bullets:
            if bullet.dead or bullet.is_off_screen(self.height):
                self.enemy_bullets.remove(bullet)
        self.enemies = [e for e in self.enemies if not e.dead]

    def _game_over(self):
        self.state = "game_over"
        self.is_record = self.score_board.commit_result()
