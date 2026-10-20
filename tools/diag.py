"""诊断：控制骨朝向 / IK 几何 / 拖远后膝盖翻转情况。

用法： blender.exe -b --factory-startup -P tools/diag.py
"""

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
    return "(%.3f, %.3f, %.3f)" % (v[0], v[1], v[2])


def main():
    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    if not p.game_dir:
        p.game_dir = ops.detect_game_dir()
    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    print("\n===== 0. 先生成控制器（后面的检查都依赖它）=====")
    bpy.ops.hs2.add_rig(follow=False, fingers=False)
    bpy.context.view_layer.update()

    print("\n===== 1. 控制骨静止朝向（Y 轴 = 骨骼方向）=====")
    for n in ("CTRL_root", "CTRL_center", "CTRL_lower", "CTRL_upper",
              "CTRL_head", "IK_foot_L", "IK_hand_L", "CTRL_thigh_L"):
        b = arm.data.bones.get(n)
        if b is None:
            print("  missing", n)
            continue
        m = b.matrix_local
        print("  %-14s Y=%s  Z=%s  len=%.3f"
              % (n, v3(m.col[1].to_3d()), v3(m.col[2].to_3d()), b.length))

    print("\n===== 2. 游戏骨静止朝向对照 =====")
    for n in ("cf_J_Hips", "cf_J_Spine01", "cf_J_Foot01_L", "cf_J_Hand_L"):
        b = arm.data.bones.get(n)
        m = b.matrix_local
        print("  %-16s Y=%s  len=%.4f" % (n, v3(m.col[1].to_3d()), b.length))

    print("\n===== 3. IK 几何：机械链长度 vs 实际腿长 =====")
    for side in ("L", "R"):
        th = arm.data.bones.get(rig.mech_thigh(side))
        cf = arm.data.bones.get(rig.mech_calf(side))
        real_up = arm.data.bones.get("cf_J_LegUp00_" + side)
        real_lo = arm.data.bones.get("cf_J_LegLow01_" + side)
        hip = th.head_local
        knee = cf.head_local
        ankle = cf.tail_local
        print("  %s IKB: hip->knee %.4f, knee->ankle %.4f, 合计 %.4f"
              % (side, (knee - hip).length, (ankle - knee).length,
                 (knee - hip).length + (ankle - knee).length))
        print("     游戏: LegUp %.4f, LegLow %.4f, 合计 %.4f"
              % (real_up.length, real_lo.length,
                 real_up.length + real_lo.length))
        print("     髋高度 %.4f  踝高度 %.4f" % (hip.z, ankle.z))

    print("\n===== 4. IK 约束设置 =====")
    for con_name in ("HS2_IK_leg_L", "HS2_IK_arm_L"):
        c = rig._con(arm, con_name)
        if c is None:
            print("  missing", con_name)
            continue
        print("  %s chain=%d use_tail=%s stretch=%s pole=%s pole_angle=%.1f"
              % (con_name, c.chain_count, c.use_tail, c.use_stretch,
                 getattr(c, "pole_subtarget", ""), math.degrees(c.pole_angle)))
        owner = c.id_data.pose.bones
        for pb in owner:
            if pb.constraints.get(con_name) is not None:
                print("     owner=%s  ik_limits X(%s %.0f~%.0f) Y(%s %.0f~%.0f) Z(%s %.0f~%.0f)"
                      % (pb.name,
                         pb.use_ik_limit_x, math.degrees(pb.ik_min_x), math.degrees(pb.ik_max_x),
                         pb.use_ik_limit_y, math.degrees(pb.ik_min_y), math.degrees(pb.ik_max_y),
                         pb.use_ik_limit_z, math.degrees(pb.ik_min_z), math.degrees(pb.ik_max_z)))
                break

    print("\n===== 5. 拖远脚，看膝盖是否翻转 =====")
    bpy.context.view_layer.update()

    scn = bpy.context.scene
    scn.frame_set(scn.frame_start)
    bpy.context.view_layer.update()

    foot = arm.pose.bones[rig.foot_ctrl("L")]
    knee_mech = arm.pose.bones[rig.mech_thigh("L")]
    calf_mech = arm.pose.bones[rig.mech_calf("L")]
    start = foot.matrix.to_translation()

    print("  起始脚 %s" % v3(start))
    for dy in (0.0, -0.1, -0.2, -0.3, -0.4, -0.5, 0.1, 0.2, 0.3):
        foot.matrix = foot.matrix.copy()
        m = foot.matrix.copy()
        m.translation = start + Vector((0.0, dy, 0.0))
        foot.matrix = m
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = arm.evaluated_get(dg)
        k = ev.pose.bones[rig.mech_thigh("L")].tail
        a = ev.pose.bones[rig.mech_calf("L")].tail
        co = ev.pose.bones[ev.pose.bones[rig.mech_calf("L")].name]
        reached = (a - m.translation).length
        print("    脚 Y%+0.2f  膝=%s  踝=%s  误差 %.4f"
              % (dy, v3(k), v3(a), reached))

    print("\n===== 6. 侧向拖动（最容易翻的方向）=====")
    for dx in (0.0, 0.15, 0.3, 0.45, -0.15, -0.3, -0.45):
        m = foot.matrix.copy()
        m.translation = start + Vector((dx, 0.0, 0.0))
        foot.matrix = m
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = arm.evaluated_get(dg)
        k = ev.pose.bones[rig.mech_thigh("L")].tail
        print("    X%+0.2f  膝=%s" % (dx, v3(k)))

    print("\n===== 7. 举高脚（过顶）=====")
    for dz in (0.0, 0.2, 0.4, 0.6, 0.8):
        m = foot.matrix.copy()
        m.translation = start + Vector((0.0, 0.0, dz))
        foot.matrix = m
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = arm.evaluated_get(dg)
        k = ev.pose.bones[rig.mech_thigh("L")].tail
        print("    Z%+0.2f  膝=%s" % (dz, v3(k)))


main()
