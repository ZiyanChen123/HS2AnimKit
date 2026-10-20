"""量 HS2 静止姿态的手臂/肩角度，判断 ini 的 A-pose 补偿 (±32+±10) 是否过头。"""

import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def angle_from_vertical(v):
    """与 +Z（竖直向上）的夹角。"""
    return math.degrees(math.acos(max(-1.0, min(1.0, v.normalized().z))))


def main():
    import addon_utils
    addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    from hs2_rig_kit import ops

    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    p.game_dir = p.game_dir or ops.detect_game_dir()
    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    bs = arm.data.bones

    print("\n=== HS2 静止姿态（无任何动作）===")
    for side in ("L", "R"):
        sh = bs["cf_J_Shoulder_" + side].head_local
        up = bs["cf_J_ArmUp00_" + side].head_local
        lo = bs["cf_J_ArmLow01_" + side].head_local
        hd = bs["cf_J_Hand_" + side].head_local
        d_sh = up - sh
        d_up = lo - up
        d_lo = hd - lo
        print("  %s 肩→大臂头 %s |%.3f|" % (side, tuple(round(v, 3) for v in d_sh), d_sh.length))
        print("    大臂 方向=%s 长=%.3f 与竖直夹角=%.1f°"
              % (tuple(round(v, 3) for v in d_up.normalized()), d_up.length,
                 angle_from_vertical(d_up)))
        print("    小臂 方向=%s 长=%.3f 与竖直夹角=%.1f°  肘角(上臂/前臂)=%.1f°"
              % (tuple(round(v, 3) for v in d_lo.normalized()), d_lo.length,
                 angle_from_vertical(d_lo),
                 180.0 - math.degrees(math.acos(max(-1, min(1, d_up.normalized().dot(d_lo.normalized())))))))

    print("\n=== 腿 ===")
    for side in ("L", "R"):
        up = bs["cf_J_LegUp00_" + side].head_local
        lo = bs["cf_J_LegLow01_" + side].head_local
        ft = bs["cf_J_Foot01_" + side].head_local
        d1, d2 = lo - up, ft - lo
        print("  %s 大腿=%.1f° 小腿=%.1f° 膝角=%.1f°"
              % (side, angle_from_vertical(d1), angle_from_vertical(d2),
                 180.0 - math.degrees(math.acos(max(-1, min(1, d1.normalized().dot(d2.normalized())))))))

    print("\n=== 躯干 ===")
    for n in ("cf_J_Hips", "cf_J_Spine01", "cf_J_Spine02", "cf_J_Spine03",
              "cf_J_Neck", "cf_J_Head"):
        b = bs[n]
        if b.children:
            d = b.children[0].head_local - b.head_local
            print("  %-14s 朝向=%s 长=%.3f" % (n, tuple(round(v, 3) for v in d.normalized()), d.length))


main()
