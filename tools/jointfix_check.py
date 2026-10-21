"""验证关节校正（dam 骨）和游戏原动作的逐帧误差。

用法：
    blender.exe -b --factory-startup -P tools/jointfix_check.py

做法：导入一个游戏动作 → 记下 dam 骨的真实 local 旋转 → 删掉 dam 骨的
fcurve（模拟用户改姿势/控制器）→ 打开关节补偿 → 逐帧比较校正结果和
真实 dam 骨。膝盖/手肘的拟合残差应该 < 1.5°。
"""

import math
import os
import sys

import bpy
from mathutils import Quaternion

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import addon_utils  # noqa: E402

try:
    addon_utils.enable("hs2_rig_kit", default_set=True)
except Exception as e:
    print("[fix] 启用插件失败:", e)

from hs2_rig_kit import api, ops, rig  # noqa: E402

CLIPS = ("L_Broken_Chair_A", "L_Broken_Chair_B", "swim_Idle_00_L",
         "cook_Idle_00_S", "S_Broken_Chair_A", "M_Broken_Chair_A",
         "S_Broken_Chair_B", "Broken_stand_A")
DAMS = tuple(
    n % side
    for n in ("cf_J_LegKnee_dam_%s", "cf_J_LegKnee_back_%s",
              "cf_J_ArmElboura_dam_%s", "cf_J_ArmElbo_dam_01_%s",
              "cf_J_SiriDam_%s", "cf_J_LegUpDam_%s")
    for side in ("L", "R")
)
FAILS = []


def ang(a, b):
    d = abs(a.dot(b))
    return math.degrees(2.0 * math.acos(min(1.0, d)))


def local_rot(pb):
    """求值后的 local rotation（包含约束），不是原始 rotation_quaternion。"""
    if pb.parent is not None:
        m = pb.parent.matrix.inverted() @ pb.matrix
    else:
        m = pb.matrix
    return m.to_quaternion()


def main():
    print("[fix] Blender %s" % bpy.app.version_string)
    available = {e["name"] for e in api.search(kind="studio", limit=100000)}
    clips = [c for c in CLIPS if c in available][:3]
    if not clips:
        clips = [e["name"] for e in
                 api.search(keyword="Idle", kind="studio", limit=3)]
    if not clips:
        print("[fix] 失败：游戏里找不到可用的 Studio 动作")
        return 1

    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    scn = bpy.context.scene

    tested = 0
    for clip in clips:
        bpy.ops.hs2.clear_rig()
        try:
            bpy.ops.hs2.import_clip(clip_name=clip, follow=False)
        except RuntimeError as e:
            print("[fix] 跳过 %s：%s" % (clip, e))
            continue
        tested += 1
        n = scn.frame_end
        act = arm.animation_data.action
        curves = _fcurves(act, arm)

        # 1) 真实 dam local
        truth = {d: [] for d in DAMS}
        for f in range(1, n + 1):
            scn.frame_set(f)
            bpy.context.view_layer.update()
            for d in DAMS:
                pb = arm.pose.bones.get(d)
                truth[d].append(local_rot(pb) if pb else None)

        # 2) 不删 dam fcurve：校正约束是 REPLACE/1.0，会直接覆盖原曲线。
        #    （逐条 remove 这个 layered action 的 fcurve 会踩到失效指针，
        #      反而把主骨曲线改坏，上一版测试的 7~20° 假误差就是这么来的。）
        # 3) 建校正并打开
        rig._build_correctives(arm)
        rig.set_joint_fix(arm, True)

        stats = {d: [] for d in DAMS}
        for i, f in enumerate(range(1, n + 1)):
            scn.frame_set(f)
            bpy.context.view_layer.update()
            for d in DAMS:
                pb = arm.pose.bones.get(d)
                if pb is None or truth[d][i] is None:
                    continue
                stats[d].append(ang(local_rot(pb), truth[d][i]))

        print("[fix] %s (%d 帧)" % (clip, n))
        for d in DAMS:
            if not stats[d]:
                continue
            mean = sum(stats[d]) / len(stats[d])
            mx = max(stats[d])
            print("  %-26s mean %5.2f°  max %5.2f°" % (d, mean, mx))
            if "SiriDam" not in d and "LegUpDam" not in d:
                check(d, mean < 4.0 and mx < 5.0, "mean %.2f max %.2f"
                      % (mean, mx))

    if not tested:
        FAILS.append("没有成功导入任何动作")
    print("[fix] %s" % ("全部通过" if not FAILS else "失败：%s" % FAILS))
    return 1 if FAILS else 0


def check(name, ok, detail=""):
    print("[fix] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)


def _fcurves(act, arm):
    from hs2_rig_kit import compat
    return compat.bag(act, arm)


raise SystemExit(main())
