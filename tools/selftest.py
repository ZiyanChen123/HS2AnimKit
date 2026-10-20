"""端到端自检（不需要开 Blender 界面）。

    blender -b --factory-startup -P tools/selftest.py
    blender -b --factory-startup -P tools/selftest.py -- --clip cook_Idle_00_S --no-export

检查项：
  1. 加载默认角色（693 骨 = 602 游戏骨 + 91 面部骨，身体 + 头部网格）
  2. 头部网格和身体在同一个空间（z 区间衔接）
  3. 导入动作后生成控制器，IK 逐帧复现 FK（误差应为 1e-4 量级）
  4. 拖脚控制器，脚踝精确跟随
  5. 重建控制器 / 换动作 后误差依旧
  6. IK 关掉、吸附、写回 FK 都不跳
  7. 烘焙 + 导出 FBX，回读确认没有控制骨残留
"""
import os
import sys
import time

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from hs2_rig_kit import ops, rig

GAME = os.environ.get("HS2_GAME_DIR", "D:/game/HS2")
ARM = "HS2_Default"

WATCH = ["cf_J_Hips", "cf_J_Kosi01", "cf_J_Kosi02",
         "cf_J_Spine01", "cf_J_Spine02", "cf_J_Spine03", "cf_J_Neck", "cf_J_Head",
         "cf_J_Shoulder_L", "cf_J_ArmUp00_L", "cf_J_ArmLow01_L", "cf_J_Hand_L",
         "cf_J_Shoulder_R", "cf_J_ArmUp00_R", "cf_J_ArmLow01_R", "cf_J_Hand_R",
         "cf_J_LegUp00_L", "cf_J_LegLow01_L", "cf_J_LegLowRoll_L", "cf_J_Foot01_L",
         "cf_J_Foot02_L", "cf_J_LegUp00_R", "cf_J_LegLow01_R", "cf_J_LegLowRoll_R",
         "cf_J_Foot01_R"]

FAILS = []


def log(*a):
    print("[self]", *a)
    sys.stdout.flush()


def check(label, ok, detail=""):
    log("%s %s %s" % ("PASS" if ok else "FAIL", label, detail))
    if not ok:
        FAILS.append(label)


