"""导出给 UE 的 FBX 验收：单位必须是厘米、根骨缩放必须是 1。

在 Blender 后台跑：

    blender -b --factory-startup --python tools/test_ue_export.py
    blender -b --factory-startup --python tools/test_ue_export.py -- <fbx 路径>

为什么查这个
------------
UE 的骨架以厘米为单位。Blender 导出器默认把「米→厘米」的 100 倍记在文件单位
（UnitScaleFactor=100）上，UE 导入时会把这一百倍乘到**根骨** p_cf_anim 上：
模型大小对，但根骨缩放变成 100，跟别的骨架做「兼容骨架」重定向时动画就错。
插件现在导出前把数据换成厘米（见 hs2_rig_kit/ueunit.py），这份脚本守住三点：

1. 文件单位是 1.0（厘米），骨架节点没有缩放
2. 骨头数值是厘米（髋骨 ≈ 100 上下，不是 1 上下）
3. 导出前后场景一模一样（rest 骨、顶点、动作曲线都还原了）
"""

import os
import sys
import tempfile

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

GAME = os.environ.get("HS2_GAME_DIR", r"D:\game\HS2")
CLIP = "mc_f_move_07_L"


def parse_fbx(path):
    sys.path.append(os.path.join(bpy.utils.resource_path("LOCAL"),
                                 "scripts", "addons_core"))
    from io_scene_fbx import parse_fbx
    return parse_fbx.parse(path)[0]


def fbx_facts(path):
    """单位因子 + 骨架节点缩放 + 髋骨的局部平移（取第一个模型里的值）。"""
    root = parse_fbx(path)
    unit = None
    for e in root.elems:
        if e.id != b"GlobalSettings":
            continue
        for props in e.elems:
            if props.id != b"Properties70":
                continue
            for q in props.elems:
                if q.id == b"P" and q.props[0].decode() == "UnitScaleFactor":
                    unit = float(q.props[-1])

    objs = [e for e in root.elems if e.id == b"Objects"][0]
    name2id = {}
    for e in objs.elems:
        if len(e.props) >= 2 and isinstance(e.props[1], bytes):
            name2id[e.props[0]] = e.props[1].split(b"\x00")[0].decode("utf-8", "replace")

    props = {}
    hips_t = None
    for e in objs.elems:
        if e.id != b"Model":
            continue
        nm = name2id.get(e.props[0])
        for block in e.elems:
            if block.id != b"Properties70":
                continue
            got = {}
            for q in block.elems:
                if q.id != b"P":
                    continue
                key = q.props[0].decode()
                if key.startswith("Lcl "):
                    nums = [float(x) for x in q.props if isinstance(x, (int, float))]
                    got[key] = nums[-3:]
            if nm in ("Armature", "p_cf_anim"):
                props[nm] = got
            if nm == "cf_J_Hips" and "Lcl Translation" in got:
                hips_t = got["Lcl Translation"]
    return unit, props, hips_t


def snapshot(arm, body):
    bpy.context.view_layer.update()
    rest = {b.name: b.head_local.copy() for b in arm.data.bones}
    pose = {pb.name: pb.matrix.copy() for pb in arm.pose.bones}
    dg = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(dg)
    me = ev.to_mesh()
    zs = [(body.matrix_world @ v.co).z for v in me.vertices]
    box = (min(zs), max(zs), len(me.vertices))
    ev.to_mesh_clear()
    curves = []
    ad = arm.animation_data
    if ad is not None and ad.action is not None:
        from hs2_rig_kit import compat
        bag = compat.bag(ad.action, arm)
        curves = [(fc.data_path, fc.array_index, [round(k.co[1], 6) for k in fc.keyframe_points[:4]])
                  for fc in bag]
    return rest, pose, box, curves


