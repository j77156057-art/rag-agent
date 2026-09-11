"""HUD 与结束界面：只产出绘制指令，不依赖 tkinter，便于逻辑层单测。

Renderer 协议（由 main.py 的 TkRenderer 实现）：
  draw_text(x, y, text, color, font_tag)
  draw_rect(x, y, w, h, color)
"""


def draw_hud(renderer, score_board, player, wave_no, width):
    """绘制顶部 HUD：分数 / 最高分 / 波次 / 生命点。"""
    renderer.draw_text(12, 8, f"分数 {score_board.score}", "#ffffff", "hud")
    renderer.draw_text(12, 30, f"最高 {score_board.high_score}", "#aaaaff", "hud")
    renderer.draw_text(width // 2 - 30, 8, f"第 {wave_no} 波", "#ffdd66", "hud")
    if score_board.combo > 1:
        renderer.draw_text(width - 120, 8, f"连击 x{score_board.combo}", "#ff8844", "hud")
    # 生命点：每点 HP 一个小方块
    for i in range(player.max_hp):
        color = "#ff4444" if i < player.hp else "#553333"
        renderer.draw_rect(width - 30 - i * 22, 32, 16, 16, color)


def draw_game_over(renderer, score, width, height, is_record):
    """绘制结束界面。"""
    renderer.draw_text(width // 2 - 60, height // 2 - 40, "游戏结束", "#ff5555", "title")
    renderer.draw_text(width // 2 - 60, height // 2, f"最终得分 {score}", "#ffffff", "hud")
    tip = "新纪录！" if is_record else "按 R 重新开始"
    renderer.draw_text(width // 2 - 60, height // 2 + 40, tip, "#ffdd66", "hud")
