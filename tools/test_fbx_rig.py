"""工作室 FBX 导入 + 套动作的自动验收测试。

在 Blender 后台跑：

    blender -b --factory-startup --python tools/test_fbx_rig.py -- <fbx 路径>

检查项：

1. 骨架/蒙皮对齐：每根变形骨到它自己权重顶点的距离
2. 对齐后的姿势：手臂骨和手臂网格的 z 一致
3. 套动作后的网格形变：边长伸缩比（默认角色当参照）
"""

import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

GAME = os.environ.get("HS2_GAME_DIR", r"D:\game\HS2")
CLIPS = ["mc_f_move_07_L", "action_44", "adv_action_05_01", "sleep_09",
         "f_custom_pose_03", "action_56_R_loop"]


def newest_fbx():
    root = os.path.join(GAME, "Export")
    best = None
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".fbx"):
                p = os.path.join(dirpath, f)
                if best is None or os.path.getmtime(p) > os.path.getmtime(best):
                    best = p
    return best


def bone_mesh_error(arm, body):
    """每根变形骨的头到它权重顶点中心的距离（越小越贴合）。"""
    names = {g.index: g.name for g in body.vertex_groups}
    acc = {}
    step = max(1, len(body.data.vertices) // 6000)
    for v in list(body.data.vertices)[::step]:
        best = None
        for ge in v.groups:
            if best is None or ge.weight > best[0]:
                best = (ge.weight, ge.group)
        if best is not None and best[0] > 0.5:
            acc.setdefault(names.get(best[1]), []).append(
                body.matrix_world @ v.co)
    ds = []
    for n, cos in acc.items():
        b = arm.data.bones.get(n)
        if b is None or len(cos) < 20:
            continue
        c = sum(cos, Vector()) / len(cos)
        ds.append(((arm.matrix_world @ b.head_local) - c).length)
    ds.sort()
    return (ds[len(ds) // 2] if ds else 0.0, ds[-1] if ds else 0.0, len(ds))


def stretch(body, frames):
    """逐帧边长伸缩比（相对静止网格）。返回所有帧合起来的 (p99, max, >1.5 比例)。"""
    rest = [v.co.copy() for v in body.data.vertices]
    edges = [(e.vertices[0], e.vertices[1]) for e in body.data.edges]
    rl = [(rest[a] - rest[b]).length for a, b in edges]
    keep = [i for i, l in enumerate(rl) if l > 0.002]
    scn = bpy.context.scene
    allr = []
    for f in frames:
        scn.frame_set(min(f, int(round(body.animation_data.action.frame_range[1]))
                          if body.animation_data and body.animation_data.action
                          else f))
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ob = body.evaluated_get(dg)
        me = ob.to_mesh()
        for i in keep:
            a, b = edges[i]
            allr.append((me.vertices[a].co - me.vertices[b].co).length / rl[i])
        ob.to_mesh_clear()
    allr.sort()
    n = len(allr)
    bad = sum(1 for r in allr if r > 1.5)
    return allr[int(n * 0.99)], allr[-1], bad / float(n or 1)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    path = argv[0] if argv else newest_fbx()
    print("[test] FBX =", path)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    from hs2_rig_kit import ops
    g = ops.get_assets(GAME)

    arm, info = ops.import_character_fbx(path, GAME)
    body = bpy.data.objects[arm["hs2_body"]]
    print("[test] 导入：%s" % info)
    med, worst, n = bone_mesh_error(arm, body)
    print("[test] 骨头-蒙皮距离：中位 %.4f m，最差 %.4f m（%d 根）" % (med, worst, n))

    # 骨数必须等于 FBX 里的 LimbNode 数（叶子骨、_s 副骨、手指末节都不能少），
    # 顶点组必须都能找到同名骨，否则那段蒙皮不会跟着动。
    from hs2_rig_kit import fbxrig
    bind = fbxrig.read_bindpose(path)
    bones = {b.name for b in arm.data.bones}
    groups = {g.name for g in body.vertex_groups}
    missing_groups = sorted(groups - bones)
    print("[test] BindPose 节点 %d，骨架骨数 %d，顶点组 %d" % (
        len(bind), len(bones), len(groups)))
    print("[test] 顶点组里找不到骨的: %s" % (missing_groups or "无"))
    check_ok = not missing_groups and len(bones) >= 280
    print("[test] 骨骼完整：%s" % ("PASS" if check_ok else "FAIL"))

    for side in ("L", "R"):
        pb = arm.pose.bones.get("cf_J_Hand_%s" % side)
        if pb is not None:
            h = arm.matrix_world @ pb.head
            print("[test] 对齐后 Hand_%s = (%.3f, %.3f, %.3f)" % (side, h.x, h.y, h.z))

    ok = med < 0.06
    print("[test] 骨架贴合：%s" % ("PASS" if ok else "FAIL"))

    # 参照：先跑插件默认角色（同一批动作），再换 FBX 角色
    def run_pass(build, tag):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        ops.STATE.clear()
        gg = ops.get_assets(GAME)
        rig_obj, bd = build(gg)
        out = {}
        for name in CLIPS:
            try:
                clip = gg.load_clip(name, fps=30.0)
            except Exception as e:
                print("%-22s 跳过：%s" % (name, e))
                continue
            frames = [0, clip.frame_count // 3, 2 * clip.frame_count // 3,
                      clip.frame_count - 1]
            if "names" in ops.STATE:
                ops.STATE["skel"] = gg.skeleton()
            ops.apply_clip_data(rig_obj, clip, name, follow=False)
            out[name] = stretch(bd, frames)
        return out

    def build_default(gg):
        skel = gg.skeleton()
        a, n, b, _h = ops.build_default(gg, scale=0.1)
        ops.STATE.update(names=n, skel=skel, scale=0.1)
        return a, b

    def build_fbx(gg):
        a, _info = ops.import_character_fbx(path, GAME)
        return a, bpy.data.objects[a["hs2_body"]]

    print("\n[test] 生成参照数据（插件默认角色）…")
    ref = run_pass(build_default, "default")
    print("[test] 生成测试数据（FBX 角色）…")
    got = run_pass(build_fbx, "fbx")

    print("\n%-22s %-24s %-24s" % ("clip", "FBX 角色 p99/max/>1.5%",
                                   "默认角色 p99/max/>1.5%"))
    fails = 0
    for name in CLIPS:
        if name not in got:
            continue
        a = got[name]
        b = ref.get(name, (0, 0, 0))
        bad = a[2] >= 0.01 or a[0] > 1.6
        if bad:
            fails += 1
        print("%-22s %5.2f / %6.2f / %5.2f%%   %5.2f / %6.2f / %5.2f%%%s"
              % (name, a[0], a[1], a[2] * 100, b[0], b[1], b[2] * 100,
                 "  <-- 有拉伸" if bad else ""))
    print("\n[test] 动作形变：%s" % ("PASS" if fails == 0 else "FAIL(%d)" % fails))


main()
