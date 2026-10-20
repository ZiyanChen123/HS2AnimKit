"""验证 Studio 式控制器（13 个目标 + 5 个 ActiveIK 分组）真的能驱动骨架。

用法：
    blender.exe -b --factory-startup -P tools/studio_check.py

不是「面板上有个按钮」检查，而是移动控制器、量对应骨的世界位移。
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
    print("[studio] 启用插件失败:", e)

from hs2_rig_kit import ops, rig  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print("[studio] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)


def head(arm, name):
    bpy.context.view_layer.update()
    pb = arm.pose.bones.get(name)
    return pb.head.copy() if pb else None


def move_x(arm, ctrl, dist=0.1):
    pb = arm.pose.bones[ctrl]
    pb.location.x += dist
    return pb


def main():
    print("[studio] Blender %s" % bpy.app.version_string)
    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects.get("HS2_Default")
    bpy.ops.hs2.clear_rig()
    bpy.ops.hs2.add_rig(follow=False)

    # 1) 13 个目标 + 别名
    names = [rig.body_ctrl()]
    for side in ("L", "R"):
        names += [rig.shoulder_ctrl(side), rig.elbow_ctrl(side),
                  rig.hand_ctrl(side), rig.thigh_ctrl(side),
                  rig.knee_ctrl(side), rig.foot_ctrl(side),
                  rig.toe_ctrl(side)]
    missing = [n for n in names if n not in arm.pose.bones]
    check("13 个 Studio 目标齐全", not missing, "缺少 %s" % missing)

    # 2) 位置效应器：移动控制器 → 对应骨/整条链移动
    cases = [
        (rig.body_ctrl(), "cf_J_Hips"),
        (rig.shoulder_ctrl("L"), "cf_J_Hand_L"),
        (rig.thigh_ctrl("L"), "cf_J_LegUp00_L"),
        (rig.hand_ctrl("L"), "cf_J_Hand_L"),
        (rig.foot_ctrl("L"), "cf_J_Foot01_L"),
    ]
    for ctrl, bone in cases:
        h0 = head(arm, bone)
        pb = move_x(arm, ctrl)
        h1 = head(arm, bone)
        pb.location.x -= 0.1
        d = (h1 - h0).length if h0 and h1 else 0.0
        check("%s 驱动 %s" % (ctrl, bone), d > 0.05,
              "位移 %.3f m" % d)

    # 3) 腿的 IK：移动大腿时脚仍钉在原地（foot target 不动）
    f0 = head(arm, "cf_J_Foot01_L")
    pb = move_x(arm, rig.thigh_ctrl("L"))
    f1 = head(arm, "cf_J_Foot01_L")
    pb.location.x -= 0.1
    d = (f1 - f0).length
    check("STU_Thigh 移动时脚 IK 保持", d < 0.03, "脚位移 %.3f m" % d)

    # 4) ActiveIK 分组
    arm.hs2_studio_arm_L = False
    bpy.context.view_layer.update()
    off = arm.pose.bones[rig.hand_ctrl("L")].get("ik", 1.0)
    check("关臂分组 → hand ik=0", abs(off) < 1e-6, "ik=%s" % off)
    arm.hs2_studio_arm_L = True
    bpy.context.view_layer.update()
    on = arm.pose.bones[rig.hand_ctrl("L")].get("ik", 0.0)
    check("开臂分组 → hand ik=1", abs(on - 1.0) < 1e-6, "ik=%s" % on)

    # 5) Body 分组保存/恢复偏移
    body = arm.pose.bones[rig.body_ctrl()]
    body.location.x = 0.2
    arm.hs2_studio_body = False
    bpy.context.view_layer.update()
    check("关 Body → 偏移归零", abs(body.location.x) < 1e-6,
          "x=%.3f" % body.location.x)
    arm.hs2_studio_body = True
    bpy.context.view_layer.update()
    check("开 Body → 偏移恢复", abs(body.location.x - 0.2) < 1e-6,
          "x=%.3f" % body.location.x)

    print("[studio] %s" % ("全部通过" if not FAILS else "失败：%s" % FAILS))
    return 1 if FAILS else 0


raise SystemExit(main())
