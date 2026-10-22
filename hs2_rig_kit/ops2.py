"""面板用的操作符：H 动作、道具、动作剪辑、文本命令。

都做得很薄 —— 真正的逻辑在 :mod:`api` 和 :mod:`animlib`，
这样 Agent 直接调函数，也能得到和点按钮完全一样的结果。
"""
import os
import time

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, IntProperty, StringProperty)

from .ripper import hdb

from . import animlib, api, jpcn, ops, props, rig


def _redraw(context):
    try:
        for area in context.screen.areas:
            area.tag_redraw()
    except Exception:
        pass


# --------------------------------------------------------------------------
# H 动作：模式 → 阶段
# --------------------------------------------------------------------------

_CAT_FALLBACK = [("ALL", "全部分类", "")]
_SUB_FALLBACK = [("ALL", "全部分类", "")]
_CATS = {"key": None, "items": _CAT_FALLBACK}
_SUBS = {"key": None, "items": _SUB_FALLBACK}
_ORDER = {"key": None, "top": {}, "sub": {}}


def _orders(cat):
    """官方大/小项目编号 → 下拉顺序；非官方分类排在后面。"""
    studio = (cat or {}).get("studio") or {}
    key = (len(studio), id(studio))
    if _ORDER["key"] == key:
        return _ORDER
    tops, subs = {}, {}
    for e in studio.values():
        if not e.get("official"):
            continue
        top, top_cn = e.get("top"), e.get("top_cn")
        grp, grp_cn = e.get("group"), e.get("group_cn")
        if top_cn:
            try:
                rank = int(top)
            except (TypeError, ValueError):
                rank = 999
            if top_cn not in tops or rank < tops[top_cn]:
                tops[top_cn] = rank
        if top_cn and grp_cn:
            try:
                rank = int(grp)
            except (TypeError, ValueError):
                rank = 999
            k = (top_cn, grp_cn)
            if k not in subs or rank < subs[k]:
                subs[k] = rank
    _ORDER.update({"key": key, "top": tops, "sub": subs})
    return _ORDER


def _top_key(name):
    order = _ORDER["top"]
    if name in order:
        return (0, order[name], name)
    if name == "H":
        return (1, 0, name)
    if name == "其它":
        return (3, 0, name)
    return (2, 0, name)


def _sub_key(top, name):
    order = _ORDER["sub"]
    if (top, name) in order:
        return (0, order[(top, name)], name)
    if name == "H":
        return (1, 0, name)
    if name == "其它":
        return (3, 0, name)
    return (2, 0, name)


def category_items(self, context):
    """Studio 动作的**大分类**（游戏的「大きい項目」）。

    只列「真能装出来」的分类 —— 那些动作全是单人骨架对不上的（fit 不足，
    比如 sao 联动这类 H 场景）干脆不出现，免得选了一行结果列表是空的，
    卡在空状态只能重启。

    官方的大分類只有 9 个（角色 / バトル / キャラ2 / 角色3 / スタジオ …），
    小分類另有 51 个 —— 一股脑塞进一个下拉会长得没法用，所以分成两级：
    这里是大分類，:func:`subcategory_items` 是小分類。
    """
    try:
        from . import api
        idx = ops.clip_table(context)
        cat = api.catalog()
    except Exception:
        return _CAT_FALLBACK
    counts = {}
    for n, e in idx.items():
        key = _top_of(e)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return _CAT_FALLBACK
    _orders(cat)
    key = tuple(sorted(counts.items()))
    if _CATS["key"] == key:
        return _CATS["items"]
    items = [("ALL", "全部分类（%d）" % sum(counts.values()), "")]
    for g, n in sorted(counts.items(), key=lambda kv: _top_key(kv[0])):
        items.append((g, "%s（%d）" % (g, n), g))
    _CATS["key"] = key
    _CATS["items"] = items
    return items


def _top_of(e):
    """一条动作的顶层分类（大項目）。"""
    if not isinstance(e, dict):
        return "其它"
    top = e.get("top_cn")
    if top:
        return top
    if e.get("group") == "H":
        return "H"
    return "其它"


def _sub_of(e):
    """一条动作的小分类（小項目）。"""
    if not isinstance(e, dict):
        return ""
    if e.get("group") == "H":
        return "H"
    return e.get("group_cn") or e.get("group") or "其它"


def subcategory_items(self, context):
    """Studio 动作的**小分类**（游戏的「中間項目」）。

    只列当前大分类下的那些 —— 大分類「バトル」下面有 34 个小分類
    （女 片手剣 / 男 弓矢 / 女 気弾 …），全列出来还是太长。
    """
    try:
        from . import api
        idx = ops.clip_table(context)
        api.catalog()
    except Exception:
        return _SUB_FALLBACK
    cur = (getattr(context.scene, "hs2_clip_cat", "") or "ALL").strip()
    counts = {}
    for n, e in idx.items():
        if cur not in ("", "ALL", "～") and _top_of(e) != cur:
            continue
        s = _sub_of(e) or "其它"
        counts[s] = counts.get(s, 0) + 1
    if not counts:
        return _SUB_FALLBACK
    key = (cur, tuple(sorted(counts.items())))
    if _SUBS["key"] == key:
        return _SUBS["items"]
    _orders(api.catalog())
    items = [("ALL", "全部（%d）" % sum(counts.values()), "")]
    for g, n in sorted(counts.items(),
                       key=lambda kv: _sub_key(cur, kv[0])):
        items.append((g, "%s（%d）" % (g, n), g))
    _SUBS["key"] = key
    _SUBS["items"] = items
    return items


