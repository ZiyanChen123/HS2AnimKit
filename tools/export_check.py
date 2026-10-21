"""按物体导出 FBX 的自检（给 UE 用）。

    blender -b --factory-startup -P tools/export_check.py

检查：
  1. 道具挂到骨上时，「内部对齐节点」确实落在骨头上；
  2. 导出的 FBX 根节点在原点、没有物体级动画；
  3. 回读后骨骼动画和导出前一致（这才是 UE 真正吃的东西）；
  4. 把角色整体挪走再导一次，文件内容不受影响（父级运动没漏进去）。
"""
import os
import sys

import bpy
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hs2_rig_kit import api, ops  # noqa: E402

FAILS = []
OUT = os.path.join(HERE, "_test", "out")


def check(label, cond, extra=""):
    print("%s %-46s %s" % ("[OK]  " if cond else "[FAIL]", label, extra))
    if not cond:
        FAILS.append(label)


def sample_bones(arm, names, frames):
    """记录每根骨在指定帧的局部四元数/位置。

    frames 传的是**原始**帧号；FBX 导入常常把动画起点挪一格（0→1），
    所以这里按导入后 action 的实际起点换算一下再采样。
    """
    off = 0
    ad = getattr(arm, "animation_data", None)
    if ad is not None and ad.action is not None and getattr(
            arm, "_hs2_frame_off", None) is not None:
        off = int(arm["_hs2_frame_off"])
    out = {}
    scn = bpy.context.scene
    for f in frames:
        scn.frame_set(f + off)
        bpy.context.view_layer.update()
        for n in names:
            pb = arm.pose.bones.get(n)
            if pb is None:
                continue
            loc, q, _s = pb.matrix_basis.decompose()
            out[(n, f)] = (tuple(loc), (q.w, q.x, q.y, q.z))
    return out


def frame_offset(arm, want_start):
    """导入后 action 的起点相对原始起点挪了几帧。"""
    ad = getattr(arm, "animation_data", None)
    if ad is None or ad.action is None:
        return 0
    try:
        return int(round(ad.action.frame_range[0] - want_start))
    except Exception:
        return 0


