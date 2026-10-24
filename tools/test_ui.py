"""侧栏面板自检：不打开界面也能把每个面板画一遍。

    blender -b --factory-startup --python tools/test_ui.py

做法是用一个假的 layout 把面板的 draw 跑完，记录里面出现的操作符、文字、图标，
然后检查：

1. 每种状态下都能画完（不抛异常，也不出现「面板出错：…」）
2. 用到的操作符真的注册了（`hs2.export_model` 这种拼错，这里立刻能发现）
3. 用到的图标名字都存在（Blender 遇到不存在的图标会报错）
4. 关键按钮在：导出面板里必须有「导出模型（骨架 + 网格）」
"""

import os
import sys

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

GAME = os.environ.get("HS2_GAME_DIR", r"D:\game\HS2")


# --------------------------------------------------------------------------
# 假 layout
# --------------------------------------------------------------------------

class Rec:
    def __init__(self):
        self.ops = []
        self.labels = []
        self.icons = []
        self.props = []
        self.lists = []

    def reset(self):
        self.ops[:] = []
        self.labels[:] = []
        self.icons[:] = []
        self.props[:] = []
        self.lists[:] = []


class Node:
    """够用的假 UILayout：容器方法返回自己，叶子方法记账。"""

    def __init__(self, rec):
        self._rec = rec
        self.scale_y = 1.0
        self.enabled = True
        self.alignment = "EXPAND"
        self.use_property_split = False
        self.use_property_decorate = False
        self.emboss = "NORMAL"
        self.active = True

    # ---- 容器 ----
    def row(self, align=False, heading="", **kw):
        return Node(self._rec)

    def column(self, align=False, heading="", **kw):
        return Node(self._rec)

    def box(self):
        return Node(self._rec)

    def grid_flow(self, row_major=True, columns=1, align=True, **kw):
        return Node(self._rec)

    def split(self, factor=0.5, align=False, **kw):
        return Node(self._rec)

    def sub(self):
        return Node(self._rec)

    # ---- 叶子 ----
    def label(self, text="", icon="NONE", **kw):
        self._rec.labels.append(text)
        self._rec.icons.append(icon)

    def operator(self, operator="", text="", icon="NONE", **kw):
        self._rec.ops.append(operator)
        self._rec.icons.append(icon)
        return Node(self._rec)

    def prop(self, data, prop, text=None, icon="NONE", **kw):
        self._rec.props.append("%s.%s" % (type(data).__name__, prop))
        self._rec.icons.append(icon)

    def prop_enum(self, data, prop, value, **kw):
        self._rec.props.append("%s.%s" % (type(data).__name__, prop))

    def separator(self, factor=1.0, **kw):
        pass

    def template_list(self, listtype_name="", list_id="", dataptr=None,
                      propname="", active_dataptr=None, active_propname="",
                      rows=5, **kw):
        self._rec.lists.append(listtype_name)

    def template_id(self, *a, **kw):
        pass

    def template_collection(self, *a, **kw):
        pass


def icon_names():
    fn = bpy.types.UILayout.bl_rna.functions["label"]
    return {e.identifier for e in fn.parameters["icon"].enum_items}


def op_exists(idname):
    ns, _, name = idname.partition(".")
    mod = getattr(bpy.ops, ns, None)
    return mod is not None and hasattr(mod, name)


# --------------------------------------------------------------------------
# 场景准备
# --------------------------------------------------------------------------

def add_fake_rig(arm):
    """给骨架补上控制器骨，好让「已装控制器」那一段也能画到。"""
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for n in ("STU_Foot_L", "STU_Foot_R", "STU_Hand_L", "STU_Hand_R"):
        eb = arm.data.edit_bones.new(n)
        eb.head = (0.0, 0.0, 0.1)
        eb.tail = (0.0, 0.1, 0.1)
    bpy.ops.object.mode_set(mode="OBJECT")
    for side in ("L", "R"):
        for nm in ("STU_Foot_" + side, "STU_Hand_" + side):
            arm.pose.bones[nm]["ik"] = 1.0


def build_scene(with_char=True, with_action=True, hands=False):
    from hs2_rig_kit import ops
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ops.STATE.clear()
    if not with_char:
        return None
    g = ops.get_assets(GAME)
    skel = g.skeleton()
    arm, names, body, head = ops.build_default(g, scale=0.1)
    ops.STATE.update(names=names, skel=skel, scale=0.1)
    arm["hs2_slot"] = arm.get("hs2_slot") or "f1"
    if with_action:
        clip = g.load_clip("mc_f_move_07_L", fps=30.0)
        ops.apply_clip_data(arm, clip, "mc_f_move_07_L", follow=False)
    bpy.context.scene.hs2_show_hand = hands
    bpy.context.view_layer.objects.active = arm
    return arm


