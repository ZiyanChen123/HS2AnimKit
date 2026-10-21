"""界面自检：面板 / 列表 / 点击加载 / 操作符都在这儿过一遍。

    blender -b --factory-startup -P tools/panel_check.py
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

FAILS = []


def check(label, cond, extra=""):
    print("%s %-46s %s" % ("[OK]  " if cond else "[FAIL]", label, extra))
    if not cond:
        FAILS.append(label)


class _L(object):
    """假 layout：只记录调用，用来离屏跑面板 draw。"""

    def __init__(self, log, depth=0):
        self.log = log
        self.depth = depth
        self.scale_y = 1.0
        self.enabled = True

    def _rec(self, name, *a):
        self.log.append((name, a))
        return _L(self.log, self.depth + 1)

    def row(self, **_k):
        return self._rec("row")

    def column(self, **_k):
        return self._rec("column")

    def box(self):
        return self._rec("box")

    def grid_flow(self, **_k):
        return self._rec("grid_flow")

    def label(self, text="", icon="NONE"):
        self._rec("label", text, icon)

    def separator(self, **_k):
        pass

    def prop(self, data, name, **_k):
        getattr(data, name)
        self._rec("prop", name)

    def prop_search(self, data, name, src, src_name, **_k):
        self.prop(data, name)

    def operator(self, idname, **_k):
        self._rec("operator", idname)
        return _L(self.log, self.depth + 1)

    def template_list(self, idname, link, data, propname, active_data,
                      active_propname, **_k):
        _L(self.log).prop(active_data, active_propname)
        self._rec("template_list", idname)


class _Panel(object):
    def __init__(self, log):
        self.layout = _L(log)


def draw_panel(cls, context):
    log = []
    f = _Panel(log)
    for nm in dir(cls):
        if nm.startswith("draw_") and nm != "draw" and callable(getattr(cls, nm)):
            setattr(f, nm, getattr(cls, nm).__get__(f, _Panel))
    cls.draw(f, context)
    bad = [a[0] for n, a in log if n == "label" and a and "出错" in str(a[0])]
    return log, bad


def main():
    import addon_utils
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("hs2_rig_kit", default_set=True)
    check("插件启用", "hs2_rig_kit" in bpy.context.preferences.addons)
    prefs = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    prefs.game_dir = os.environ.get("HS2_GAME_DIR", r"D:\game\HS2")

    from hs2_rig_kit import api, ops, ops2, ui

    # ---- 场景属性 ----
    missing = [n for n in ops2.scene_props() if not hasattr(bpy.types.Scene, n)]
    check("场景属性齐全", not missing, str(missing))
    gone = [n for n in ("hs2_clip_pos", "hs2_clip_enum", "hs2_clip_auto")
            if hasattr(bpy.types.Scene, n)]
    check("旧的滑条/枚举已移除", not gone, str(gone))

    # ---- 面板与列表类 ----
    panels = [c for c in ui.CLASSES if c.__name__.startswith("HS2_PT")]
    uls = [c for c in ui.CLASSES if c.__name__.startswith("HS2_UL")]
    # v3.0 重排之后是 3 个：动作 / 调整 / 外部动作（角色靠「动作驱动」自动放，
    # 输出收敛成外部动作面板里的一句话提示）。这里卡的是「别再冒出多余面板」。
    check("面板数", len(panels) == 3, "%d 个：%s" % (
        len(panels), "、".join(p.bl_label for p in panels)))
    check("面板名不带编号",
          not any(p.bl_label[0] in "①②③④123456789" for p in panels),
          "、".join(p.bl_label for p in panels))
    check("列表类", len(uls) == 3, "%d 个" % len(uls))
    check("面板都注册了",
          all(hasattr(bpy.types, c.bl_idname) for c in panels))
    check("列表都注册了", all(hasattr(bpy.types, c.__name__) for c in uls))

    # ---- 无角色时先画一遍 ----
    for cls in panels:
        _log, bad = draw_panel(cls, bpy.context)
        check("draw %s（无角色）" % cls.bl_label, not bad, str(bad[:1]))

    # ---- 动作驱动：点一行动作 → 演员自动进场 ----
    scn = bpy.context.scene
    scn.hs2_tab = "STUDIO"
    ops2._LIST_KEY.clear()
    ops2.refresh_studio(bpy.context)
    n_studio = len(scn.hs2_studio_items)
    check("工作室列表", n_studio > 500, "%d 项" % n_studio)
    check("开始时没有演员", not api.characters())

    scn.hs2_studio_index = 3
    chars = api.characters()
    check("点动作自动放演员", len(chars) == 1,
          "%s" % [c.name for c in chars.values()])
    first = list(chars.values())[0].name
    scn.hs2_studio_index = 5
    chars2 = api.characters()
    check("换动作换演员（只剩一个）", len(chars2) == 1,
          "%s" % [c.name for c in chars2.values()])

    scn.hs2_clip_filter = "swim"
    ops2.refresh_studio(bpy.context)
    check("搜索过滤", 0 < len(scn.hs2_studio_items) < 50,
          "swim → %d 项" % len(scn.hs2_studio_items))
    scn.hs2_clip_filter = ""

    # ---- H：人数和性别由动作决定 ----
    scn.hs2_tab = "H"
    ops2._LIST_KEY.clear()
    ops2.refresh_h(bpy.context)
    check("H 列表", len(scn.hs2_h_items) > 100,
          "%d 段" % len(scn.hs2_h_items))

    def first_index(cast):
        for i, it in enumerate(scn.hs2_h_items):
            m = [x for x in api.h_data()["modes"] if x["key"] == it.value]
            if m and m[0].get("cast_gender") == cast:
                return i
        return -1

    i_yuri = first_index({"female": 2, "male": 0})
    if i_yuri >= 0:
        scn.hs2_h_index = i_yuri
        roles = [api.gender_of(c) for c in api.characters().values()]
        check("百合自动放两个女角", roles.count("female") == 2 and len(roles) == 2,
              "%d 人" % len(roles))

    i_pair = first_index({"female": 1, "male": 1})
    if i_pair >= 0:
        scn.hs2_h_index = i_pair
        roles = [api.gender_of(c) for c in api.characters().values()]
        check("男女动作放一男一女",
              sorted(roles) == ["female", "male"], "%s" % roles)

    # 换阶段：演员不重建
    n_before = len(api.characters())
    ids_before = {c.name for c in api.characters().values()}
    if len(scn.hs2_stage_items) > 2:
        scn.hs2_stage_index = 2
        ids_after = {c.name for c in api.characters().values()}
        check("换阶段不重建演员",
              len(api.characters()) == n_before and ids_after == ids_before)

    # ---- 操作符 ----
    for name in ("load_h", "setup_cast", "sync_stage",
                 "edit_action", "export_object", "export_fbx", "scan_clips"):
        check("操作符 hs2.%s" % name, hasattr(bpy.ops.hs2, name))

    print()
    print("失败 %d 项：%s" % (len(FAILS), ", ".join(FAILS)) if FAILS
          else "全部通过")


if __name__ == "__main__":
    main()
