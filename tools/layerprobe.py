"""逐层拆解：VMD 原始 → 虚拟骨世界 → HS2 骨世界 → 官方参照，找出断在哪一层。"""

import math
import os
import sys

import bpy
from mathutils import Quaternion, Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
VMD = os.path.join(HERE, "小鹿乱撞.vmd")


def ang(a, b):
    return math.degrees(2.0 * math.acos(abs(max(-1.0, min(1.0, a.dot(b))))))


def fmt(q):
    e = q.to_euler("ZXY")
    return "(%.0f,%.0f,%.0f)" % tuple(math.degrees(v) for v in e)


def main():
    import addon_utils
    addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    from hs2_rig_kit import boneadj, ops, vmd

    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    p.game_dir = p.game_dir or ops.detect_game_dir()
    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    bpy.ops.hs2.add_rig(follow=False, fingers=False)
    bpy.ops.hs2.import_vmd(filepath=VMD)

    data = vmd.parse(VMD)
    scn = bpy.context.scene

    def q_cvt(q):
        bx, by, bz, bw = vmd.mmd_quat_to_blender(q)
        return Quaternion((bw, bx, by, bz))

    # 检查 HS2 骨 rest 是否真的单位旋转
    print("\n=== HS2 rest 旋转 ===")
    for n in ("cf_J_Hips", "cf_J_Spine01", "cf_J_ArmUp00_L", "cf_J_Hand_L"):
        b = arm.data.bones[n]
        q = b.matrix_local.to_quaternion()
        print("  %-16s rest=%s 角=%.1f°" % (n, fmt(q), math.degrees(q.angle)))

    table = boneadj.build_arm_chain(10.0, 32.0, 0.0)

    for f in (1, 112):
        scn.frame_set(f)
        bpy.context.view_layer.update()
        print("\n===== 帧 %d =====" % f)
        for mmd_name, vname, hs2 in (
                ("上半身", "MMD_SPINE1", "cf_J_Spine01"),
                ("左腕", "MMD_UPARM_L", "cf_J_ArmUp00_L"),
                ("左ひじ", "MMD_LOWARM_L", "cf_J_ArmLow01_L"),
                ("左手首", "MMD_HAND_L", "cf_J_Hand_L")):
            keys = data["bones"].get(mmd_name)
            raw = vmd._sample(keys, f, True) if keys else None
            if raw is None:
                print("  %s: VMD 无数据" % mmd_name)
                continue
            adj = table.get(hs2)
            qa = adj.apply(raw) if adj else raw
            print("  %-8s VMD=%s" % (mmd_name, fmt(q_cvt(raw))))
            print("           官方调整后=%s (差 %5.1f°)" %
                  (fmt(q_cvt(qa)), ang(q_cvt(raw), q_cvt(qa))))
            vb = arm.pose.bones.get(vname)
            if vb is not None:
                print("           虚拟骨世界=%s" % fmt(vb.matrix.to_quaternion()))
            pb = arm.pose.bones.get(hs2)
            if pb is not None:
                cons = [(c.type, c.subtarget, round(c.influence, 2)) for c in pb.constraints]
                print("           约束=%s 本地旋转=%s" % (cons, fmt(pb.rotation_quaternion)))
                print("           HS2 世界  =%s   与官方原始差 %.1f°  与调整后差 %.1f°"
                      % (fmt(pb.matrix.to_quaternion()),
                         ang(pb.matrix.to_quaternion(), q_cvt(raw)),
                         ang(pb.matrix.to_quaternion(), q_cvt(qa))))


main()