def pose_drift(arm, frames):
    """IK 开着的评估结果 vs 原始 FK 的局部矩阵差。"""
    scn = bpy.context.scene
    worst = (0.0, 0, "")
    for f in frames:
        scn.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = arm.evaluated_get(dg)
        for n in WATCH:
            a = arm.pose.bones[n].matrix
            b = ev.pose.bones[n].matrix
            d = max(abs(a[r][c] - b[r][c]) for r in range(4) for c in range(4))
            if d > worst[0]:
                worst = (d, f, n)
    return worst


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    do_export = "--no-export" not in argv
    rest = [a for a in argv if not a.startswith("--")]
    clip = rest[0] if rest else "swim_Idle_00_L"

    try:
        bpy.ops.preferences.addon_enable(module="hs2_rig_kit")
    except Exception:
        # 没装插件也能跑：直接把源码注册进来（面板没有 Preferences 也能工作）
        import hs2_rig_kit
        hs2_rig_kit.register()
        log("（插件未安装，已直接注册源码）")
    prefs = bpy.context.preferences.addons.get("hs2_rig_kit")
    if prefs is not None:
        prefs.preferences.game_dir = GAME

    t0 = time.time()
    bpy.ops.hs2.load_default()
    arm = bpy.data.objects[ARM]
    body = bpy.data.objects.get(ops.BODY_NAME)
    head = bpy.data.objects.get(ops.HEAD_NAME)
    log("加载 %.1fs" % (time.time() - t0))

    check("骨架 693 骨", len(arm.data.bones) == 693, "(%d)" % len(arm.data.bones))
    # 静止朝向必须是单位旋转（游戏里的骨架就是这样）。零长度根骨如果用
    # eb.matrix 直接赋值会得到 90°X，整副骨架跟着翻倒 —— 这个检查防的就是它。
    import math as _m
    axes = ["cf_J_Root", "cf_N_height", "cf_J_Hips", "cf_J_Spine01",
            "cf_J_Head", "cf_J_Foot01_L", "cf_J_Hand_R"]
    worst = max((_m.degrees(arm.data.bones[n].matrix_local.to_quaternion().angle), n)
                for n in axes if n in arm.data.bones)
    check("静止朝向与游戏一致", worst[0] < 2.0, "最大 %.2f° (%s)" % worst)

    check("身体网格", body is not None and len(body.data.vertices) == 9594)
    check("头部网格", head is not None and len(head.data.vertices) > 3000,
          "(%s)" % (len(head.data.vertices) if head else 0))
    if body and head:
        bz = max(v.co.z for v in body.data.vertices)
        hz = min(v.co.z for v in head.data.vertices)
        # 头底部可以略微插进脖子（有重叠才不会有缝）
        check("头身衔接", -0.15 < hz - bz < 0.05, "(身体顶 %.3f / 头底 %.3f)" % (bz, hz))

    g = ops.get_assets(GAME)
    idx = g.clip_index()
    if clip not in idx:
        clip = sorted(idx.keys())[0]
    bpy.ops.hs2.import_clip(clip_name=clip, fps=30.0, follow=False)
    log("动作 %s：%d-%d 帧" % (clip, bpy.context.scene.frame_start,
                              bpy.context.scene.frame_end))

    t0 = time.time()
    # 生成控制器前先记下变形骨的世界矩阵 —— 生成后必须一模一样
    # （控制骨 rest 朝向若和游戏骨不一致，COPY_TRANSFORMS/REPLACE 会把
    #  静止姿势拧歪，表现为「生成控制器后脚变形」）
    _watch = ["cf_J_Foot01_L", "cf_J_Foot01_R", "cf_J_Toes01_L", "cf_J_Toes01_R",
              "cf_J_Hand_L", "cf_J_Hand_R", "cf_J_Head", "cf_J_Hips"]
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    _pre = {n: [tuple(round(v, 6) for v in row)
                for row in arm.evaluated_get(dg).pose.bones[n].matrix]
            for n in _watch}
    bpy.ops.hs2.add_rig(follow=True)
    log("生成控制器 %.1fs" % (time.time() - t0))
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    _worst, _wn = 0.0, ""
    for n in _watch:
        _now = arm.evaluated_get(dg).pose.bones[n].matrix
        for r in range(4):
            for c in range(4):
                d = abs(_now[r][c] - _pre[n][r][c])
                if d > _worst:
                    _worst, _wn = d, "%s[%d][%d]" % (n, r, c)
    check("生成控制器不改变静止姿势", _worst < 1e-4,
          "最大偏差 %.6f (%s)" % (_worst, _wn))

    scn = bpy.context.scene
    step = max(1, (scn.frame_end - scn.frame_start) // 6)
    frames = list(range(scn.frame_start, scn.frame_end + 1, step))

    d, f, n = pose_drift(arm, frames)
    check("IK 复现 FK", d < 5e-3, "max %.5f @f%d %s" % (d, f, n))

    # 人物应该是「站着」的（髋->头 基本朝上），不是躺着的
    scn.frame_set(scn.frame_start)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev0 = arm.evaluated_get(dg)
    up = ev0.pose.bones["cf_J_Head"].head - ev0.pose.bones["cf_J_Hips"].head
    import math as _mm
    pitch = abs(_mm.degrees(_mm.atan2(up.y, up.z)))
    check("动作起始姿势是站立的", pitch < 60.0, "俯仰 %.0f°" % pitch)

    # 拖脚
    scn.frame_set(min(scn.frame_end, 20))
    ctrl = arm.pose.bones[rig.foot_ctrl("L")]
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    before = arm.evaluated_get(dg).pose.bones["cf_J_Foot01_L"].head.copy()
    m = ctrl.matrix.copy()
    m.translation.z += 0.10
    ctrl.matrix = m
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    after = arm.evaluated_get(dg).pose.bones["cf_J_Foot01_L"].head.copy()
    check("拖脚 IK", (after - before - Vector((0, 0, 0.10))).length < 5e-3,
          "位移 %s" % (tuple(round(v, 3) for v in (after - before)),))
    m.translation.z -= 0.10
    ctrl.matrix = m
    bpy.context.view_layer.update()

    # 重建控制器
    bpy.ops.hs2.add_rig(follow=True)
    d, _, _ = pose_drift(arm, frames)
    check("重建控制器后仍然精确", d < 5e-3, "max %.5f" % d)

    # 换动作
    other = "cook_Idle_00_S" if "cook_Idle_00_S" in idx else sorted(idx.keys())[3]
    bpy.ops.hs2.import_clip(clip_name=other, fps=30.0, follow=True)
    frames2 = list(range(bpy.context.scene.frame_start,
                         bpy.context.scene.frame_end + 1, 5))
    d, _, _ = pose_drift(arm, frames2)
    check("换动作后仍然精确", d < 5e-3, "%s max %.5f" % (other, d))

    # IK 开关 / 吸附
    rig.set_ik(arm, False)
    d, _, _ = pose_drift(arm, frames2[:3])
    check("关 IK = 纯 FK", d < 1e-5, "max %.6f" % d)
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    before = arm.evaluated_get(dg).pose.bones["cf_J_Hand_R"].head.copy()
    bpy.ops.hs2.snap_ik()
    rig.set_ik(arm, True)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get(); dg.update()
    after = arm.evaluated_get(dg).pose.bones["cf_J_Hand_R"].head.copy()
    # 允许 1cm 级残差：手臂两段 IK 在接近伸直时解算器会有几毫米误差
    check("吸附后开 IK 不跳", (after - before).length < 0.02,
          "%.4f m" % (after - before).length)

    # IK 写回 FK
    n = rig.snap_fk_to_ik(arm, key=False)
    check("IK 写回 FK", n >= 10 and not rig.ik_state(arm), "(%d 骨)" % n)

    # 烘焙 + 导出
    t0 = time.time()
    bpy.ops.hs2.bake_fk()
    act = arm.animation_data.action
    from hs2_rig_kit import compat
    nfc = compat.fcurve_count(act, arm)
    check("烘焙出关键帧", act is not None and nfc > 800,
          "%d 条曲线 %.1fs" % (nfc, time.time() - t0))

    if do_export:
        out = os.path.join(HERE, "_test", "out")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "selftest.fbx")
        bpy.ops.hs2.export_fbx(filepath=path)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        check("导出 FBX", size > 100000, "%.1f MB" % (size / 1048576.0))

        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.preferences.addon_enable(module="hs2_rig_kit")
        bpy.ops.import_scene.fbx(filepath=path)
        arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        junk = []
        if arms:
            junk = [b.name for b in arms[0].data.bones
                    if b.name.startswith(("IK_", "IKB_", "CAR_", "Toe_"))]
        check("回读骨架干净", bool(arms) and not junk,
              "bones=%s junk=%s" % (len(arms[0].data.bones) if arms else 0,
                                    junk or "无"))

    log("")
    if FAILS:
        log("失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
    else:
        log("全部通过")


main()
