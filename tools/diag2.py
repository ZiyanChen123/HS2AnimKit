"""诊断 2：IKB 机械骨的本地轴 + 拖远时变形骨的实际角度变化。"""

import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import addon_utils
addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)

from hs2_rig_kit import ops, rig  # noqa: E402


def v3(v):
    return "(%+.3f,%+.3f,%+.3f)" % (v[0], v[1], v[2])


def main():
    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    if not p.game_dir:
        p.game_dir = ops.detect_game_dir()
    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    bpy.ops.hs2.add_rig(follow=False, fingers=False)
    bpy.context.view_layer.update()

    print("\n===== IKB / CAR 骨的本地轴（静止）=====")
    for n in ("IKB_thigh_L", "IKB_calf_L", "IKB_upper_L", "IKB_lower_L",
              "CAR_thigh_L", "CAR_calf_L", "IK_foot_L"):
        b = arm.data.bones.get(n)
        if b is None:
            print("  missing", n)
            continue
        m = b.matrix_local
        print("  %-13s X=%s Y=%s Z=%s" % (n, v3(m.col[0].to_3d()),
                                          v3(m.col[1].to_3d()),
                                          v3(m.col[2].to_3d())))

    print("\n===== 拖远脚，测小腿变形骨的扭转 =====")
    foot = arm.pose.bones[rig.foot_ctrl("L")]
    start = foot.matrix.to_translation()
    scn = bpy.context.scene
    scn.frame_set(scn.frame_start)
    bpy.context.view_layer.update()

    def sample():
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = arm.evaluated_get(dg)
        calf = ev.pose.bones["cf_J_LegLow01_L"]
        thigh = ev.pose.bones["cf_J_LegUp00_L"]
        # 小腿相对大腿的弯曲角
        # 取小腿的世界 Y 轴（骨方向）与大腿世界 Y 轴
        q1 = thigh.matrix.to_quaternion()
        q2 = calf.matrix.to_quaternion()
        rel = q1.inverted() @ q2
        ax, ang = rel.to_axis_angle()
        # 扭转：绕自身骨轴的分量
        twist = 0.0
        if abs(ax.length - 1.0) < 1e-3:
            twist = math.degrees(ang) * ax.y
        return (math.degrees(ang), twist, ax.z, calf.matrix.to_translation())

    print("  基线（不动）: 弯曲 %.1f°  绕Y扭转 %+.1f°  axisZ %+.3f  踝 %s"
          % sample())

    for dy in (-0.1, -0.25, -0.4, -0.55, -0.7, 0.2, 0.4):
        m = foot.matrix.copy()
        m.translation = start + Vector((0.0, dy, 0.0))
        foot.matrix = m
        ang, tw, az, pos = sample()
        print("  脚Y%+0.2f: 弯曲 %6.1f°  绕Y扭转 %+7.1f°  axisZ %+.3f  踝 z=%.3f"
              % (dy, ang, tw, az, pos.z))

    print("\n  侧向：")
    for dx in (0.15, 0.3, 0.45, 0.6, -0.3, -0.6):
        m = foot.matrix.copy()
        m.translation = start + Vector((dx, 0.0, 0.0))
        foot.matrix = m
        ang, tw, az, pos = sample()
        print("  脚X%+0.2f: 弯曲 %6.1f°  绕Y扭转 %+7.1f°  axisZ %+.3f"
              % (dx, ang, tw, az))

    print("\n  抬高：")
    for dz in (0.2, 0.4, 0.6, 0.8, 1.0):
        m = foot.matrix.copy()
        m.translation = start + Vector((0.0, 0.0, dz))
        foot.matrix = m
        ang, tw, az, pos = sample()
        print("  脚Z%+0.2f: 弯曲 %6.1f°  绕Y扭转 %+7.1f°  axisZ %+.3f"
              % (dz, ang, tw, az))


main()
