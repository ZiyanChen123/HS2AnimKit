"""体型（身高/胸围）功能自检。

    blender -b --factory-startup -P tools/body_check.py

检查项：
  1. split_variants 正确拆出三套命名（身高前缀 / 身高后缀 / 胸围后缀）
  2. group_stages 把 S/M/L 归成一个「身高」组、胸围 5 档归成一个「胸围」组
  3. pick_variant 在 0/0.5/1 处给出正确权重（单变体 / 两端 / 中间插值）
  4. clips.blend 位置线性、四元数 slerp
  5. 端到端：选 S 档 ↔ L 档解出不同姿态，且 cf_N_height 缩放到 0.8 ↔ 1.0
  6. 胸围阶段最小 ↔ 最大也是不同的姿态
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hs2_rig_kit import api  # noqa: E402
from hs2_rig_kit.ripper import hdb, clips  # noqa: E402

GAME = os.environ.get("HS2_GAME_DIR", "D:/game/HS2")

FAILS = []


def check(label, cond, extra=""):
    print("%s %-46s %s" % ("[OK]  " if cond else "[FAIL]", label, extra))
    if not cond:
        FAILS.append(label)


def track_diff(a, b):
    """两条 ClipData 里同一骨骼位置曲线的最大差距（没有共同骨就 0）。"""
    worst = 0.0
    keys = set(a.tracks) & set(b.tracks)
    for p in keys:
        va = (a.tracks[p] or {}).get("pos")
        vb = (b.tracks[p] or {}).get("pos")
        if not va or not vb:
            continue
        n = min(len(va), len(vb))
        for i in range(n):
            for k in range(3):
                d = abs(va[i][k] - vb[i][k])
                if d > worst:
                    worst = d
    return worst


def pure_logic():
    print("== 纯逻辑：命名拆分 / 分组 / 选变体 / 混合 ==")
    cases = [
        ("S_Idle", "S", "", "height"),
        ("M_WLoop1", "M", "", "height"),
        ("L_D_Drop", "L", "", "height"),
        ("chair_17_S", "S", "", "height"),
        ("chair_17_S_loop", "S", "", "height"),
        ("WBreastSM", "", "SM", "bust"),
        ("S1BreastL", "", "L", "bust"),
        ("D_Idle_BreastL", "", "L", "bust"),
    ]
    for name, h, b, _kind in cases:
        v = hdb.split_variants(name)
        check("拆 %s" % name, v["height"] == h and v["bust"] == b,
              "height=%r bust=%r" % (v["height"], v["bust"]))

    # 造一段带 S/M/L 的控制器片段
    items = [{"name": "S_WLoop1", "pid": 1, "bundle": "x"},
             {"name": "M_WLoop1", "pid": 2, "bundle": "x"},
             {"name": "L_WLoop1", "pid": 3, "bundle": "x"},
             {"name": "S_Idle", "pid": 4, "bundle": "x"},
             {"name": "M_Idle", "pid": 5, "bundle": "x"},
             {"name": "L_Idle", "pid": 6, "bundle": "x"}]
    gs = hdb.group_stages(items)
    check("S/M/L 归成 2 组", len(gs) == 2, "groups=%d" % len(gs))
    hg = next((g for g in gs if g["kind"] == "height"), None)
    check("WLoop1 是身高组", hg is not None and len(hg["variants"]) == 3,
          "variants=%s" % (sorted(hg["variants"]) if hg else None))
    if hg:
        check("身高组 S/M/L 全", sorted(hg["variants"]) == ["L", "M", "S"],
              str(sorted(hg["variants"])))

        p0 = hdb.pick_variant(hg, 0.0)
        p1 = hdb.pick_variant(hg, 1.0)
        pm = hdb.pick_variant(hg, 0.5)
        check("t=0 → S", p0 == [(hg["variants"]["S"], 1.0)], str(p0))
        check("t=1 → L", p1 == [(hg["variants"]["L"], 1.0)], str(p1))
        check("t=0.5 → M", len(pm) == 1 and pm[0][0]["name"] == "M_WLoop1",
              str(pm))

    # 胸围 5 档
    bitems = [{"name": "WBreast%s" % b, "pid": i, "bundle": "x"}
              for i, b in enumerate(("S", "SM", "M", "ML", "L"))]
    bg = hdb.group_stages(bitems)
    check("胸围 5 档归成 1 组", len(bg) == 1 and bg[0]["kind"] == "bust"
          and len(bg[0]["variants"]) == 5,
          "groups=%d kind=%s n=%d" % (
              len(bg), bg[0]["kind"], len(bg[0]["variants"])))
    if bg:
        p75 = hdb.pick_variant(bg[0], 0.75)
        check("胸围 t=0.75 正好是 ML", len(p75) == 1 and p75[0][0]["name"].endswith("ML"),
              str([it["name"] for it, _w in p75]))
        p875 = hdb.pick_variant(bg[0], 0.875)
        check("胸围 t=0.875 是 ML~L 之间", len(p875) == 2
              and p875[0][0]["name"].endswith("ML") and p875[1][0]["name"].endswith("L"),
              str([it["name"] for it, _w in p875]))

    # clips.blend：位置线性、旋转 slerp
    a = clips.ClipData("a", 30.0, 1.0, {"bone": {
        "pos": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        "rot": [[1.0, 0.0, 0.0, 0.0]]}}, {})
    b = clips.ClipData("b", 30.0, 1.0, {"bone": {
        "pos": [[2.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
        "rot": [[0.0, 1.0, 0.0, 0.0]]}}, {})
    m = clips.blend(a, b, 0.25)
    pos = m.tracks["bone"]["pos"][0]
    rot = m.tracks["bone"]["rot"][0]
    check("blend 位置线性", abs(pos[0] - 0.5) < 1e-4, "x=%.4f" % pos[0])
    # 25% 处：绕 Y 轴 180° 旋转插到 45°，q=(cos22.5°, sin22.5°, 0, 0)
    import math as _m
    check("blend 旋转 slerp", abs(rot[0] - _m.cos(_m.radians(22.5))) < 1e-3
          and abs(rot[1] - _m.sin(_m.radians(22.5))) < 1e-3,
          "q=(%.3f, %.3f, %.3f, %.3f)" % tuple(rot))


def world_pose(arm, bones=("cf_J_Hips", "cf_J_Head", "cf_J_Hand_L", "cf_J_Hand_R")):
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = arm.evaluated_get(dg)
    out = {}
    for n in bones:
        if n in ev.pose.bones:
            out[n] = tuple(round(v, 4) for v in ev.pose.bones[n].head)
    return out


def end_to_end():
    print("\n== 端到端：身高档位选单档 + 缩放身高骨骼 ==")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    h = api.h_data()
    solo = [m for m in h["modes"] if len(hdb.cast_of(m.get("roles") or {})) == 1
            and m.get("cast_gender", {}).get("female") == 1]
    check("找到单人女模式", bool(solo), "%d 个" % len(solo))
    if not solo:
        return
    # 找一个带身高变体的模式（stage_groups 里有 height 组）
    mode = None
    hgroup = None
    for m in solo:
        slot = hdb.cast_of(m.get("roles") or {})[0]
        gs = api.stage_groups(m, slot)
        for g in gs:
            if g["kind"] == "height" and len(g["variants"]) >= 2:
                mode, hgroup = m, g
                break
        if mode:
            break
    check("找到带身高变体的动作", mode is not None,
          mode["label"] if mode else "")
    if mode is None:
        return
    slot = hdb.cast_of(mode.get("roles") or {})[0]
    gs = api.stage_groups(mode, slot)
    n_height = sum(1 for g in gs if g["kind"] == "height")
    n_bust = sum(1 for g in gs if g["kind"] == "bust")
    check("阶段分组", len(gs) > 0 and n_height > 0,
          "groups=%d height=%d bust=%d" % (len(gs), n_height, n_bust))

    # 档位字母选单档：S vs L 两条 clip 应有差异（不混合）
    slo = api._pick_group_clip(hgroup, "S")
    shi = api._pick_group_clip(hgroup, "L")
    d = track_diff(slo, shi)
    check("S 档 ↔ L 档 解出不同姿态", d > 1e-3, "max 位移 %.4f" % d)

    # 真正装到角色上：选 S 档会缩放 cf_N_height 到 0.8，L 档到 1.0
    char = api.character("female")
    api.apply_h(mode, hgroup["key"], target=char, with_props=False, height="S")
    s_s = tuple(round(v, 4) for v in char.pose.bones["cf_N_height"].scale)
    bpy.context.scene.frame_set(bpy.context.scene.frame_start + 2)
    pose_lo = world_pose(char)
    api.apply_h(mode, hgroup["key"], target=char, with_props=False, height="L")
    l_s = tuple(round(v, 4) for v in char.pose.bones["cf_N_height"].scale)
    bpy.context.scene.frame_set(bpy.context.scene.frame_start + 2)
    pose_hi = world_pose(char)
    check("选 S 档缩身高骨骼到 0.8", s_s == (0.8, 0.8, 0.8), "scale=%s" % (s_s,))
    check("选 L 档身高骨骼 = 1.0", l_s == (1.0, 1.0, 1.0), "scale=%s" % (l_s,))
    dpose = max(
        max(abs(pose_lo[n][k] - pose_hi[n][k]) for k in range(3))
        for n in pose_lo if n in pose_hi)
    check("S 档 ↔ L 档 装到角色后姿态不同", dpose > 1e-4, "世界位移 %.4f" % dpose)

    # 胸围阶段：如果有，bust 5 档里最小 vs 最大也应不同
    bust_group = next((g for g in gs if g["kind"] == "bust"), None)
    if bust_group:
        blo = api._pick_group_clip(bust_group, "S")
        bhi = api._pick_group_clip(bust_group, "L")
        db = track_diff(blo, bhi)
        check("胸围最小 ↔ 最大 解出不同姿态", db > 1e-3, "max 位移 %.4f" % db)
    else:
        print("[--]  胸围阶段                                       这一段没有")


def main():
    pure_logic()
    end_to_end()
    print()
    print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)) if FAILS else "全部通过")


if __name__ == "__main__":
    main()
