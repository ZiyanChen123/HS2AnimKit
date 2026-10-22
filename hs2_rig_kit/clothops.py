"""换装页：衣服列表、穿上/换下、批量导出。"""

import os
import time

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

from . import api, clothes, ops
from .ripper import clothes as rc
from .ripper import unityfs

STATE = {
    "records": [],
    "by_key": {},
    "game_dir": "",
    "loading": False,
    "tried": False,
    "items_key": None,
    "skel": None,
    "states": {},
}

SLOT_ITEMS = [("ALL", "全部", "")] + [
    (slot, rc.SLOT_LABELS.get(slot, slot), "") for slot in rc.SLOT_ORDER
]


def _redraw(context=None):
    ctx = context or bpy.context
    try:
        for area in ctx.screen.areas:
            area.tag_redraw()
    except Exception:
        pass


def _set_status(scene, text):
    scene.hs2_cloth_status = text


def record_key(rec):
    return "%s|%s|%s" % (rec.get("bundle", ""), rec.get("prefab", ""),
                         rec.get("id", ""))


def active_gender(context):
    ch = api.active_character()
    if ch is None:
        return None
    return ch.get("hs2_gender") or "female"


def _reset(game_dir=""):
    STATE["records"] = []
    STATE["by_key"] = {}
    STATE["game_dir"] = game_dir
    STATE["loading"] = False
    STATE["tried"] = False
    STATE["items_key"] = None
    STATE["skel"] = None
    STATE["states"] = {}


def _install(records):
    STATE["records"] = records
    STATE["by_key"] = {record_key(r): r for r in records}
    STATE["items_key"] = None
    STATE["states"] = {}


def ensure_loaded(context):
    """保证衣服目录已读入。没有缓存时后台扫一次（约 0.7 秒）。"""
    scene = context.scene
    game_dir = api.game_dir()
    if not game_dir:
        return False
    if STATE["game_dir"] != game_dir:
        _reset(game_dir)
    if STATE["records"]:
        return True
    if STATE["loading"] or STATE["tried"]:
        return False
    try:
        assets = ops.get_assets(game_dir)
    except Exception as e:
        _set_status(scene, "游戏目录不可用：%s" % e)
        STATE["tried"] = True
        return False

    # 有缓存：直接读（几毫秒）
    if os.path.isfile(assets.clothes_catalog_path()):
        try:
            _install(assets.clothes_catalog())
            return True
        except Exception as e:
            print("[HS2] 衣服缓存读取失败：%s" % e)

    STATE["loading"] = True
    STATE["tried"] = True
    _set_status(scene, "正在扫描衣服目录…")

    def run():
        try:
            recs = assets.clothes_catalog(rebuild=True)
            _install(recs)
            _set_status(bpy.context.scene, "衣服：%d 件" % len(recs))
        except Exception as e:
            _set_status(bpy.context.scene, "扫描失败：%s" % e)
            print("[HS2] 衣服扫描失败：%s" % e)
        finally:
            STATE["loading"] = False
            STATE["items_key"] = None
            _redraw()
        return None

    try:
        bpy.app.timers.register(run, first_interval=0.05)
    except Exception:
        STATE["loading"] = False
    return False


def filtered_records(context, ignore_filters=False):
    scene = context.scene
    gender = active_gender(context)
    slot = scene.hs2_cloth_slot
    needle = (scene.hs2_cloth_filter or "").strip().lower()
    out = []
    for r in STATE["records"]:
        if not ignore_filters:
            if gender and r.get("gender") != gender:
                continue
            if slot not in ("", "ALL") and r.get("slot") != slot:
                continue
            if needle:
                text = " ".join(str(r.get(k, "")) for k in
                                ("name", "name_jp", "prefab", "id")).lower()
                if needle not in text:
                    continue
        out.append(r)

    order = {s: i for i, s in enumerate(rc.SLOT_ORDER)}

    def sort_key(r):
        try:
            ident = int(r.get("id") or 0)
        except Exception:
            ident = 10 ** 9
        return (r.get("gender", ""), order.get(r.get("slot"), 99), ident,
                r.get("name", ""))

    out.sort(key=sort_key)
    return out


def _fill(coll, rows):
    coll.clear()
    for label, value, sub in rows:
        it = coll.add()
        it.label = label[:80]
        it.value = value
        it.sub = (sub or "")[:40]


def refresh(context):
    """定时器里调用：按当前角色/槽位/搜索重建列表。"""
    scene = context.scene
    if not ensure_loaded(context):
        return
    gender = active_gender(context)
    key = (STATE["game_dir"], len(STATE["records"]), gender,
           scene.hs2_cloth_slot, scene.hs2_cloth_filter)
    if STATE["items_key"] == key:
        return
    STATE["items_key"] = key
    rows = []
    if gender:
        for r in filtered_records(context):
            sub = "%s %s" % (rc.SLOT_LABELS.get(r.get("slot"), r.get("slot")),
                             r.get("id", ""))
            rows.append((r.get("name") or "?", record_key(r), sub))
    _fill(scene.hs2_cloth_items, rows)
    if rows and not (0 <= scene.hs2_cloth_index < len(rows)):
        scene.hs2_cloth_index = 0
    ensure_state(context)


