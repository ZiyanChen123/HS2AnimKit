"""H 场景整段加载自检：女角 + 男角 + 一堆道具，检查挂点是否合理。

    blender -b --factory-startup -P tools/hscene_check.py
"""
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hs2_rig_kit import api  # noqa: E402

FAILS = []


def check(label, cond, extra=""):
    print("%s %-44s %s" % ("[OK]  " if cond else "[FAIL]", label, extra))
    if not cond:
        FAILS.append(label)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    fem = api.character("female")
    male = api.character("male")
    check("两个角色", fem is not None and male is not None,
          "%s + %s" % (fem.name, male.name))

    h = api.h_data()
    rich = [m for m in h["modes"]
            if len([p for p in (m.get("props") or []) if p.get("prefab")]) >= 3
            and m.get("cast_gender", {}).get("female")]
    check("找到多道具模式", bool(rich), rich[0]["label"] if rich else "")
    mode = rich[0]

    res = api.load_h(mode, None)
    check("装女+男动作", len(res["actions"]) >= 1,
          ",".join(res["actions"]))
    check("道具数量", len(res["props"]) >= 3, "%d 个" % len(res["props"]))

    # 挂点检查：道具该对准的地方 vs 目标骨位置
    #
    # 注意：挂点分两种，不能一律拿「道具根」去比。**带「くっつけるもの」的那组**
    # 是让道具内部的某个节点（attach）落在骨头上，道具根本身是偏开的 ——
    # 这时要验的是那个节点，不是根。
    bad = []
    checked_attach = 0
    for ob in res["props"]:
        bone = ob.get("hs2_attached_to") or ""
        parent = ob.parent
        if parent is None or not bone:
            bad.append((ob.name, "没挂上"))
            continue
        pb = parent.pose.bones.get(bone)
        if pb is None:
            bad.append((ob.name, "骨骼 %s 不存在" % bone))
            continue
        want = parent.matrix_world @ pb.matrix
        node = ob.get("hs2_attach_node") or ""
        if node:
            pbn = ob.pose.bones.get(node)
            if pbn is None:
                bad.append((ob.name, "找不到内部节点 %s" % node))
                continue
            # 用**静止**矩阵比：动画本来就会把这个节点拖走（长绳会被拉到空中），
            # 要验的是「对齐做得对不对」，跟正在播哪一帧无关。
            got = ob.matrix_world @ pbn.bone.matrix_local
            checked_attach += 1
        else:
            got = ob.matrix_world
        d = (got.translation - want.translation).length
        if d > 0.05:
            bad.append((ob.name, "偏移 %.3f m%s" % (d, "（节点 %s）" % node
                                                  if node else "")))
    check("道具都在挂点上", not bad,
          "%s 件用内部节点对齐" % checked_attach if not bad else str(bad[:3]))

    # 所有道具的包围盒应该靠近角色
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for ob in res["props"]:
        for c in ob.bound_box:
            w = ob.matrix_world @ Vector(c)
            for i in range(3):
                lo[i] = min(lo[i], w[i])
                hi[i] = max(hi[i], w[i])
    check("道具在角色附近", lo.z > -2.0 and hi.z < 6.0,
          "z %.2f..%.2f" % (lo.z, hi.z))

    # 男角也有动作
    act = res["actions"].get("m1")
    if act is not None:
        check("男角动作", bool(act.name), act.name)
    else:
        print("[--]   男角动作                                      这一段没有")

    # 换一段带道具的、再换成不带道具的
    plain = [m for m in h["modes"] if not m.get("props") and m.get("roles")]
    if plain:
        r2 = api.load_h(plain[0], None, with_props=False)
        check("无道具模式", bool(r2["actions"]), plain[0]["label"])

    print()
    print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)) if FAILS
          else "全部通过")


if __name__ == "__main__":
    main()