def h_modes(context):
    """有动作的 H 模式列表（带缓存，UI 每帧都会问）。"""
    try:
        h = api.h_data()
    except Exception:
        return []
    modes = [m for m in (h.get("modes") or []) if m.get("roles")]
    filt = (getattr(context.scene, "hs2_h_filter", "") or "").strip().lower()
    if filt:
        modes = [m for m in modes
                 if filt in (m.get("label") or "").lower()
                 or filt in (m.get("label_jp") or "").lower()
                 or filt in (m.get("label_cn") or "").lower()
                 or filt in (m.get("type_jp") or "").lower()
                 or filt in (m.get("type_cn") or "").lower()
                 or filt in (m.get("type") or "").lower()
                 or filt in (m.get("cast_text") or "")]
    cast = getattr(context.scene, "hs2_h_cast", "ALL") or "ALL"
    if cast != "ALL":
        modes = [m for m in modes if _cast_match(m, cast)]
    modes.sort(key=_h_rank)
    return modes


def _h_rank(m):
    """H 列表的排序：类型 → 场景（壁 / 机 / 沙发…）→ 番号。

    「場所」这组编号是拿来分 Isoaxes 的 —— 同一个场景的体位排在一起，
    找起来和游戏里挑体位的感觉一致（不再是一条长名单按番号排）。
    """
    from .ripper import hdb
    try:
        n = int(hdb.place_key((m.get("group") or {}).get("place")) or -1)
    except ValueError:
        n = -1
    return (m.get("type") or "", n, m.get("id") or 0)


def _cast_match(m, key):
    """按「几个人/什么性别」筛：solo / pair / yuri / 3p 等。"""
    g = m.get("cast_gender") or {}
    f, mm = g.get("female", 0), g.get("male", 0)
    n = f + mm
    if key == "SOLO":
        return n == 1
    if key == "PAIR":
        return f == 1 and mm == 1
    if key == "YURI":
        return f == 2 and mm == 0
    if key == "TWO_M":
        return mm == 2 and f == 0
    if key == "THREE":
        return n == 3
    if key == "TWO":
        return n == 2
    return True


def _on_h_filter(self, context):
    _LIST_KEY.pop("h", None)
    _LIST_KEY.pop("stage", None)
    refresh_all(context)


