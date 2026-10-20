"""A/B 实验：逐个关掉 use_tail / ik_limit，看哪个让脚「拖得动」。"""

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


def main():
    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    if not p.game_dir:
        p.game_dir = ops.detect_game_dir()
    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    bpy.ops.hs2.add_rig(follow=False, fingers=False)
    bpy.context.view_layer.update()

    scn = bpy.context.scene
    scn.frame_set(scn.frame_start)
    bpy.context.view_layer.update()

    foot = arm.pose.bones[rig.foot_ctrl("L")]
    calf_d = arm.pose.bones["cf_J_LegLow01_L"]
    foot_d = arm.pose.bones["cf_J_Foot01_L"]
    con = rig._con(arm, "HS2_IK_leg_L")
    owner = arm.pose.bones[rig.mech_calf("L")]
    thigh = arm.pose.bones[rig.mech_thigh("L")]

    def ankle_pos():
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = arm.evaluated_get(dg)
        return ev.pose.bones["cf_J_Foot01_L"].matrix.to_translation()

    def knee_flex():
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = arm.evaluated_get(dg)
        t = ev.pose.bones[rig.mech_thigh("L")]
        c = ev.pose.bones[rig.mech_calf("L")]
        a = (t.tail - t.head)
        b = (c.tail - c.head)
        return math.degrees(a.angle(b))

    def trial(tag, lift=0.10):
        foot.matrix = arm.pose.bones[rig.foot_ctrl("L")].matrix.copy()
        # 复位
        bpy.context.view_layer.update()
        base = ankle_pos()
        m = foot.matrix.copy()
        m.translation = base + Vector((0.0, 0.0, lift))
        foot.matrix = m
        bpy.context.view_layer.update()
        after = ankle_pos()
        d = after - base
        print("  %-46s 踝位移=(%+.3f,%+.3f,%+.3f) |d|=%.3f 膝弯 %.1f°"
              % (tag, d.x, d.y, d.z, d.length, knee_flex()))

    print("\n===== 当前设置（use_tail=%s, limits x=%s y=%s z=%s）====="
          % (con.use_tail, owner.use_ik_limit_x, owner.use_ik_limit_y,
             owner.use_ik_limit_z))
    trial("原样")

    print("\n===== 关掉 ik_limit =====")
    for ax in ("x", "y", "z"):
        setattr(owner, "use_ik_limit_" + ax, False)
        setattr(thigh, "use_ik_limit_" + ax, False)
    trial("limits OFF")

    print("\n===== 关掉 stiffness =====")
    for pb in (owner, thigh):
        pb.ik_stiffness_x = 0.0
        pb.ik_stiffness_y = 0.0
        pb.ik_stiffness_z = 0.0
    trial("limits OFF + stiffness 0")

    print("\n===== 改回 use_tail=True =====")
    con.use_tail = True
    trial("use_tail=True")

    print("\n===== use_tail=True + limits ON =====")
    for ax, lo, hi in (("x", 0.0, 155.0), ("y", 0.0, 0.0), ("z", -10.0, 10.0)):
        setattr(owner, "use_ik_limit_" + ax, True)
        setattr(owner, "ik_min_" + ax, math.radians(lo))
        setattr(owner, "ik_max_" + ax, math.radians(hi))
    trial("use_tail=True + 膝盖限制")

    print("\n===== 只锁 Y（不限制 X/Z）+ use_tail=False =====")
    con.use_tail = False
    owner.use_ik_limit_x = False
    owner.use_ik_limit_z = False
    owner.use_ik_limit_y = True
    owner.ik_min_y = 0.0
    owner.ik_max_y = 0.0
    trial("只锁 Y")

    print("\n===== pole_angle =====")
    print("  leg pole_angle = %.1f°  chain=%d"
          % (math.degrees(con.pole_angle), con.chain_count))
    print("  owner=%s  ik_limit X(%s) Y(%s) Z(%s)"
          % (owner.name, owner.use_ik_limit_x, owner.use_ik_limit_y,
             owner.use_ik_limit_z))


main()