# --------------------------------------------------------------------------
# 画
# --------------------------------------------------------------------------

class PanelProxy:
    """冒充面板实例：draw 里会调 self.draw_studio / self.draw_rig 这些辅助方法。"""

    def __init__(self, cls, rec):
        self._cls = cls
        self.layout = Node(rec)

    def __getattr__(self, name):
        fn = getattr(self._cls, name, None)
        if fn is None:
            raise AttributeError(name)
        return lambda *a, **kw: fn(self, *a, **kw)


def draw(cls, rec):
    cls.draw(PanelProxy(cls, rec), bpy.context)


def main():
    import hs2_rig_kit
    hs2_rig_kit.register()

    from hs2_rig_kit import api, ui

    icons = icon_names()
    fails = []

    def check(tag, rec, must_ops=()):
        errs = [t for t in rec.labels if t.startswith("面板出错")]
        bad_ops = [o for o in rec.ops if not op_exists(o)]
        bad_icons = [i for i in rec.icons if i and i not in icons]
        missing = [o for o in must_ops if o not in rec.ops]
        ok = not errs and not bad_ops and not bad_icons and not missing
        print("[ui] %-28s 按钮 %2d · 列表 %d · %s" % (
            tag, len(rec.ops), len(rec.lists), "PASS" if ok else "FAIL"))
        for e in errs:
            print("      错误：%s" % e)
        if bad_ops:
            print("      操作符不存在：%s" % sorted(set(bad_ops)))
        if bad_icons:
            print("      图标不存在：%s" % sorted(set(bad_icons)))
        if missing:
            print("      少了按钮：%s" % missing)
        if not ok:
            fails.append(tag)

    # ---- 没有游戏目录 ----
    rec = Rec()
    orig = api.game_dir
    api.game_dir = lambda: ""
    build_scene(with_char=False)
    draw(ui.HS2_PT_main, rec)
    api.game_dir = orig
    check("主面板 · 没配游戏目录", rec)

    # ---- 没有角色 ----
    scenes = [
        ("主面板 · 工作室", "STUDIO", dict(with_char=False)),
        ("主面板 · H", "H", dict(with_char=False)),
    ]
    for tag, tab, kw in scenes:
        rec = Rec()
        build_scene(**kw)
        bpy.context.scene.hs2_tab = tab
        draw(ui.HS2_PT_main, rec)
        check(tag, rec)

    # ---- 有角色 / 有动作 ----
    cases = [
        ("主面板 · 工作室", "STUDIO"),
        ("主面板 · H", "H"),
        ("主面板 · 换装", "CLOTH"),
    ]
    for tag, tab in cases:
        rec = Rec()
        build_scene()
        bpy.context.scene.hs2_tab = tab
        draw(ui.HS2_PT_main, rec)
        check(tag, rec)

    # ---- 调整：没有控制器 / 有控制器 / 打开手势 ----
    for tag, rigged, hands in (("调整 · 无控制器", False, False),
                               ("调整 · 有控制器", True, False),
                               ("调整 · 手势展开", True, True)):
        rec = Rec()
        arm = build_scene(hands=hands)
        if rigged:
            add_fake_rig(arm)
        bpy.context.scene.hs2_tab = "STUDIO"
        draw(ui.HS2_PT_edit, rec)
        check(tag, rec)

    # ---- 调整：没有角色 ----
    rec = Rec()
    build_scene(with_char=False)
    draw(ui.HS2_PT_edit, rec)
    check("调整 · 没角色", rec)

    # ---- 导出 ----
    rec = Rec()
    build_scene()
    draw(ui.HS2_PT_export, rec)
    check("导出 · 有角色有动作", rec,
          must_ops=("hs2.export_model", "hs2.export_char_anim",
                    "hs2.export_object"))

    rec = Rec()
    build_scene(with_char=False)
    draw(ui.HS2_PT_export, rec)
    check("导出 · 没角色", rec)

    # ---- 外部动作 ----
    rec = Rec()
    build_scene(with_char=False)
    draw(ui.HS2_PT_ext, rec)
    check("外部动作", rec)

    print("\n[ui] 面板自检：%s" % ("PASS" if not fails else "FAIL(%d)" % len(fails)))
    return fails


if __name__ == "__main__":
    main()
