"""在 Blender 5.2 里加载「已安装」的插件（不是源码路径），复现面板绘制路径。

重点验证：
  1. bl_info 版本号对不对（防版本漂移）
  2. ops.clip_list / clip_index / current_clip_name 在各种状态下不抛异常
     （旧版 v2.4.1 的 `name 'scn' is not defined` 就在这里）
  3. 各 Panel 类被 ui.py 包住后能正常绘制（无 "HS2 面板出错"）
"""

import os
import traceback

import bpy

VMD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "小鹿乱撞.vmd")


def hdr(t):
    print("\n===== %s =====" % t)


def src_version():
    """源码里 bl_info 的 version，用来断言「装的和写的一样」。"""
    import ast

    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "hs2_rig_kit", "__init__.py")
    with open(p, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "bl_info":
            return tuple(ast.literal_eval(node.value)["version"])
    return None


def main():
    hdr("1. 启用已安装插件")
    import addon_utils

    mod = addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    ver = getattr(mod, "bl_info", {}).get("version")
    want = src_version()
    print("[P] bl_info version =", ver, " 源码 =", want)
    print("[P] 模块文件 =", mod.__file__)
    assert ver == want, "版本漂移！装的是 %s，源码是 %s" % (ver, want)

    from hs2_rig_kit import ops, ui

    hdr("2. 空场景调用面板数据函数")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # factory reset 会把插件禁用掉，必须重新启用
    mod = addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    print("[P] 重置后 version =", getattr(mod, "bl_info", {}).get("version"))
    from hs2_rig_kit import ops, ui  # 重新 import，确保拿到新模块

    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    bpy.context.preferences.addons["hs2_rig_kit"].preferences.game_dir = "D:/game/HS2"
    ctx = bpy.context
    for fn in ("clip_list", "clip_index", "current_clip_name"):
        try:
            r = getattr(ops, fn)(ctx)
            print("[P] OK  %s -> %s" % (fn, (len(r) if isinstance(r, (list, tuple)) else r)))
        except Exception as e:
            print("[P] FAIL %s: %s: %s" % (fn, type(e).__name__, e))
            traceback.print_exc()

    hdr("3. 载入动作后再调用")
    bpy.ops.hs2.add_rig(follow=False, fingers=False)
    bpy.ops.hs2.import_vmd(filepath=VMD)
    for fn in ("clip_list", "clip_index", "current_clip_name"):
        try:
            r = getattr(ops, fn)(ctx)
            print("[P] OK  %s -> %s" % (fn, (len(r) if isinstance(r, (list, tuple)) else r)))
        except Exception as e:
            print("[P] FAIL %s: %s: %s" % (fn, type(e).__name__, e))
            traceback.print_exc()

    hdr("4. 检查 ui.py 里的 Panel 是否都包了异常捕获")
    n_bad = 0
    for name in dir(ui):
        cls = getattr(ui, name)
        if isinstance(cls, type) and issubclass(cls, bpy.types.Panel) and cls is not bpy.types.Panel:
            src = None
            try:
                import inspect

                src = inspect.getsource(cls.draw) if hasattr(cls, "draw") else ""
            except Exception:
                src = ""
            guarded = "_safe_draw" in src or "面板出错" in src or "try:" in src
            print("[P] %-28s guarded=%s" % (name, guarded))
            if not guarded:
                n_bad += 1
    print("[P] 未加保护的 Panel 数量 =", n_bad)

    hdr("5. 手动触发一次真实重绘（区域 tag_redraw 后再取依赖图）")
    for area in bpy.context.screen.areas if bpy.context.screen else []:
        if area.type == "VIEW_3D":
            area.tag_redraw()
    bpy.context.view_layer.update()
    print("[P] 完成")

    hdr("结论")
    print("[P] 全部检查执行完毕（上面若有 FAIL / 面板出错即为问题）")


main()
