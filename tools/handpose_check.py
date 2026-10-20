"""验证工作室手势（cf_anmShapeHand）能被正确读取并驱动手指骨。

用法：
    blender.exe -b --factory-startup -P tools/handpose_check.py
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import addon_utils  # noqa: E402

try:
    addon_utils.enable("hs2_rig_kit", default_set=True)
except Exception as e:
    print("[hand] 启用插件失败:", e)

from hs2_rig_kit import ops, handpose  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print("[hand] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)


def main():
    print("[hand] Blender %s" % bpy.app.version_string)
    g = ops.detect_game_dir()
    if not g:
        print("[hand] 找不到游戏目录")
        return 2
    poses = handpose.load_poses(g)
    check("读到 21 个手势", len(poses) == 21, "实际 %d" % len(poses))
    check("每个手势 30 根手指骨",
          all(len(p) == 30 for p in poses),
          "min=%d max=%d" % (min(len(p) for p in poses),
                             max(len(p) for p in poses)))

    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    scn = bpy.context.scene

    # 逐手势写入，确认没有 NaN、且不同手势确实不同
    sigs = []
    for i, p in enumerate(poses):
        n = handpose.apply_pose(arm, i, side="BOTH", poses=poses)
        bpy.context.view_layer.update()
        if n != 30:
            check("手势 %02d 写入 30 骨" % (i + 1), False, "写入 %d" % n)
            continue
        vals = []
        for b in handpose.FINGER_BONES:
            q = arm.pose.bones[b].rotation_quaternion
            for c in q:
                if c != c:
                    check("手势 %02d 无 NaN" % (i + 1), False)
                    break
            vals.extend((round(q.w, 3), round(q.x, 3),
                         round(q.y, 3), round(q.z, 3)))
        sigs.append(tuple(vals))
    check("30 根手指骨全部写入", len(sigs) == 21,
          "%d/21" % len(sigs))
    check("不同手势不相同", len(set(sigs)) >= 15,
          "不同签名 %d" % len(set(sigs)))

    # 应用第 1 个手势后指尖应相对第 20 个（近似静止）有明显位移
    def tip():
        bpy.context.view_layer.update()
        return arm.pose.bones["cf_J_Hand_Index03_L"].head.copy()

    handpose.apply_pose(arm, 20, side="BOTH", poses=poses)
    p0 = tip()
    handpose.apply_pose(arm, 0, side="BOTH", poses=poses)
    p1 = tip()
    d = (p1 - p0).length
    check("手势改变指尖位置", d > 0.01, "%.3f m" % d)

    # 还原
    handpose.apply_pose(arm, -1, side="BOTH", poses=poses)
    bpy.context.view_layer.update()
    q = arm.pose.bones["cf_J_Hand_Index03_L"].rotation_quaternion
    check("还原手势 → 单位旋转", abs(q.w - 1.0) < 1e-6 and
          abs(q.x) + abs(q.y) + abs(q.z) < 1e-6)

    # UI 操作符
    scn.hs2_hand_L = "5"
    r = bpy.ops.hs2.apply_hand_pose(mode="BOTH", keyframe=False)
    check("操作符写入关键帧(双手)", "FINISHED" in str(r), str(r))
    r = bpy.ops.hs2.apply_hand_pose(mode="RESET", keyframe=False)
    check("操作符还原手势", "FINISHED" in str(r), str(r))

    # VMD 虚拟骨架路径：手指有 MMDD 约束，手势必须能盖掉它
    vmd = os.path.join(HERE, "小鹿乱撞.vmd")
    if os.path.isfile(vmd):
        bpy.ops.hs2.import_vmd(filepath=vmd, scale=0.0832, follow=False,
                               use_dummy=True, use_ik=True)
        scn.frame_set(112)
        bpy.context.view_layer.update()
        pb = arm.pose.bones["cf_J_Hand_Index03_L"]
        q0 = pb.matrix.to_quaternion().copy()
        scn.hs2_hand_L = "5"
        bpy.context.view_layer.update()
        q1 = arm.pose.bones["cf_J_Hand_Index03_L"].matrix.to_quaternion()
        d = math.degrees(2.0 * math.acos(min(1.0, abs(q0.dot(q1)))))
        check("VMD 下选中即预览", d > 2.0, "差 %.1f°" % d)
        scn.hs2_hand_L = "-1"
        bpy.context.view_layer.update()
        muted = [c.mute for c in
                 arm.pose.bones["cf_J_Hand_Index03_L"].constraints
                 if c.name.startswith("MMDD_")]
        check("还原手势恢复 MMDD 约束", not any(muted), str(muted))

    print("[hand] %s" % ("全部通过" if not FAILS else "失败：%s" % FAILS))
    return 1 if FAILS else 0


raise SystemExit(main())