class HS2_OT_load_h(bpy.types.Operator):
    """把选中的 H 动作装到**当前选中的角色**身上（Studio 的做法：先选角色，再给动作）"""
    bl_idname = "hs2.load_h"
    bl_label = "装到选中角色"
    bl_options = {"REGISTER", "UNDO"}

    mode: StringProperty(default="")
    stage: StringProperty(default="")
    with_props: BoolProperty(name="自动装备道具", default=True)
    all_roles: BoolProperty(name="装到场景里所有对得上的角色", default=False)

    def execute(self, context):
        scn = context.scene
        mode = self.mode or getattr(scn, "hs2_h_mode", "")
        stage = self.stage or getattr(scn, "hs2_h_stage", "")
        if not mode:
            self.report({"ERROR"}, "先选一段 H 动作")
            return {"CANCELLED"}
        char = api.active_character()
        if char is None:
            self.report({"ERROR"}, "先加载角色")
            return {"CANCELLED"}
        try:
            if self.all_roles:
                res = api.load_h(mode, stage, with_props=self.with_props)
                if not res["actions"]:
                    self.report({"ERROR"}, "场景里没有能装这段动作的角色")
                    return {"CANCELLED"}
                first = list(res["info"])[0]
                self.report({"INFO"}, "%s · %s（%d 帧）· %d 个角色%s"
                            % (jpcn.label_of(res["mode"]),
                               res["stage"]["cn"] if res.get("stage") else "",
                               res["info"][first]["frames"],
                               len(res["actions"]),
                               "，道具 %d" % len(res["props"])
                               if res["props"] else ""))
            else:
                res = api.apply_h(mode, stage, target=char,
                                  with_props=self.with_props)
                self.report({"INFO"}, "%s → %s · %s（%d 帧）%s"
                            % (jpcn.label_of(res["mode"]),
                               api.SLOT_CN.get(res["slot"], res["slot"]),
                               res["stage"]["cn"], res["info"]["frames"],
                               "，道具 %d" % len(res["props"])
                               if res["props"] else ""))
        except Exception as e:
            self.report({"ERROR"}, "加载失败：%s" % e)
            return {"CANCELLED"}
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_add_rig_all(bpy.types.Operator):
    """给场景里所有演员一起加控制器"""
    bl_idname = "hs2.add_rig_all"
    bl_label = "给所有演员加控制器"
    bl_options = {"REGISTER", "UNDO"}

    fingers: BoolProperty(name="含手指控制器", default=False)

    def execute(self, context):
        chars = api.characters()
        if not chars:
            chars = {api.slot_of(api.active_character()): api.active_character()}
        n = 0
        for slot in sorted(chars, key=hdb.slot_sort):
            char = chars[slot]
            if char is None:
                continue
            for o in list(context.selected_objects):
                o.select_set(False)
            char.select_set(True)
            context.view_layer.objects.active = char
            try:
                bpy.ops.hs2.add_rig(fingers=self.fingers, follow=True)
                n += 1
            except Exception as e:
                print("[HS2] 给 %s 加控制器失败：%s" % (char.name, e))
        self.report({"INFO"}, "已给 %d 个演员加控制器" % n)
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_select_char(bpy.types.Operator):
    """选中这个角色（之后的动作都装到它身上）"""
    bl_idname = "hs2.select_char"
    bl_label = "选中角色"
    bl_options = {"REGISTER"}

    name: StringProperty(default="")

    def execute(self, context):
        ob = bpy.data.objects.get(self.name)
        if ob is None or ob.type != "ARMATURE":
            self.report({"ERROR"}, "找不到角色 %s" % self.name)
            return {"CANCELLED"}
        for o in list(context.selected_objects):
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        # 切换编辑对象后，把身高/胸围档位、阶段列表这些「跟当前演员走」的状态
        # 刷新成新选中角色的实际值，而不是沿用上一个演员的。
        try:
            _LIST_KEY.pop("stage", None)
            _VARIANT_CACHE["key"] = None
            refresh_stages(context)
            _sync_variant(context)
        except Exception as e:
            print("[HS2] 切换角色刷新失败：%s" % e)
        self.report({"INFO"}, "已选中 %s" % ob.name)
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_delete_char(bpy.types.Operator):
    """删掉这个角色（连同它的身体/头部网格）"""
    bl_idname = "hs2.delete_char"
    bl_label = "删除角色"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        ob = bpy.data.objects.get(self.name)
        if ob is None:
            return {"CANCELLED"}
        ops.remove_character(ob)
        invalidate_lists()
        self.report({"INFO"}, "已删除 %s" % self.name)
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_setup_cast(bpy.types.Operator):
    """按这段 H 动作的人数/性别，把缺的角色补上并装上动作"""
    bl_idname = "hs2.setup_cast"
    bl_label = "补齐角色并装"
    bl_options = {"REGISTER", "UNDO"}

    mode: StringProperty(default="")
    stage: StringProperty(default="")

    def execute(self, context):
        scn = context.scene
        m = current_h_mode(context) if not self.mode else None
        if m is None:
            try:
                m = api.find_mode(self.mode or scn.hs2_h_mode)
            except Exception:
                m = None
        if m is None:
            self.report({"ERROR"}, "先选一段 H 动作")
            return {"CANCELLED"}
        try:
            made = api.setup_cast(m)
            stage = self.stage or current_stage(context) or None
            res = api.load_h(m, stage, with_props=scn.hs2_h_props)
        except Exception as e:
            self.report({"ERROR"}, "失败：%s" % e)
            return {"CANCELLED"}
        scn.hs2_last = "%s · %d 个角色" % (jpcn.label_of(m), len(res["actions"]))
        self.report({"INFO"}, "%s：补了 %d 个角色，装了 %d 个"
                    % (jpcn.label_of(m), len(made["added"]),
                       len(res["actions"])))
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_sync_stage(bpy.types.Operator):
    """把同一段 H 动作的所有角色 + 道具一起切到同一个阶段"""
    bl_idname = "hs2.sync_stage"
    bl_label = "阶段联动"
    bl_options = {"REGISTER", "UNDO"}

    stage: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        try:
            ok, _msg = api.sync_state()
            return ok
        except Exception:
            return False

    def execute(self, context):
        stage = self.stage or getattr(context.scene, "hs2_h_stage", "")
        try:
            msg = api.sync_stage(stage or None)
        except Exception as e:
            self.report({"ERROR"}, "联动失败：%s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, msg)
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_export_object(bpy.types.Operator):
    """把选中的物体（道具/角色）各导出一个独立 FBX，给 UE 用"""
    bl_idname = "hs2.export_object"
    bl_label = "导出选中物品 FBX"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(name="输出目录", subtype="DIR_PATH", default="")
    axis: EnumProperty(name="轴向", default="ue", items=[
        ("ue", "UE / Unity（Y-up）", "FBX 用 Y-up，导入 UE 时它会自己转成 Z-up"),
        ("blender", "Blender 原始（Z-up）", "不转轴，个别流程要用"),
    ])
    include_mesh: BoolProperty(name="带网格", default=True)
    single_file: BoolProperty(name="合成一个文件", default=False)
    filename: StringProperty(name="文件名", default="objects.fbx")

    def invoke(self, context, event):
        if not self.directory:
            self.directory = bpy.path.abspath("//") or ""
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        layout = self.layout
        objs = _picked_objects(context)
        layout.label(text="将导出 %d 个物体：%s" % (
            len(objs), "、".join(o.name for o in objs[:4]) +
            (" …" if len(objs) > 4 else "")), icon="OBJECT_DATA")
        layout.prop(self, "axis")
        layout.prop(self, "include_mesh")
        layout.prop(self, "single_file")
        if self.single_file:
            layout.prop(self, "filename")
        else:
            layout.prop(self, "directory")

    def execute(self, context):
        objs = _picked_objects(context)
        if not objs:
            self.report({"ERROR"}, "先在 3D 视图里选一个或多个物体")
            return {"CANCELLED"}
        try:
            if self.single_file:
                path = os.path.join(self.directory or os.getcwd(),
                                    self.filename or "objects.fbx")
                for ob in objs:
                    ob.select_set(True)
                ops.export_object(objs[0], path, axis=self.axis,
                                  include_mesh=self.include_mesh)
                made = [(objs[0].name, path)]
            else:
                made = ops.export_objects(objs, outdir=self.directory or None,
                                          axis=self.axis,
                                          include_mesh=self.include_mesh)
        except Exception as e:
            self.report({"ERROR"}, "导出失败：%s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, "已导出 %d 个 FBX → %s"
                    % (len(made), made[0][1] if made else ""))
        return {"FINISHED"}


def _picked_objects(context):
    """选中的物体里，跳过蒙皮网格（它们会跟着骨架一起导出）。"""
    out = []
    for ob in context.selected_objects:
        if ob.type not in ("ARMATURE", "MESH", "EMPTY"):
            continue
        if ob.parent is not None and ob.parent.type == "ARMATURE":
            continue        # 身体/道具的网格，跟着骨架走
        out.append(ob)
    if not out:
        out = [ob for ob in context.selected_objects
               if ob.type in ("ARMATURE", "MESH")]
    return out


class HS2_OT_load_prop(bpy.types.Operator):
    """单独装一个 H 道具，可选挂到某根骨"""
    bl_idname = "hs2.load_prop"
    bl_label = "装道具"
    bl_options = {"REGISTER", "UNDO"}

    prefab: StringProperty(name="道具", default="")
    pack: StringProperty(name="包", default="01")
    bone: StringProperty(name="挂到骨骼", default="")
    who: EnumProperty(name="角色", default="female",
                      items=[("female", "女角", ""), ("male", "男角", "")])

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        if not self.prefab:
            self.report({"ERROR"}, "要填道具 prefab 名（比如 p_item_dildo）")
            return {"CANCELLED"}
        try:
            ob = api.load_prop(self.prefab, pack=self.pack,
                               bone=self.bone or None, who=self.who)
        except Exception as e:
            self.report({"ERROR"}, "装道具失败：%s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, "%s：%d 骨 %d 网格"
                    % (ob.name, len(ob.data.bones),
                       ob.get("hs2_prop_meshes", 0)))
        return {"FINISHED"}




# --------------------------------------------------------------------------
# 列表数据（面板用 UIList 显示，点一行就装）
# --------------------------------------------------------------------------

class HS2ClipItem(bpy.types.PropertyGroup):
    label: StringProperty()
    value: StringProperty()
    sub: StringProperty()


_LIST_KEY = {}


_BUSY = {"on": False}


def _fill(coll, rows):
    """cols: [(label, value, sub), ...]

    重建列表时把回调关掉 —— 否则 index 归零会触发「点一下就装」，
    搜个关键字就莫名其妙装了第一个动作。
    """
    _BUSY["on"] = True
    try:
        coll.clear()
        for label, value, sub in rows:
            it = coll.add()
            it.label = label[:80]
            it.value = value
            it.sub = (sub or "")[:40]
    finally:
        _BUSY["on"] = False


def invalidate_lists():
    """换游戏目录 / 重新扫描之后，让三个列表都重建一次。"""
    _LIST_KEY.clear()
    _CATS["key"] = None
    try:
        ops._STUDIO_VAR_CACHE["key"] = None
        ops._STUDIO_VAR_CACHE["map"] = None
    except Exception:
        pass


def _need(kind, key):
    if _LIST_KEY.get(kind) == key:
        return False
    _LIST_KEY[kind] = key
    return True


def refresh_studio(context):
    scn = context.scene
    key = ("studio", scn.hs2_clip_filter, scn.hs2_clip_cat, scn.hs2_clip_sub)
    if not _need("studio", key):
        return
    try:
        names = ops.clip_list(context)
    except Exception:
        names = []
    # 防呆：选中的分类一个都装不出来时，回退到「全部」——绝不把列表留成
    # 空的卡死状态（旧版选 sao 联动这类组就会这样，只能重启 Blender）。
    cat = (getattr(scn, "hs2_clip_cat", "") or "").strip()
    sub = (getattr(scn, "hs2_clip_sub", "") or "").strip()
    if not names and (cat and cat not in ("ALL", "～")):
        try:
            old = scn.hs2_clip_cat
            scn.hs2_clip_cat = "ALL"
            names = ops.clip_list(context)
        except Exception:
            names = []
    elif not names and (sub and sub not in ("ALL", "～")):
        try:
            old = scn.hs2_clip_sub
            scn.hs2_clip_sub = "ALL"
            names = ops.clip_list(context)
        except Exception:
            names = []
    idx = (ops.STATE.get("game") or api.assets()).clip_index()

    try:
        _orders(api.catalog())
    except Exception:
        pass

    def key(n):
        e = idx.get(n) or {}
        # 跟下拉一样按官方编号排，同类聚在一起，组内按名字排
        grp = _sub_of(e) or "～"
        return ((1 if grp == "H" else 0,) + _sub_key(_top_of(e), grp) + (n,))

    only_h = (scn.hs2_clip_cat == "H")
    rows = []
    for n in sorted(names, key=key):
        e = idx.get(n) or {}
        if e.get("group") == "H" and not only_h:
            # Studio 包内嵌的 H 动作（S_Idle 这种）默认不混进来，
            # 想看得在「分类」里选 H
            continue
        # 左边显示官方的「表示名」（攻撃１ 这种），右边标小分類
        label = e.get("name") or n
        if e.get("official") and label != n:
            label = "%s · %s" % (label, n)
        rows.append((label, n, _sub_of(e) or e.get("group") or ""))
    _fill(scn.hs2_studio_items, rows)


def refresh_h(context):
    scn = context.scene
    key = ("h", scn.hs2_h_filter, scn.hs2_h_cast)
    if not _need("h", key):
        return
    modes = h_modes(context)
    # 同名的几段是真的不同的体检（不同包 / 不同番号），不能合并 —— 但不加区分
    # 界面上就是两行一模一样的名字，看着像「列表去重失败」。所以重名的补个编号。
    counts = {}
    for m in modes:
        name = jpcn.label_of(m)
        counts[name] = counts.get(name, 0) + 1
    rows = []
    for m in modes:
        label = jpcn.label_of(m) or m["key"]
        if counts.get(label, 0) > 1:
            label = "%s  [%s-%s]" % (label, m.get("pack"), m.get("id"))
        # 右侧那列：场景 + 道具件数 + 阵容。以前只有阵容，看不出这一段要带什么。
        sub = []
        if m.get("place_cn"):
            sub.append(m["place_cn"])
        n = len(m.get("props") or [])
        if n:
            sub.append("道具×%d" % n)
        if m.get("cast_text"):
            sub.append(m["cast_text"])
        rows.append((label, m["key"], " · ".join(sub)))
    _fill(scn.hs2_h_items, rows)


def current_h_mode(context):
    scn = context.scene
    items = scn.hs2_h_items
    i = scn.hs2_h_index
    if 0 <= i < len(items):
        key = items[i].value
        for m in h_modes(context):
            if m["key"] == key:
                return m
    modes = h_modes(context)
    return modes[0] if modes else None


def refresh_stages(context):
    scn = context.scene
    m = current_h_mode(context)
    char = api.active_character()
    slot = api._slot_for(m, char) if (m and char) else None
    if slot is None and m:
        cast = hdb.cast_of(m.get("roles") or {})
        slot = cast[0] if cast else None
    key = ("stage", m["key"] if m else "", slot)
    if not _need("stage", key):
        return
    rows = []
    if m and slot:
        for g in api.stage_groups(m, slot):
            sub = {"height": "身高", "bust": "胸围"}.get(g["kind"], "")
            rows.append((g["label"], g["key"], sub))
    _fill(scn.hs2_stage_items, rows)
    if rows and scn.hs2_stage_index >= len(rows):
        scn.hs2_stage_index = 0


def current_stage(context):
    scn = context.scene
    items = scn.hs2_stage_items
    i = scn.hs2_stage_index
    if 0 <= i < len(items):
        return items[i].value
    return ""


# --------------------------------------------------------------------------
# 后台维护：列表在面板**外面**重建
# --------------------------------------------------------------------------

# 在 draw() 里清空并重填 CollectionProperty，Blender 的列表框经常画成空框
# （它拿到的是上一次的 item 指针）。所以列表一律在定时器/回调里重建，
# draw 只负责画。
_TIMER = {"on": False, "scan": False, "tried": False, "panel": 0.0}


def run_scan():
    """建/重建动作库索引（带进度条）。

    定时器里不能保证 bpy.ops 的上下文，所以这里直接调底层，不走操作符。
    """
    g = api.assets()
    wm = bpy.context.window_manager

    def prog(i, total, name):
        if wm is not None:
            wm.progress_update(int(i * 100 / max(total, 1)))

    if wm is not None:
        wm.progress_begin(0, 100)
    try:
        idx = g.clip_index(rebuild=True, progress=prog)
        ops.STATE["index"] = idx
    finally:
        if wm is not None:
            wm.progress_end()
    invalidate_lists()
    return idx


def _catalog_ready():
    try:
        return os.path.isfile(api.assets().catalog_path())
    except Exception:
        return False


def scanning():
    return _TIMER["scan"]


def maybe_autoscan(context):
    """第一次用：没有索引就自动建一次，用户不用点任何按钮。"""
    if _catalog_ready():
        _TIMER["scan"] = False
        return
    if _TIMER["scan"] or _TIMER["tried"]:
        return
    if not api.game_dir():
        return
    _TIMER["tried"] = True
    _TIMER["scan"] = True      # 立刻标记，面板马上显示「正在建立…」

    def run():
        try:
            run_scan()
        except Exception as e:
            print("[HS2] 自动建索引失败：%s" % e)
        finally:
            _TIMER["scan"] = False
            invalidate_lists()
            _redraw(bpy.context)
        return None

    try:
        bpy.app.timers.register(run, first_interval=0.15)
    except Exception:
        _TIMER["tried"] = False
        _TIMER["scan"] = False


def refresh_all(context):
    """定时器里调用：把当前页签需要的列表准备好。"""
    scn = getattr(context, "scene", None)
    if scn is None or not hasattr(scn, "hs2_tab"):
        return
    tab = scn.hs2_tab
    if tab == "STUDIO":
        refresh_studio(context)
        maybe_autoscan(context)
    elif tab == "H":
        refresh_h(context)
        refresh_stages(context)
        maybe_autoscan(context)
    elif tab == "CLOTH":
        try:
            from . import clothops
            clothops.refresh(context)
        except Exception:
            pass


def note_panel_draw():
    """HS2 面板真的在画时调用：说明用户在看这个页签，才值得读目录/刷列表。

    Blender 启动时插件会被加载，但用户可能压根不打开 HS2 页签。以前定时器
    一启动就 refresh，等于每次开机都去读一遍动作库缓存（缓存坏了就重扫
    几十秒），白白卡启动。现在没画面板就什么都不做。
    """
    _TIMER["panel"] = time.time()


def _tick():
    # 3 秒内没画过 HS2 面板 → 认为面板关着，空转即可
    if time.time() - _TIMER["panel"] > 3.0:
        return 0.5
    try:
        refresh_all(bpy.context)
    except Exception:
        pass
    return 0.4


def start_timer():
    if _TIMER["on"]:
        return
    try:
        bpy.app.timers.register(_tick, first_interval=0.2, persistent=True)
        _TIMER["on"] = True
    except Exception:
        pass


def stop_timer():
    try:
        bpy.app.timers.unregister(_tick)
    except Exception:
        pass
    _TIMER["on"] = False


# --------------------------------------------------------------------------
# 列表点击回调
# --------------------------------------------------------------------------

def _on_tab(self, context):
    invalidate_lists()
    refresh_all(context)


def _on_clip_filter(self, context):
    _LIST_KEY.pop("studio", None)
    self.hs2_studio_index = -1
    refresh_all(context)


def _on_clip_cat(self, context):
    """换了大分類 → 小分類得跟着重算（Blender 的动态 EnumProperty 不会
    自动刷新 items，得手动把当前值清掉再触发一次更新）。"""
    try:
        scn = context.scene
        # 先把当前小类归零，否则 Blender 会因为「当前值不在新列表里」报错
        scn["hs2_clip_sub"] = scn.get("hs2_clip_sub", "ALL")
        scn.hs2_clip_sub = "ALL"
    except Exception:
        pass
    _LIST_KEY.pop("studio", None)
    try:
        self.hs2_studio_index = -1
    except Exception:
        pass
    refresh_all(context)


def _on_h_index(self, context):
    _LIST_KEY.pop("stage", None)
    if _BUSY["on"]:
        return
    refresh_stages(context)
    scn = context.scene
    m = current_h_mode(context)
    if m is None:
        return
    # 推荐一个「能看」的阶段（中速抽插），并把它设为当前选中
    char = api.active_character()
    slot = api._slot_for(m, char) if char else None
    if slot is None:                     # 还没演员：按动作的阵容挑第一个槽
        cast = hdb.cast_of(m.get("roles") or {})
        slot = cast[0] if cast else None
    groups = api.stage_groups(m, slot) if slot else []
    pick = api._pick_group(groups, None) if groups else None
    if pick is None:
        return
    for i, it in enumerate(scn.hs2_stage_items):
        if it.value == pick["key"]:
            _BUSY["on"] = True
            try:
                scn.hs2_stage_index = i
            finally:
                _BUSY["on"] = False
            break
    _load_h_stage(context, pick["key"], m)


def _on_studio_index(self, context):
    if _BUSY["on"]:
        return
    items = self.hs2_studio_items
    i = self.hs2_studio_index
    if not (0 <= i < len(items)):
        return
    _load_studio(context, items[i].value)


_STUDIO_HEIGHT_FALLBACK = [("NONE", "—", "")]
_STUDIO_HEIGHT_CACHE = {"key": None, "items": _STUDIO_HEIGHT_FALLBACK}


def studio_height_items(self, context):
    """当前选中工作室动作的身高档位下拉（只有动作带 S/M/L 变体才列）。"""
    try:
        items = self.hs2_studio_items
        i = self.hs2_studio_index
        if not (0 <= i < len(items)):
            _STUDIO_HEIGHT_CACHE["key"] = None
            return _STUDIO_HEIGHT_CACHE["items"]
        name = items[i].value
        vmap = ops._studio_variant_map(context)
        hv = vmap.get(name)
        if not hv:
            _STUDIO_HEIGHT_CACHE["key"] = None
            return _STUDIO_HEIGHT_CACHE["items"]
        default, avail = hv
        key = (name, default, tuple(sorted(avail)))
        if _STUDIO_HEIGHT_CACHE["key"] == key:
            return _STUDIO_HEIGHT_CACHE["items"]
        out = []
        for h in hdb.HEIGHT_ORDER:
            if h in avail:
                out.append((h, hdb.HEIGHT_CN.get(h, h), ""))
        out.sort(key=lambda x: default == x[0] and 0 or 1)  # 默认档排最前
        _STUDIO_HEIGHT_CACHE["key"] = key
        _STUDIO_HEIGHT_CACHE["items"] = out
        return out
    except Exception:
        return _STUDIO_HEIGHT_CACHE["items"]


def _on_studio_height(self, context):
    """切工作室动作的身高档：换到对应变体动作，并重载。
    只重装当前选中的这条动作（不触发列表重建的麻烦）。"""
    if _BUSY["on"]:
        return
    h = getattr(self, "hs2_studio_height", "")
    if not h or h == "NONE":
        return
    items = self.hs2_studio_items
    i = self.hs2_studio_index
    if not (0 <= i < len(items)):
        return
    _BUSY["on"] = True
    try:
        _load_studio(context, items[i].value)
    finally:
        _BUSY["on"] = False
    _redraw(context)


def _on_stage_index(self, context):
    if _BUSY["on"]:
        return
    stage = current_stage(context)
    if not stage:
        return
    _load_h_stage(context, stage)


def _current_group(context):
    """当前 H 模式 + 选中阶段对应的阶段组（有身高/胸围变体信息）。"""
    from . import api
    m = current_h_mode(context)
    if m is None:
        return None, None, None
    char = api.active_character()
    slot = api._slot_for(m, char) if char else None
    if slot is None:
        cast = hdb.cast_of(m.get("roles") or {})
        slot = cast[0] if cast else None
    groups = api.stage_groups(m, slot) if slot else []
    stage = current_stage(context)
    g = api._find_group(groups, stage) or api._pick_group(groups, None)
    return m, stage, g


_VARIANT_FALLBACK = [("NONE", "—", "")]
_VARIANT_CACHE = {"key": None, "items": _VARIANT_FALLBACK}


def variant_items(self, context):
    """当前选中阶段的身高/胸围档位 —— 只有这一段动作真有变体才列出来。"""
    try:
        m, _stage, g = _current_group(context)
        if g is None:
            return _VARIANT_CACHE["items"]
        variants = hdb.group_variants(g)
        if not variants:
            _VARIANT_CACHE["key"] = None
            _VARIANT_CACHE["items"] = _VARIANT_FALLBACK
            return _VARIANT_CACHE["items"]
        key = (m["key"], g["key"], tuple(l for l, _, _ in variants))
        if _VARIANT_CACHE["key"] != key:
            _VARIANT_CACHE["key"] = key
            _VARIANT_CACHE["items"] = [
                (l, cn, "") for l, cn, _it in variants]
        return _VARIANT_CACHE["items"]
    except Exception:
        return _VARIANT_CACHE["items"]


def _on_variant(self, context):
    """选身高/胸围档位：只改**当前角色**的档位，别的演员保持自己的。

    同一段 H 动作里男女是各自的身形（有高有矮），不能让一套档位同步到
    所有人 —— 这里只重装当前选中的角色，并同步缩放它的身高骨骼。
    """
    if _BUSY["on"]:
        return
    from . import api
    letter = getattr(self, "hs2_h_variant", "")
    if not letter or letter == "NONE":
        return
    m, stage, g = _current_group(context)
    if m is None:
        return
    kind = g["kind"] if g else "height"
    _BUSY["on"] = True
    try:
        api.apply_variant(m, letter, kind=kind, stage=stage or None)
        cn = hdb.BUST_CN.get(letter, letter) if kind == "bust" else \
            hdb.HEIGHT_CN.get(letter, letter)
        context.scene.hs2_last = "%s · %s" % (
            "胸围" if kind == "bust" else "身高", cn)
    except Exception as e:
        print("[HS2] 档位切换失败：%s" % e)
    finally:
        _BUSY["on"] = False
    _redraw(context)


def _load_studio(context, name):
    """装一条动作 = 换一场景：清掉上一场的演员和道具，再按这条动作放演员。

    列表里现在是折叠后的基础名（cook_01），这里按当前身高档位还原成
    具体动作（cook_01_M）并加载。
    """
    scn = context.scene
    height = getattr(scn, "hs2_studio_height", "") or ""
    concrete = ops.resolve_studio_clip(name, height if height not in ("", "NONE") else None)
    try:
        res = api.load_studio_fresh(concrete)
    except Exception as e:
        print("[HS2] 装动作失败 %s：%s" % (name, e))
        context.scene.hs2_last = "装不上：%s" % e
        return None
    info = res["info"]
    # 让身高下拉反映实际加载的档位（默认 M 时把枚举同步成 M）
    hv = hdb.split_variants(concrete)
    if hv["height"]:
        try:
            avail = [it[0] for it in studio_height_items(scn, context)]
            if hv["height"] in avail and scn.hs2_studio_height != hv["height"]:
                scn.hs2_studio_height = hv["height"]
        except Exception:
            pass
    shown = name
    if concrete != name and hv["height"]:
        shown = "%s · %s" % (name, hdb.HEIGHT_CN.get(hv["height"], hv["height"]))
    context.scene.hs2_last = "%s · %d 帧 · %s" % (
        shown, info["frames"], "男" if res["gender"] == "male" else "女")
    invalidate_lists()
    _redraw(context)
    return res["action"]


def _load_h_stage(context, stage, mode=None):
    """装一段 H 动作：人数/性别对不上就清场重装，对得上就只换阶段。"""
    m = mode or current_h_mode(context)
    if m is None:
        return None
    try:
        r = api.load_h_fresh(m, stage)
    except Exception as e:
        context.scene.hs2_last = "装不上：%s" % e
        print("[HS2] H 动作失败：%s" % e)
        return None
    stage_cn = r.get("stage", {}).get("cn") or ""
    info = (r.get("info") or {})
    frames = info[list(info)[0]]["frames"] if info else 0
    context.scene.hs2_last = "%s · %s%s" % (
        jpcn.label_of(m), stage_cn,
        "（沿用当前演员）" if r.get("reused") else
        (" · %d 帧 · %d 人" % (frames, len(api.characters()))))
    _sync_variant(context)
    invalidate_lists()
    _redraw(context)
    return r


def _sync_variant(context):
    """把「体型档位」枚举同步到当前角色的档位（切阶段后 items 会变）。"""
    from . import api
    scn = context.scene
    char = api.active_character()
    letter = char.get("hs2_h_letter") if char else ""
    try:
        available = [l for l, _c, _d in variant_items(scn, context)]
    except Exception:
        available = []
    if letter and letter in available and scn.hs2_h_variant != letter:
        _BUSY["on"] = True
        try:
            scn.hs2_h_variant = letter
        except Exception:
            pass
        finally:
            _BUSY["on"] = False


# --------------------------------------------------------------------------
# 动作剪辑
# --------------------------------------------------------------------------

class HS2_OT_root_motion(bpy.types.Operator):
    """把 Hips 上的水平位移/转身/跳跃提取到 p_cf_anim 根骨"""
    bl_idname = "hs2.root_motion"
    bl_label = "根动画转换"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if ops.current_arm() is None:
            self.report({"ERROR"}, "先加载角色")
            return {"CANCELLED"}
        if api.current_action() is None:
            self.report({"ERROR"}, "当前没有动作")
            return {"CANCELLED"}
        scn = context.scene
        move = getattr(scn, "hs2_rm_move", True)
        turn = getattr(scn, "hs2_rm_turn", True)
        jump = getattr(scn, "hs2_rm_jump", False)
        if not (move or turn or jump):
            self.report({"ERROR"}, "至少选一个要提取的分量")
            return {"CANCELLED"}
        try:
            act = api.root_motion(horizontal=move, turn=turn, vertical=jump)
        except Exception as e:
            self.report({"ERROR"}, "根动画转换失败：%s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, "根动画完成：%s" % act.name)
        return {"FINISHED"}


class HS2_OT_edit_action(bpy.types.Operator):
    """动作后处理：镜像 / 变速 / 贴地 / 重打控制器帧"""
    bl_idname = "hs2.edit_action"
    bl_label = "动作后处理"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(name="操作", default="MIRROR", items=[
        ("MIRROR", "左右镜像", ""),
        ("SPEED", "变速", ""),
        ("GROUND", "放回地面", ""),
        ("KEYRIG", "重打控制器帧", ""),
    ])
    value: FloatProperty(name="参数", default=0.0, min=0.0, max=20.0,
                         description="变速：2=慢一倍，0.5=快一倍")

    def execute(self, context):
        if ops.current_arm() is None:
            self.report({"ERROR"}, "先加载角色")
            return {"CANCELLED"}
        try:
            if self.mode == "MIRROR":
                act = api.mirror()
                msg = "镜像完成：%s" % act.name
            elif self.mode == "SPEED":
                act = api.retime(factor=self.value or 2.0)
                msg = "变速完成：%s（×%g）" % (act.name, self.value)
            elif self.mode == "GROUND":
                act = api.ground()
                msg = "贴地完成：%s" % act.name
            else:
                n = api.keyframe_rig()
                msg = "控制器重新打帧：%d 帧" % n
        except Exception as e:
            self.report({"ERROR"}, "失败：%s" % e)
            return {"CANCELLED"}
        self.report({"INFO"}, msg)
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 注册
# --------------------------------------------------------------------------

CLASSES = (HS2_OT_load_h, HS2_OT_load_prop, HS2_OT_edit_action,
           HS2_OT_root_motion, HS2_OT_sync_stage, HS2_OT_export_object,
           HS2_OT_setup_cast, HS2_OT_select_char, HS2_OT_delete_char,
           HS2_OT_add_rig_all)


def scene_props():
    return {
        # ---- 页签 ----
        "hs2_tab": EnumProperty(
            name="分类", default="STUDIO", update=_on_tab,
            items=[("STUDIO", "工作室", ""), ("H", "H 动作", ""),
                   ("CLOTH", "换装", "")]),
        # ---- 角色 ----
        # ---- 工作室动作 ----
        "hs2_studio_items": CollectionProperty(type=HS2ClipItem),
        "hs2_studio_index": IntProperty(name="动作", default=-1,
                                        update=_on_studio_index),
        "hs2_clip_filter": StringProperty(name="搜索", default="",
                                          update=_on_clip_filter),
        "hs2_clip_cat": EnumProperty(name="大类", items=category_items,
                                     update=_on_clip_cat,
                                     description="按游戏的「大きい項目」过滤"),
        "hs2_clip_sub": EnumProperty(name="小类", items=subcategory_items,
                                     update=_on_clip_filter,
                                     description="按游戏的「中間項目」过滤"),
        "hs2_studio_height": EnumProperty(
            name="身高", default=0, items=studio_height_items,
            update=_on_studio_height,
            description="工作室动作的身高档位（cook_01_S/M/L 这类变体）"),
        # ---- H 动作 ----
        "hs2_h_items": CollectionProperty(type=HS2ClipItem),
        "hs2_h_index": IntProperty(name="H 动作", default=-1,
                                   update=_on_h_index),
        "hs2_h_filter": StringProperty(name="搜索", default="",
                                       update=_on_h_filter),
        "hs2_h_cast": EnumProperty(
            name="人数", default="ALL", update=_on_h_filter,
            items=[("ALL", "全部", ""), ("SOLO", "单人", ""),
                   ("PAIR", "男女", ""), ("YURI", "百合", ""),
                   ("THREE", "3P", "")]),
        "hs2_stage_items": CollectionProperty(type=HS2ClipItem),
        "hs2_stage_index": IntProperty(name="阶段", default=0,
                                       update=_on_stage_index),
        "hs2_h_props": BoolProperty(name="带道具", default=True),
        # ---- 体型档位（只有当前阶段真有身高/胸围变体才显示，items 动态生成）----
        "hs2_h_variant": EnumProperty(
            name="体型", default=0, items=variant_items, update=_on_variant,
            description="当前阶段的身高/胸围档位，切档会重装动画并按档位缩放身高骨骼"),
        # ---- 通用 ----
        "hs2_last": StringProperty(name="状态", default=""),
        "hs2_fingers": BoolProperty(
            name="手指控制器", default=False,
            description="给每根手指加 FK 控制器（37 个变 100+ 个，一般不需要）"),
        "hs2_show_hand": BoolProperty(name="手势", default=False),
        "hs2_show_disp": BoolProperty(name="显示", default=False),
        # ---- 根动画 ----
        "hs2_rm_move": BoolProperty(name="水平位移", default=True,
                                    description="前后左右移动提取到根骨"),
        "hs2_rm_turn": BoolProperty(name="转身", default=True,
                                    description="绕竖直轴的旋转提取到根骨"),
        "hs2_rm_jump": BoolProperty(name="垂直位移", default=False,
                                    description="跳跃高度提取到根骨"),
    }


def register():
    bpy.utils.register_class(HS2ClipItem)
    for c in CLASSES:
        bpy.utils.register_class(c)
    for name, prop in scene_props().items():
        setattr(bpy.types.Scene, name, prop)
    start_timer()


def unregister():
    stop_timer()
    for name in scene_props():
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    bpy.utils.unregister_class(HS2ClipItem)
