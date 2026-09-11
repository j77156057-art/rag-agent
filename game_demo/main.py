"""星舰勇者入口：tkinter 窗口 + 主循环 + 渲染。

运行：python main.py
方向键/A/D 移动，空格射击，游戏结束后按 R 重开。
"""
import tkinter as tk

from starvoyager.config import load_balance
from starvoyager.game import Game
from starvoyager.input import InputState
from starvoyager import ui


class TkRenderer:
    """把逻辑实体绘制到 tkinter Canvas 上。"""

    def __init__(self, canvas):
        self.canvas = canvas

    def draw_rect(self, x, y, w, h, color):
        self.canvas.create_rectangle(x, y, x + w, y + h, fill=color, outline="")

    def draw_text(self, x, y, text, color, font_tag):
        font = ("Microsoft YaHei", 18, "bold") if font_tag == "title" else ("Microsoft YaHei", 12)
        self.canvas.create_text(x, y, text=text, fill=color, font=font, anchor="nw")

    def draw_entity(self, entity, color):
        s = entity.size
        self.canvas.create_oval(
            entity.x - s / 2, entity.y - s / 2,
            entity.x + s / 2, entity.y + s / 2,
            fill=color, outline="",
        )


class App:
    def __init__(self):
        cfg = load_balance()
        self.cfg = cfg
        self.interval = int(1000 / cfg["screen"]["fps"])
        self.root = tk.Tk()
        self.root.title("星舰勇者 StarVoyager")
        self.canvas = tk.Canvas(
            self.root, width=cfg["screen"]["width"], height=cfg["screen"]["height"], bg="#0b0e1a"
        )
        self.canvas.pack()
        self.input = InputState()
        self.renderer = TkRenderer(self.canvas)
        self.game = Game(cfg)

        self.root.bind("<KeyPress>", self._on_press)
        self.root.bind("<KeyRelease>", self._on_release)
        self._tick()

    def _on_press(self, event):
        if event.keysym == "r" and self.game.state == "game_over":
            self.game = Game(self.cfg)
            return
        self.input.press(event.keysym)

    def _on_release(self, event):
        self.input.release(event.keysym)

    def _tick(self):
        dt = self.interval / 1000.0
        self.game.update(dt, self.input)
        self._render()
        self.root.after(self.interval, self._tick)

    def _render(self):
        self.canvas.delete("all")
        g = self.game
        if g.state == "game_over":
            ui.draw_game_over(self.renderer, g.score_board.score, g.width, g.height, g.is_record)
            return
        # 无敌闪烁：每 0.1 秒切换一次可见性
        if g.player.invincible <= 0 or int(g.player.invincible * 10) % 2 == 0:
            self.renderer.draw_entity(g.player, "#44aaff")
        for enemy in g.enemies:
            color = "#ff5566" if enemy.kind == "chaser" else "#cc66ff"
            self.renderer.draw_entity(enemy, color)
        for bullet in g.bullets:
            self.renderer.draw_entity(bullet, "#ffe066")
        for bullet in g.enemy_bullets:
            self.renderer.draw_entity(bullet, "#ff8844")
        ui.draw_hud(self.renderer, g.score_board, g.player, g.wave_no, g.width)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
