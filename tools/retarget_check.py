"""验证两个重定向（MMD/VMD 与 Mixamo）在 Blender 里真的能用。

检查的是「人看得出来的东西」：
1. 起始姿势是站着的（不是趴着/躺着/倒立）
2. 脚高度在合理范围（不做贴地断言 —— 贴地逻辑已删除，MMDD 也没有）
3. 动作真的在动（不是一具僵尸）
4. 没有 NaN

用法： blender.exe -b -P tools/retarget_check.py
"""

import math
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

VMD = os.path.join(HERE, "小鹿乱撞.vmd")
FBX = os.path.join(HERE, "Stomp.fbx")

FAILS = []


def check(name, ok, detail=""):
    print("[retarget] %s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)


def head_pitch(arm):
    """髋 → 头 的俯仰角（读**动画求值姿势**）。站着 ≈ 0~20°，趴着 ≈ 90°。"""
    scn = bpy.context.scene
    cur = scn.frame_current
    scn.frame_set(1)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    ev = arm.evaluated_get(dg)
    hips = ev.pose.bones.get("cf_J_Hips")
    head = ev.pose.bones.get("cf_J_Head")
    scn.frame_set(cur)
    if hips is None or head is None:
        return 999.0
    d = head.matrix.to_translation() - hips.matrix.to_translation()
    if d.length < 1e-6:
        return 999.0
    up = Vector((0.0, 0.0, 1.0))
    return math.degrees(d.angle(up))


def sole_low(arm, frames, step):
    """整段里脚底最低点（世界 Z）。"""
    scn = bpy.context.scene
    pts = []
    for side in ("L", "R"):
        for bn in ("cf_J_Foot01_" + side, "cf_J_Toes01_" + side):
            b = arm.data.bones.get(bn)
            if b is not None:
                pts.append((bn, Vector((b.head_local.x, b.head_local.y, 0.012))))
    lo = 1e9
    cur = scn.frame_current
    try:
        for f in range(1, frames + 1, step):
            scn.frame_set(f)
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
            ev = arm.evaluated_get(dg)
            for bn, local in pts:
                pb = ev.pose.bones.get(bn)
                if pb is None:
                    continue
                w = pb.matrix @ (pb.bone.matrix_local.inverted() @ local)
                lo = min(lo, w.z)
    finally:
        scn.frame_set(cur)
    return lo


def motion_amount(arm, frames, step=8):
    """逐帧看几根关键骨的世界位置，量总位移（判断是不是真的在动）。"""
    scn = bpy.context.scene
    names = [n for n in ("cf_J_Hips", "cf_J_Head", "cf_J_Hand_L",
                         "cf_J_Foot01_L", "cf_J_Foot01_R")
             if n in arm.pose.bones]
    if not names:
        return 0.0
    cur = scn.frame_current
    total = 0.0
    prev = None
    try:
        for f in range(1, frames + 1, step):
            scn.frame_set(f)
            bpy.context.view_layer.update()
            dg = bpy.context.evaluated_depsgraph_get()
            ev = arm.evaluated_get(dg)
            now = []
            for n in names:
                pb = ev.pose.bones.get(n)
                now.append(tuple(pb.matrix.to_translation()) if pb else (0, 0, 0))
            if prev is not None:
                for a, b in zip(prev, now):
                    total += math.dist(a, b)
            prev = now
    finally:
        scn.frame_set(cur)
    return total


def has_nan(arm):
    for pb in arm.pose.bones:
        for v in pb.rotation_quaternion:
            if v != v:
                return True
        for v in pb.location:
            if v != v:
                return True
    return False


def local_rot(pb):
    m = pb.parent.matrix.inverted() @ pb.matrix if pb.parent else pb.matrix
    return m.to_quaternion()


def check_jointfix(arm, frames):
    """VMD 默认应打开关节补偿；关掉后 dam 骨应明显不同。"""
    scn = bpy.context.scene
    dam = "cf_J_LegKnee_dam_L"
    if dam not in arm.pose.bones:
        return
    f = min(max(112, 1), frames)
    scn.frame_set(f)
    bpy.context.view_layer.update()
    if not bool(getattr(arm, "hs2_jointfix", True)):
        arm.hs2_jointfix = True
        bpy.context.view_layer.update()
    on = local_rot(arm.pose.bones[dam]).copy()
    arm.hs2_jointfix = False
    bpy.context.view_layer.update()
    off = local_rot(arm.pose.bones[dam]).copy()
    arm.hs2_jointfix = True
    bpy.context.view_layer.update()
    d = abs(on.dot(off))
    deg = math.degrees(2.0 * math.acos(min(1.0, d)))
    check("VMD 关节补偿生效", deg > 2.0, "frame %d 差 %.1f°" % (f, deg))


def report(tag, arm, frames):
    pitch = head_pitch(arm)
    check("%s 起始姿势站立" % tag, pitch < 60.0, "俯仰 %.0f°" % pitch)
    # 不做贴地断言（贴地逻辑已删除，MMDD 也没有）；只做合理性范围检查
    lo = sole_low(arm, frames, max(1, frames // 60))
    check("%s 脚高度合理" % tag, -0.5 < lo < 1.0, "最低 %.3f m" % lo)
    mv = motion_amount(arm, frames)
    check("%s 动作在动" % tag, mv > 0.05, "位移 %.2f m" % mv)
    check("%s 无 NaN" % tag, not has_nan(arm))


def main():
    import addon_utils
    try:
        addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    except Exception as e:
        print("[retarget] 启用插件失败：%s" % e)

    from hs2_rig_kit import ops

    print("[retarget] Blender %s" % bpy.app.version_string)
    # 游戏目录
    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    if not p.game_dir:
        p.game_dir = ops.detect_game_dir()
    print("[retarget] 游戏目录 %s" % p.game_dir)

    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects.get("HS2_Default")
    check("加载角色", arm is not None)

    # ---- VMD（直接映射 + 虚拟骨架两种）----
    if os.path.isfile(VMD):
        scn = bpy.context.scene
        scn.render.fps = 30
        for tag, dummy in (("VMD直接", False), ("VMD虚拟骨", True)):
            bpy.ops.hs2.clear_rig()
            r = bpy.ops.hs2.import_vmd(filepath=VMD, scale=0.0832,
                                       follow=False, use_dummy=dummy)
            check("%s 导入" % tag, "FINISHED" in str(r), str(r))
            frames = scn.frame_end
            report(tag, arm, frames)
            if dummy:
                check_jointfix(arm, frames)
            print("[retarget] %s %d 帧" % (tag, frames))
    else:
        print("[retarget] 跳过 VMD（没找到 %s）" % VMD)

    # ---- Mixamo ----
    if os.path.isfile(FBX):
        scn = bpy.context.scene
        r = bpy.ops.hs2.import_mixamo(filepath=FBX, with_fingers=True,
                                      follow=False, keep_source=False)
        check("Mixamo 导入", "FINISHED" in str(r), str(r))
        frames = scn.frame_end
        report("Mixamo", arm, frames)
        print("[retarget] Mixamo %d 帧" % frames)
    else:
        print("[retarget] 跳过 Mixamo（没找到 %s）" % FBX)

    print("[retarget] %s" % ("全部通过" if not FAILS else "失败：%s" % FAILS))
    return 1 if FAILS else 0


raise SystemExit(main())
