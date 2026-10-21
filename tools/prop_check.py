"""道具导入自检：建道具 → 量尺寸 → 挂到角色骨 → 写道具动画 → 检查跟随。

    blender -b --factory-startup -P tools/prop_check.py
"""
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hs2_rig_kit import ops, props  # noqa: E402
from hs2_rig_kit.ripper import GameAssets, hdb  # noqa: E402

GAME = os.environ.get("HS2_GAME_DIR", r"D:\game\HS2")


def bbox(objs):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for ob in objs:
        if ob.type != "MESH":
            continue
        for v in ob.bound_box:
            w = ob.matrix_world @ Vector(v)
            for i in range(3):
                lo[i] = min(lo[i], w[i])
                hi[i] = max(hi[i], w[i])
    return lo, hi


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ga = GameAssets(GAME)

    # 1) 角色
    arm, names, body, head = ops.build_default(ga, include_head=True)
    print("[角色] 骨 %d, 身体顶点 %s" % (len(arm.data.bones),
                                        len(body.data.vertices) if body else "-"))
    lo, hi = bbox([o for o in bpy.data.objects if o.type == "MESH"])
    print("[角色] 包围盒 Z %.3f .. %.3f (身高 %.3f m)" % (lo.z, hi.z, hi.z - lo.z))

    # 2) 道具
    t = props.build_prop(ga, "01", "p_item_dildo")
    print("[道具] %s 骨 %d 网格 %d" % (t.name, len(t.data.bones), t.get("hs2_prop_meshes")))
    meshes = [o for o in bpy.data.objects if o.get("hs2_prop") == "p_item_dildo"]
    lo2, hi2 = bbox(meshes)
    print("[道具] 包围盒 X %.3f..%.3f Y %.3f..%.3f Z %.3f..%.3f" %
          (lo2.x, hi2.x, lo2.y, hi2.y, lo2.z, hi2.z))
    size = (hi2 - lo2)
    print("[道具] 尺寸 %.3f x %.3f x %.3f m" % (size.x, size.y, size.z))

    # 3) 挂到右手
    ok = props.attach_to_bone(t, arm, "k_f_handR_00")
    print("[挂载] k_f_handR_00 ->", ok, "world=", [round(v, 3) for v in t.matrix_world.translation])
    hand = arm.matrix_world @ arm.pose.bones["k_f_handR_00"].matrix
    print("[挂载] 手骨位置 =", [round(v, 3) for v in hand.translation])

    # 4) 道具动画
    data = ga.h_data()
    mode = None
    for m in data.get("modes") or []:
        if m["type"] == "tokushu" and m["id"] == 2:
            mode = m
            break
    print("[数据] 模式:", (mode or {}).get("label"), (mode or {}).get("props"))
    ctrl_key = hdb.item_ctrl(mode or {})
    print("[数据] 道具控制器:", ctrl_key)
    if ctrl_key:
        ctrl = data["ctrls"][ctrl_key]
        stages = hdb.stage_list(ctrl)
        stage = ([x for x in stages if x["stage"] == "WLoop2"] or stages)[0]
        clip = props.load_clip(ga, t, ctrl["bundle"], stage["pid"])
        clip.name = stage["name"]
        act, info = props.apply_clip(t, clip)
        print("[动画] %s %d 帧 %d 骨" % (act.name, info["frames"], info["bone_tracks"]))
        # 逐帧看道具会不会动
        scn = bpy.context.scene
        scn.frame_set(1)
        last = t.pose.bones[len(t.pose.bones) - 1]
        p1 = (t.matrix_world @ last.matrix).translation.copy()
        scn.frame_set(max(1, info["frames"] // 2))
        p2 = (t.matrix_world @ last.matrix).translation.copy()
        print("[动画] 首帧 %s 中帧 %s 位移 %.4f" %
              ([round(v, 3) for v in p1], [round(v, 3) for v in p2], (p2 - p1).length))

    # 5) 换个姿势看是否跟随
    pb = arm.pose.bones.get("k_f_handR_00")
    if pb:
        pb.location = (0.0, 0.0, 0.2)
        bpy.context.view_layer.update()
        print("[跟随] 手骨移动后道具世界位置 =",
              [round(v, 3) for v in t.matrix_world.translation])

    print("OK")


if __name__ == "__main__":
    main()