def current_record(context):
    scene = context.scene
    items = scene.hs2_cloth_items
    i = scene.hs2_cloth_index
    if 0 <= i < len(items):
        rec = STATE["by_key"].get(items[i].value)
        if rec is not None:
            return rec
    if len(items):
        return STATE["by_key"].get(items[0].value)
    return None


def record_plan(rec):
    """这件衣服的 (默认状态, [半脱/全脱分支...])。"""
    key = record_key(rec)
    cached = STATE["states"].get(key)
    if cached is not None:
        return cached
    plan = ("", [])
    try:
        b = unityfs.Bundle(rec["bundle"])
        try:
            plan = rc.state_plan(None, rec, bundle=b)
        finally:
            try:
                b.env = None
            except Exception:
                pass
    except Exception as e:
        print("[HS2] 读取衣服状态失败 %s: %s" % (rec.get("prefab"), e))
    STATE["states"][key] = plan
    return plan


def state_options(context):
    """给 UI 用：[(state_id, label, desc), ...]。

    默认 = 不脱（不带后缀，state_id 固定为 ""）；之后按实际分支给半脱/全脱。
    """
    rec = current_record(context)
    if rec is None:
        return [("", "默认", "")]
    _base, variants = record_plan(rec)
    opts = [("", "默认", "")]
    for i, letter in enumerate(variants):
        label = "半脱" if i == 0 else ("全脱" if i == 1 else "变体%d" % (i + 1))
        opts.append((letter, label, ""))
    return opts


def state_label(context, sid):
    for s, label, _desc in state_options(context):
        if s == sid:
            return label
    return "默认" if not sid else sid


def ensure_state(context):
    scene = context.scene
    ids = [x[0] for x in state_options(context)]
    if scene.hs2_cloth_state not in ids:
        scene.hs2_cloth_state = ids[0] if ids else ""


def worn_records(context):
    """角色身上当前穿着的衣服 -> [(record, state), ...]。"""
    char = api.active_character()
    if char is None or not ensure_loaded(context):
        return []
    out = []
    seen = set()
    for job in clothes.worn_jobs(char):
        key = "%s|%s|%s" % (job["bundle"], job["prefab"], job["id"])
        rec = STATE["by_key"].get(key)
        if rec is None:
            rec = next((r for r in STATE["records"]
                        if r.get("prefab") == job["prefab"]
                        and str(r.get("id")) == job["id"]), None)
        if rec is None:
            continue
        sig = (key, job["state"])
        if sig in seen:
            continue
        seen.add(sig)
        out.append((rec, job["state"]))
    return out


def invalidate():
    STATE["items_key"] = None


def _skeleton(context):
    if STATE["skel"] is not None:
        return STATE["skel"]
    skel = ops.STATE.get("skel")
    if skel is None:
        skel = ops.get_assets(api.game_dir()).skeleton()
    STATE["skel"] = skel
    return skel


def _on_cloth_filter(self, context):
    STATE["items_key"] = None
    refresh(context)


def _on_cloth_index(self, context):
    ensure_state(context)


# --------------------------------------------------------------------------
# 操作符
# --------------------------------------------------------------------------

class HS2ClothItem(bpy.types.PropertyGroup):
    label: StringProperty()
    value: StringProperty()
    sub: StringProperty()