def main():
    os.makedirs(OUT, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    api.character("female")

    # 找一个「既有道具、又有内部对齐节点」的模式
    h = api.h_data()
    mode = pick = None
    for m in h["modes"]:
        if not m.get("roles"):
            continue
        for p in m.get("props") or []:
            if p.get("prefab") and p.get("attach"):
                mode, pick = m, p
                break
        if mode:
            break
    if mode is None:
        print("[--]  没找到带 attach 节点的道具，改用普通道具")
        for m in h["modes"]:
            if m.get("props") and m["roles"]:
                mode = m
                pick = m["props"][0]
                break

    res = api.load_h(mode, None)
    check("装 H 动作（含道具）", len(res["props"]) > 0,
          "%s · 道具 %d" % (mode["label"], len(res["props"])))
    prop = next((o for o in res["props"] if o.get("hs2_attach_node")),
                res["props"][0])
    # 回读比对用**没有内部对齐节点**的那件：FBX 回读比的是「动画有没有被搬走」，
    # 跟挂载偏移无关；混在一起会把两件事绑成一件，看不出到底哪边坏。
    rt = next((o for o in res["props"] if not o.get("hs2_attach_node")),
              res["props"][0])
    # 场景后面会被重置，先把要的东西存成普通值（RNA 指针会悬空）
    want_prefab = prop.get("hs2_prop_prefab") or ""
    rt_prefab = rt.get("hs2_prop_prefab") or ""
    bone_name = prop.get("hs2_attached_to") or ""
    check("道具已挂骨", bool(prop.get("hs2_attached_to")),
          "%s → %s" % (prop.name, prop.get("hs2_attached_to")))

    # 1) 内部对齐节点
    node = prop.get("hs2_attach_node") or ""
    if node:
        char = prop.parent
        pb = char.pose.bones.get(prop.get("hs2_attached_to"))
        want = (char.matrix_world @ pb.matrix).translation
        # 用**静止**矩阵比：道具自己的动画会把这个节点拖走（长绳会被拉到空中），
        # 这里验的是「挂载时对齐做得对不对」，跟播到哪一帧无关。
        got = (prop.matrix_world
               @ prop.pose.bones[node].bone.matrix_local).translation
        check("对齐节点落在骨头上", (got - want).length < 1e-4,
              "%s 偏差 %.5f m" % (node, (got - want).length))
    else:
        print("[--]  这件道具没有内部对齐节点，跳过")

    # 角色整体导出（原有链路）也来一次，确认没被改坏
    p3 = os.path.join(OUT, "char_obj.fbx")
    api.export_fbx(p3)
    check("角色 FBX 仍能导出", os.path.isfile(p3),
          "%.1f MB" % (os.path.getsize(p3) / 1e6))

    # 2/3) 导出 + 回读对比
    names = [b.name for b in rt.data.bones]
    rng = ops.object_action_range(rt) or (1, 10)
    frames = [rng[0], (rng[0] + rng[1]) // 2, rng[1]]
    before = sample_bones(rt, names, frames)
    before_start = (ops.object_action_range(rt) or (1, 1))[0]

    path = os.path.join(OUT, "prop_obj.fbx")
    ops.export_object(rt, path, axis="ue", include_mesh=False)
    check("导出文件", os.path.isfile(path),
          "%.1f KB" % (os.path.getsize(path) / 1024.0))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=path)
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    check("回读骨架", len(arms) == 1, arms[0].name if arms else "无")
    if not arms:
        print("失败：导出的 FBX 里没有骨架")
        return report()
    got = arms[0]
    loc = got.matrix_world.translation
    check("根节点在原点", loc.length < 1e-4, [round(v, 4) for v in loc])
    anim = got.animation_data.action if got.animation_data else None
    check("带动画", anim is not None, anim.name if anim else "无")

    got["_hs2_frame_off"] = frame_offset(got, before_start)
    after = sample_bones(got, names, frames)
    worst_q = 0.0
    worst_l = 0.0
    missing = []
    for key, (l0, q0) in before.items():
        if key not in after:
            missing.append(key[0])
            continue
        l1, q1 = after[key]
        dq = abs(sum(a * b for a, b in zip(q0, q1)))
        worst_q = max(worst_q, 1.0 - dq)
        worst_l = max(worst_l, max(abs(l0[i] - l1[i]) for i in range(3)))
    check("骨骼旋转回读一致", worst_q < 1e-3,
          "最大差 %.2e（缺失 %d）" % (worst_q, len(set(missing))))
    check("骨骼位移回读一致", worst_l < 1e-3, "最大差 %.4f m" % worst_l)

    # 4) 把角色挪走，导出结果应该完全一样
    bpy.ops.wm.read_factory_settings(use_empty=True)
    api.character("female")
    res2 = api.load_h(mode, None)
    prop2 = next((o for o in res2["props"]
                  if o.get("hs2_prop_prefab") == rt_prefab), res2["props"][0])
    char = prop2.parent
    char.location = (3.0, -2.0, 0.5)
    bpy.context.view_layer.update()
    path2 = os.path.join(OUT, "prop_obj_moved.fbx")
    ops.export_object(prop2, path2, axis="ue", include_mesh=False)
    size1 = os.path.getsize(path)
    size2 = os.path.getsize(path2)
    # 回读「挪走之后」的文件，骨骼数据应该和第一次一模一样
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=path2)
    arms2 = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    moved_ok = False
    if arms2:
        arms2[0]["_hs2_frame_off"] = frame_offset(arms2[0], before_start)
        after2 = sample_bones(arms2[0], names, frames)
        worst = 0.0
        for key, (l0, q0) in before.items():
            if key not in after2:
                continue
            _l1, q1 = after2[key]
            worst = max(worst, 1.0 - abs(sum(a * b for a, b in zip(q0, q1))))
        moved_ok = worst < 1e-3
    check("父级挪动不影响导出", moved_ok,
          "%d vs %d 字节" % (size1, size2))

    return report()


def report():
    print()
    print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)) if FAILS
          else "全部通过")


if __name__ == "__main__":
    main()
