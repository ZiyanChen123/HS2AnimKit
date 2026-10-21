"""API 层自检：

    blender -b --factory-startup -P tools/api_check.py

覆盖：加载角色 → 搜动作 → 装 Studio 动作 → 装 H 动作（带道具）→
镜像 → 贴地 → 变速 → 导出 → 文本命令。
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from hs2_rig_kit import api, animlib  # noqa: E402

FAILS = []


def check(label, cond, extra=""):
    print("%s %-42s %s" % ("[OK]  " if cond else "[FAIL]", label, extra))
    if not cond:
        FAILS.append(label)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    check("游戏目录", bool(api.game_dir()), api.game_dir())

    a = api.character()
    check("加载角色", a is not None and len(a.data.bones) > 600,
          "%d 骨 · 槽 %s" % (len(a.data.bones), a.get("hs2_slot")))
    b = api.character("male")
    check("加男角", b is not None and b.get("hs2_slot") == "m1",
          "%s · 槽 %s" % (b.name, b.get("hs2_slot")))

    cats = api.categories()
    check("分类", len(cats) >= 5, "%d 个大类：%s" % (len(cats),
                                              ", ".join(sorted(cats)[:8])))

    res = api.search("chair")
    check("搜索 chair", len(res) > 5, "%d 条" % len(res))
    res_h = api.search("正常位", kind="h")
    check("搜索 H", len(res_h) >= 1, str(res_h[0]["name"] if res_h else ""))

    act, info = api.load("chair_17_M")
    check("装 Studio 动作", info["frames"] > 10,
          "%s %d 帧" % (act.name, info["frames"]))

    out = api.load_h("正常位", "M_WLoop1")
    check("装 H 动作", bool(out["actions"]),
          "%s %s" % (out["mode"]["label"],
                     out.get("stage", {}).get("cn", "?")))
    st = api.stages(api.find_mode("骑乗位") or api.find_mode("正常位"), "f1")
    check("H 阶段列表", len(st) > 10, "%d 段" % len(st))

    # 带道具的 H 动作
    mode = None
    for m in (api.h_data().get("modes") or []):
        if m.get("props") and m.get("cast_gender", {}).get("female"):
            mode = m
            break
    if mode:
        r = api.load_h(mode, None)
        check("H 道具加载", len(r["props"]) > 0,
              "%s → %d 个道具" % (mode["label"], len(r["props"])))
    else:
        check("H 道具加载", False, "目录里没找到带道具的模式")

    # 动作编辑
    a1 = act

    mir = api.mirror(a1)
    check("镜像", mir is not None and mir.name.endswith("_mirror"), mir.name)

    rt = api.retime(a1, factor=2.0)
    lo, hi = animlib.action_range(rt)
    check("变速", (hi - lo + 1) > info["frames"], "%d 帧" % (hi - lo + 1))

    g = api.ground(a1)
    check("贴地", g is not None, g.name)

    # 文本命令
    print("-" * 60)
    print(api.cmd("info"))
    print(api.cmd("list chair"))
    print(api.cmd("hlist 拘束"))
    print("-" * 60)
    check("cmd: list", "chair" in api.cmd("list chair"))
    check("cmd: info", "角色" in api.cmd("info"))

    # 导出
    out_path = os.path.join(bpy.app.tempdir or ".", "hs2_api_test.fbx")
    api.export_fbx(out_path)
    check("导出 FBX", os.path.isfile(out_path),
          "%.1f KB" % (os.path.getsize(out_path) / 1024.0)
          if os.path.isfile(out_path) else "没生成")

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)))
    else:
        print("全部通过")


if __name__ == "__main__":
    main()