class HS2_OT_cloth_scan(bpy.types.Operator):
    bl_idname = "hs2.cloth_scan"
    bl_label = "重新扫描衣服"

    def execute(self, context):
        game_dir = api.game_dir()
        if not game_dir:
            self.report({"ERROR"}, "先设置 HS2 游戏目录")
            return {"CANCELLED"}
        try:
            assets = ops.get_assets(game_dir)
            recs = assets.clothes_catalog(rebuild=True)
        except Exception as e:
            _set_status(context.scene, "扫描失败：%s" % e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _install(recs)
        _set_status(context.scene, "衣服：%d 件" % len(recs))
        refresh(context)
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_cloth_wear(bpy.types.Operator):
    bl_idname = "hs2.cloth_wear"
    bl_label = "穿上"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        char = api.active_character()
        if char is None:
            self.report({"ERROR"}, "先在「编辑对象」里选一个角色")
            return {"CANCELLED"}
        rec = current_record(context)
        if rec is None:
            self.report({"ERROR"}, "没有选中的衣服")
            return {"CANCELLED"}
        game_dir = api.game_dir()
        state = scene.hs2_cloth_state or ""
        try:
            skel = _skeleton(context)
            b = unityfs.Bundle(rec["bundle"])
            try:
                data, objs = clothes.wear(
                    game_dir, rec, state, char, skel,
                    preview=scene.hs2_cloth_preview, bundle=b)
            finally:
                try:
                    b.env = None
                except Exception:
                    pass
        except Exception as e:
            _set_status(scene, "穿上失败：%s" % e)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _set_status(scene, "穿上：%s (%s)，%d 个网格" % (
            rec.get("name"), state_label(context, scene.hs2_cloth_state),
            len(objs)))
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_cloth_state(bpy.types.Operator):
    bl_idname = "hs2.cloth_state"
    bl_label = "衣服状态"

    state: StringProperty(default="")

    def execute(self, context):
        context.scene.hs2_cloth_state = self.state
        _set_status(context.scene, "状态：%s" % state_label(context, self.state))
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_cloth_clear(bpy.types.Operator):
    bl_idname = "hs2.cloth_clear"
    bl_label = "脱下"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        char = api.active_character()
        if char is None:
            return {"CANCELLED"}
        slot = scene.hs2_cloth_slot
        try:
            if slot in ("", "ALL"):
                clothes.clear_character(char)
                n = -1
            else:
                n = clothes.remove_slot(char, slot)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _set_status(scene, "已脱下%s" % (
            "全部衣服" if n < 0 else "%s %d 件" % (rc.SLOT_LABELS.get(slot, slot), n)))
        _redraw(context)
        return {"FINISHED"}


class HS2_OT_cloth_export(bpy.types.Operator):
    bl_idname = "hs2.cloth_export"
    bl_label = "导出当前穿搭"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        game_dir = api.game_dir()
        if not game_dir:
            self.report({"ERROR"}, "先设置 HS2 游戏目录")
            return {"CANCELLED"}
        char = api.active_character()
        if char is None:
            self.report({"ERROR"}, "先在「编辑对象」里选一个角色")
            return {"CANCELLED"}
        if not ensure_loaded(context):
            self.report({"ERROR"}, "衣服目录还没读完，稍等再点一次")
            return {"CANCELLED"}
        jobs = worn_records(context)
        if not jobs:
            self.report({"ERROR"}, "这个角色身上没有穿衣服")
            return {"CANCELLED"}

        out = bpy.path.abspath(scene.hs2_cloth_out or "//hs2_clothes")
        wm = context.window_manager

        def progress(i, n, name):
            try:
                wm.progress_update(int(i * 100 / max(n, 1)))
            except Exception:
                pass
            _set_status(scene, "导出 %d/%d %s" % (i + 1, n, name))

        t0 = time.time()
        wm.progress_begin(0, 100)
        try:
            res = clothes.export_worn(
                game_dir, jobs, out, progress=progress,
                export_textures=scene.hs2_cloth_tex,
                flip_green=scene.hs2_cloth_flipg,
                skip_existing=scene.hs2_cloth_skip)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            _set_status(scene, "导出失败：%s" % e)
            return {"CANCELLED"}
        finally:
            wm.progress_end()

        msg = "当前穿搭：%d 个 FBX / %d 张贴图" % (
            res["ok"], res["textures"])
        if res["skipped"]:
            msg += "，跳过 %d" % res["skipped"]
        if res["failed"]:
            msg += "，失败 %d（看控制台）" % len(res["failed"])
        if res.get("warnings"):
            msg += "，贴图警告 %d" % res["warnings"]
        msg += "，用时 %.0fs" % (time.time() - t0)
        _set_status(scene, msg)
        self.report({"INFO"}, msg)
        _redraw(context)
        return {"FINISHED"}


CLASSES = (
    HS2ClothItem,
    HS2_OT_cloth_scan,
    HS2_OT_cloth_wear,
    HS2_OT_cloth_state,
    HS2_OT_cloth_clear,
    HS2_OT_cloth_export,
)


def scene_props():
    return {
        "hs2_cloth_slot": EnumProperty(
            name="槽位", items=SLOT_ITEMS, default="top",
            update=_on_cloth_filter),
        "hs2_cloth_filter": StringProperty(
            name="搜索", default="", update=_on_cloth_filter),
        "hs2_cloth_state": StringProperty(
            name="状态", default=""),
        "hs2_cloth_items": CollectionProperty(type=HS2ClothItem),
        "hs2_cloth_index": IntProperty(name="衣服", default=-1,
                                       update=_on_cloth_index),
        "hs2_cloth_out": StringProperty(
            name="输出目录", subtype="DIR_PATH", default=""),
        "hs2_cloth_tex": BoolProperty(name="贴图", default=True),
        "hs2_cloth_flipg": BoolProperty(name="翻转 G", default=False,
                                        description="法线贴图绿通道翻转（UE 一般不用勾）"),
        "hs2_cloth_skip": BoolProperty(name="跳过已存在", default=True,
                                       description="已经有同名 FBX 就跳过"),
        "hs2_cloth_preview": BoolProperty(name="材质预览", default=True,
                                          description="穿上时顺便加载主贴图；关掉更快"),
        "hs2_cloth_status": StringProperty(name="状态", default=""),
    }


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    for name, prop in scene_props().items():
        setattr(bpy.types.Scene, name, prop)


def unregister():
    for name in scene_props():
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
