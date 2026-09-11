"""碰撞与计分的无依赖自检：python tests/test_collision.py

不依赖 pytest，直接运行即可；有断言失败会以非零退出码退出。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starvoyager.collision import circle_hit, aabb_hit, pair_bullets_enemies
from starvoyager.bullet import Bullet
from starvoyager.enemy import Chaser


class _Box:
    def __init__(self, x, y, size):
        self.x = x
        self.y = y
        self.size = size


def test_circle_hit():
    # 圆心距离 10，两半径之和 20：应当判定相撞
    assert circle_hit(_Box(0, 0, 20), _Box(10, 0, 20)), "距离小于半径和时必须判定碰撞"
    # 圆心距离 25，两半径之和 20：不应碰撞
    assert not circle_hit(_Box(0, 0, 20), _Box(25, 0, 20)), "距离大于半径和时不应碰撞"
    # 临界：距离恰好等于半径和（20），严格小于才碰撞，边界上不算
    assert not circle_hit(_Box(0, 0, 20), _Box(20, 0, 20)), "恰好相切不算碰撞"


def test_aabb_hit():
    assert aabb_hit(_Box(0, 0, 20), _Box(15, 0, 20))
    assert not aabb_hit(_Box(0, 0, 20), _Box(30, 0, 20))


def test_pair_bullet_enemy_direct_hit():
    # 子弹正中敌人中心：必须能配对出来
    enemy = Chaser(100, 100, 60, 30, 100)
    bullet = Bullet(100, 100, 520, "player")
    hits = pair_bullets_enemies([bullet], [enemy])
    assert len(hits) == 1, f"正中敌人应产生 1 个命中对，实际 {len(hits)}"
    assert hits[0][1] is enemy


def test_pair_bullet_enemy_miss():
    enemy = Chaser(100, 100, 60, 30, 100)
    bullet = Bullet(100, -200, 520, "player")
    assert pair_bullets_enemies([bullet], [enemy]) == []


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    if failures:
        print(f"\n{failures} 个用例失败")
        sys.exit(1)
    print("\n全部通过")