def same(s0, s1):
    rest0, pose0, box0, cur0 = s0
    rest1, pose1, box1, cur1 = s1
    ok_rest = all((rest0[n] - rest1[n]).length < 1e-6 for n in rest0)
    ok_pose = all((pose0[n].translation - pose1[n].translation).length < 1e-6
                  for n in pose0)
    ok_box = (abs(box0[0] - box1[0]) < 1e-5 and abs(box0[1] - box1[1]) < 1e-5
              and box0[2] == box1[2])
    ok_cur = cur0 == cur1
    return ok_rest, ok_pose, ok_box, ok_cur


def main():
    from hs2_rig_kit import ops

    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    paths = []

    # 1) 插件默认角色（骨架 scale = 1）
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ops.STATE.clear()
    g = ops.get_assets(GAME)
    skel = g.skeleton()
    arm, names, body, _head = ops.build_default(g, scale=0.1)
    ops.STATE.update(names=names, skel=skel, scale=0.1)
    clip = g.load_clip(CLIP, fps=30.0)
    ops.apply_clip_data(arm, clip, CLIP, follow=False)
    bpy.context.scene.frame_set(5)

    out = os.path.join(tempfile.gettempdir(), "hs2_ue_export")
    os.makedirs(out, exist_ok=True)

    p0 = os.path.join(out, "ue_default.fbx")
    before = snapshot(arm, body)
    ops._export(arm, p0, include_mesh=True, deform_only=True)
    after = snapshot(arm, body)
    paths.append(("默认角色", p0, before, after))

    # 2) 工作室 FBX 角色（骨架 scale = 0.1）
    if args:
        fbx = args[0]
    else:
        fbx = None
        root = os.path.join(GAME, "Export")
        best = None
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".fbx"):
                    p = os.path.join(dirpath, f)
                    if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                        best = p
        fbx = best
    if fbx and os.path.isfile(fbx):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        ops.STATE.clear()
        arm2, _info = ops.import_character_fbx(fbx, GAME)
        body2 = bpy.data.objects.get(arm2.get("hs2_body"))
        clip = ops.get_assets(GAME).load_clip(CLIP, fps=30.0)
        ops.apply_clip_data(arm2, clip, CLIP, follow=False)
        bpy.context.scene.frame_set(5)
        p1 = os.path.join(out, "ue_fbxchar.fbx")
        before = snapshot(arm2, body2)
        ops._export(arm2, p1, include_mesh=True, deform_only=True)
        after = snapshot(arm2, body2)
        paths.append(("工作室FBX角色", p1, before, after))

    fails = 0
    for tag, path, before, after in paths:
        unit, props, hips = fbx_facts(path)
        arm_scale = props.get("Armature", {}).get("Lcl Scaling")
        root_scale = props.get("p_cf_anim", {}).get("Lcl Scaling")
        # 轴向被转成 Y-up 了，髋骨的位置分量落在 z 上，取绝对值最大的那个
        hips_y = max((abs(v) for v in hips), default=float("nan"))

        ok_unit = unit is not None and abs(unit - 1.0) < 1e-9
        ok_arm = arm_scale is None or all(abs(v - 1.0) < 1e-6 for v in arm_scale)
        ok_root = root_scale is None or all(abs(v - 1.0) < 1e-6 for v in root_scale)
        ok_cm = 80.0 < abs(hips_y) < 200.0
        ok_rest, ok_pose, ok_box, ok_cur = same(before, after)

        print("[test] %-14s 单位=%.3f 骨架缩放=%s 根骨缩放=%s 髋骨=%.2fcm" % (
            tag, unit if unit is not None else -1, arm_scale, root_scale, hips_y))
        for name, ok in (("文件单位 = 厘米", ok_unit),
                         ("骨架节点无缩放", ok_arm),
                         ("根骨无缩放", ok_root),
                         ("骨头数值是厘米", ok_cm),
                         ("rest 骨还原", ok_rest),
                         ("姿势还原", ok_pose),
                         ("网格还原", ok_box),
                         ("动作曲线还原", ok_cur)):
            print("   %-14s %s" % (name, "PASS" if ok else "FAIL"))
            if not ok:
                fails += 1

    print("\n[test] UE 导出单位：%s" % ("PASS" if fails == 0 else "FAIL(%d)" % fails))
    return fails


if __name__ == "__main__":
    main()
