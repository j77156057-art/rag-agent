"""计分系统：击杀得分、连击加成、最高分本地存档。"""
import json
import os


class ScoreBoard:
    """本局分数与连击；最高分持久化到本地 JSON。"""

    def __init__(self, save_path="highscore.json"):
        self.save_path = save_path
        self.score = 0
        self.combo = 0
        self.combo_timer = 0.0
        self.high_score = self._load()

    def add_kill(self, enemy, combo_timeout):
        """击杀一个敌人：连击数 +1，本击得分 = 敌人分值 × 连击倍率。"""
        self.combo += 1
        self.combo_timer = combo_timeout
        self.score += enemy.score_value * self.combo

    def update(self, dt):
        """连击计时：超时未续击杀，连击清零。"""
        if self.combo_timer > 0:
            self.combo_timer -= dt
            if self.combo_timer <= 0:
                self.combo = 0

    def commit_result(self):
        """一局结束：刷新最高分并存盘。返回是否破纪录。"""
        if self.score > self.high_score:
            self.high_score = self.score
            self.save()
            return True
        return False

    def save(self):
        with open(self.save_path, "w", encoding="utf-8") as f:
            json.dump({"high_score": self.high_score}, f)

    def _load(self):
        """读取历史最高分；文件不存在或损坏时按 0 分处理。"""
        if not os.path.exists(self.save_path):
            return 0
        try:
            with open(self.save_path, encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("hiscore", 0))
        except (OSError, ValueError, TypeError):
            return 0
