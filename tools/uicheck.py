"""静态 + 运行时检查面板：图标名是否合法、操作符是否都注册了。

图标名写错会让 panel.draw() 抛异常 → 整块面板空白（之前踩过 CON_KIND 的坑）。
用法： blender.exe -b -P tools/uicheck.py
"""

import re
import os

import addon_utils
import bpy
from bpy.types import UILayout

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(HERE, "hs2_rig_kit")

# 没有界面时插件不会自动启用，先手动开一次
try:
    addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    print("[uicheck] 插件已启用")
except Exception as e:
    print("[uicheck] 启用失败：%s" % e)


def icon_enum():
    """UILayout 上所有带 icon 参数的 RNA 函数，取它们的枚举项。"""
    names = set()
    for fn in UILayout.bl_rna.functions:
        for p in fn.parameters:
            if p.identifier != "icon":
                continue
            try:
                items = p.enum_items
            except Exception:
                continue
            for k in items.keys():
                names.add(k)
    return names


def main():
    valid = icon_enum()
    print("[uicheck] 图标枚举 %d 个" % len(valid))

    bad = []
    total = 0
    for fn in sorted(os.listdir(PKG)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(PKG, fn), "r", encoding="utf-8") as f:
            src = f.read()
        for m in re.finditer(r'icon\s*=\s*"([A-Z0-9_]+)"', src):
            total += 1
            if valid and m.group(1) not in valid:
                bad.append((fn, m.group(1)))
    print("[uicheck] 代码里用了 %d 个图标" % total)
    if bad:
        for fn, ic in bad:
            print("[uicheck] X %s 图标不存在：%s" % (fn, ic))
    else:
        print("[uicheck] OK 图标全部合法")

    # 操作符是否都注册
    ids = set()
    for fn in sorted(os.listdir(PKG)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(PKG, fn), "r", encoding="utf-8") as f:
            for m in re.finditer(r'bl_idname\s*=\s*"(hs2\.[a-z_]+)"', f.read()):
                ids.add(m.group(1))
    missing = []
    for i in sorted(ids):
        mod, _, name = i.partition(".")
        if not hasattr(bpy.ops, mod) or not hasattr(getattr(bpy.ops, mod), name):
            missing.append(i)
    print("[uicheck] 操作符 %d 个，未注册 %s" % (len(ids), missing or "无"))

    # 面板是否都注册
    panels = []
    with open(os.path.join(PKG, "ui.py"), "r", encoding="utf-8") as f:
        for m in re.finditer(r'bl_idname\s*=\s*"(HS2_PT_[a-z]+)"', f.read()):
            panels.append(m.group(1))
    pmiss = [p for p in panels if not hasattr(bpy.types, p)]
    print("[uicheck] 面板 %d 个，未注册 %s" % (len(panels), pmiss or "无"))

    ok = not bad and not missing and not pmiss
    print("[uicheck] %s" % ("全部通过" if ok else "有问题"))
    return 0 if ok else 1


raise SystemExit(main())
