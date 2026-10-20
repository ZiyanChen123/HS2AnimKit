"""Blender 操作符。"""

import os
import time

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import anim, bdata, handpose, mmdrig, retarget, rig, vmd as vmdlib
from .ripper import GameAssets
from .ripper import mesh as _mesh

_ASSETS = {}
STATE = {}

ARM_NAME = "HS2_Default"
BODY_NAME = "HS2_Body"
HEAD_NAME = "HS2_Head"


# --------------------------------------------------------------------------
# 环境
# --------------------------------------------------------------------------

def get_assets(game_dir):
    key = os.path.abspath(game_dir)
    if key not in _ASSETS:
        _ASSETS[key] = GameAssets(key)
    return _ASSETS[key]


def addon_prefs(context):
    try:
        return context.preferences.addons[__package__].preferences
    except Exception:
        return None


def detect_game_dir():
    """猜游戏目录：环境变量 → 插件所在目录往上找 → 几个常见安装位置。"""
    env = os.environ.get("HS2_GAME_DIR")
    if env and os.path.isfile(os.path.join(env, "abdata", "chara", "oo_base.unity3d")):
        return env.replace("\\", "/")
    cands = []
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cur = here
    for _ in range(4):
        cands.append(cur)
        cur = os.path.dirname(cur)
    for drive in ("D", "E", "C", "F"):
        cands += ["%s:/game/HS2" % drive, "%s:/HS2" % drive,
                  "%s:/game/HoneySelect2" % drive]
    for c in cands:
        if os.path.isfile(os.path.join(c, "abdata", "chara", "oo_base.unity3d")):
            return c.replace("\\", "/")
    return ""


def game_dir_of(context):
    p = addon_prefs(context)
    d = p.game_dir if p and p.game_dir else ""
    d = bpy.path.abspath(d) if d else ""
    if d and os.path.isfile(os.path.join(d, "abdata", "chara", "oo_base.unity3d")):
        return d
    auto = detect_game_dir()
    if auto and p is not None and not p.game_dir:
        p.game_dir = auto
    return auto or d


def current_arm():
    arm = bpy.data.objects.get(ARM_NAME)
    if arm is not None and arm.type == "ARMATURE":
        return arm
    ob = bpy.context.view_layer.objects.active
    if ob is not None and ob.type == "ARMATURE" and ob.data.bones.get("cf_J_Root"):
        return ob
    return None


def _tag_redraw(context):
    try:
        for area in context.screen.areas:
            area.tag_redraw()
    except Exception:
        pass


def _remove_object(name):
    ob = bpy.data.objects.get(name)
    if ob is not None:
        data = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if data is not None and getattr(data, "users", 0) == 0:
            if isinstance(data, bpy.types.Armature):
                bpy.data.armatures.remove(data)
            elif isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)


# --------------------------------------------------------------------------
# 加载角色
# --------------------------------------------------------------------------

class HS2_OT_detect_dir(bpy.types.Operator):
    """自动找游戏目录（插件所在盘 + 常见安装位置）"""
    bl_idname = "hs2.detect_dir"
    bl_label = "自动检测游戏目录"

    def execute(self, context):
        p = addon_prefs(context)
        found = detect_game_dir()
        if not found:
            self.report({"ERROR"}, "没找到，请手动选 abdata 所在的目录")
            return {"CANCELLED"}
        if p is not None:
            p.game_dir = found
        self.report({"INFO"}, "游戏目录：%s" % found)
        _tag_redraw(context)
        return {"FINISHED"}


class HS2_OT_load_default(bpy.types.Operator):
    """加载 HS2 默认角色：默认骨架 + 素模（身体 + 头），完全没捏过的状态"""
    bl_idname = "hs2.load_default"
    bl_label = "1. 加载默认角色"
    bl_options = {"REGISTER", "UNDO"}

    cleanup: BoolProperty(name="清理已有 HS2 物体", default=True)
    include_head: BoolProperty(name="带头部", default=True,
                               description="从 fo_head_00 取头骨与头部网格")
    scale: FloatProperty(name="单位缩放", default=0.1, min=0.001, max=10.0)

    def execute(self, context):
        t0 = time.time()
        g = get_assets(game_dir_of(context))
        try:
            skel = g.skeleton()
            data = g.body_mesh()
        except Exception as e:
            self.report({"ERROR"}, "读取失败：%s" % e)
            return {"CANCELLED"}

        head = None
        if self.include_head:
            try:
                head = g.head()
            except Exception as e:
                self.report({"WARNING"}, "头部没读到：%s" % e)

        if self.cleanup:
            for name in (ARM_NAME, BODY_NAME, HEAD_NAME):
                _remove_object(name)

        extra = None
        if head:
            extra = [{"name": b["name"], "parent": b["parent"],
                      "world": b["world"], "deform": b["deform"]}
                     for b in head["bones"]]

        arm, names = bdata.build_armature(skel, name=ARM_NAME,
                                          scale=self.scale, extra=extra)
        body = bdata.build_mesh(data, skel, names, arm, name=BODY_NAME,
                                scale=self.scale)

        head_obj = None
        if head:
            hd = dict(head["mesh"])
            hd["group_names"] = hd.get("bone_names") or []
            head_obj = bdata.build_mesh(hd, skel, names, arm, name=HEAD_NAME,
                                        scale=self.scale,
                                        xform=head["attach"])

        STATE.update(names=names, skel=skel, arm=arm, mesh=body, head=head_obj,
                     game=g, scale=self.scale)

        for ob in list(bpy.context.selected_objects):
            ob.select_set(False)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm

        total = len(arm.data.bones)
        self.report({"INFO"}, "骨架 %d 骨（含面部 %d），身体 %d 顶点，头 %s，用时 %.1fs"
                    % (total, total - len(names),
                       len(data["vertices"]),
                       ("%d 顶点" % len(head["mesh"]["vertices"])) if head else "无",
                       time.time() - t0))
        return {"FINISHED"}


class HS2_OT_add_head(bpy.types.Operator):
    """给现有骨架补上头部（骨骼 + 网格）"""
    bl_idname = "hs2.add_head"
    bl_label = "补头部"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "没有 HS2 骨架")
            return {"CANCELLED"}
        if arm.data.bones.get("cf_J_FaceRoot"):
            self.report({"INFO"}, "已经有头了")
            return {"CANCELLED"}

        g = get_assets(game_dir_of(context))
        try:
            head = g.head()
        except Exception as e:
            self.report({"ERROR"}, "头部读取失败：%s" % e)
            return {"CANCELLED"}

        scale = float(arm.get("hs2_scale", 0.1))
        extra = [{"name": b["name"], "parent": b["parent"],
                  "world": b["world"], "deform": b["deform"]}
                 for b in head["bones"]]

        # 复用 build_armature 的额外骨骼逻辑：借一份空骨架太重，这里单独插
        bdata.add_extra_bones(arm, extra, scale)

        for ob in list(bpy.context.selected_objects):
            ob.select_set(False)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm

        hd = dict(head["mesh"])
        hd["group_names"] = hd.get("bone_names") or []
        bdata.build_mesh(hd, STATE.get("skel"), STATE.get("names") or [], arm,
                         name=HEAD_NAME, scale=scale, xform=head["attach"])
        self.report({"INFO"}, "头部：%d 根骨，%d 顶点"
                    % (len(extra), len(head["mesh"]["vertices"])))
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 动作
# --------------------------------------------------------------------------

class HS2_OT_scan_clips(bpy.types.Operator):
    """扫描游戏里的全部动作（第一次会慢些，之后有缓存）"""
    bl_idname = "hs2.scan_clips"
    bl_label = "扫描动作库"

    def execute(self, context):
        g = get_assets(game_dir_of(context))
        wm = context.window_manager

        def progress(i, total, name):
            wm.progress_update(int(i * 100 / max(total, 1)))

        wm.progress_begin(0, 100)
        t0 = time.time()
        try:
            idx = g.clip_index(rebuild=True, progress=progress)
        except Exception as e:
            wm.progress_end()
            self.report({"ERROR"}, "扫描失败：%s" % e)
            return {"CANCELLED"}
        wm.progress_end()
        STATE["index"] = idx
        clear_enum_cache()
        good = sum(1 for v in idx.values() if _fit_of(v) >= MIN_FIT)
        self.report({"INFO"}, "共 %d 个动作，其中 %d 个适配这副骨架，用时 %.0fs"
                    % (len(idx), good, time.time() - t0))
        _tag_redraw(context)
        return {"FINISHED"}


# 骨骼绑定匹配率低于这个值就当成「不是给这副骨架的动作」藏起来
MIN_FIT = 0.5


def _fit_of(entry):
    if isinstance(entry, dict):
        return float(entry.get("fit", 1.0))
    return 1.0


def _files_of(entry):
    if isinstance(entry, dict):
        return entry.get("files") or []
    return entry or []


def clip_list(context):
    """当前过滤条件下的动作名列表（排序）。"""
    try:
        g = STATE.get("game") or get_assets(game_dir_of(context))
        idx = g.clip_index()
    except Exception:
        return []
    if not idx:
        return []
    names = sorted(idx.keys())

    # 恒定滤掉「不是给这个骨架的」：男角 / 道具 / 椅子 / 双人动作
    names = [n for n in names if _fit_of(idx.get(n)) >= MIN_FIT]

    scn = getattr(context, "scene", None)
    filt = (getattr(scn, "hs2_clip_filter", "") or "").strip().lower() if scn else ""
    if filt:
        names = [n for n in names if filt in n.lower()]
    return names


def clip_index(context):
    """当前滑条位置对应的下标。"""
    names = clip_list(context)
    if not names:
        return 0, []
    if len(names) == 1:
        return 0, names
    scn = context.scene
    i = int(round(float(getattr(scn, "hs2_clip_pos", 0.0)) * (len(names) - 1)))
    return max(0, min(i, len(names) - 1)), names


def current_clip_name(context):
    """当前选中的动作名（滑条没动过时也能拿到，不会返回空串）。"""
    scn = getattr(context, "scene", None)
    name = (getattr(scn, "hs2_clip_name", "") or "").strip() if scn else ""
    if name:
        return name
    _i, names = clip_index(context)
    return names[0] if names else ""


# --------------------------------------------------------------------------
# 自动加载：拖动滑条会连续触发 update，用定时器防抖，停手后才真的读动作
# --------------------------------------------------------------------------

_TIMER = {"fn": None}


def _clear_pending():
    fn = _TIMER.get("fn")
    if fn is not None:
        try:
            bpy.app.timers.unregister(fn)
        except Exception:
            pass
        _TIMER["fn"] = None


def _do_clip_load(name):
    """真正执行导入。在定时器里跑，只能拿 bpy.context，不能用传入的 context。"""
    try:
        import bpy as _bpy
        scn = getattr(_bpy.context, "scene", None)
        if scn is None:
            return
        arm = current_arm()
        if arm is None:
            return
        fps = float(getattr(scn.render, "fps", 30.0)) or 30.0
        _apply_clip(_bpy.context, arm, name, fps, follow=True)
        print("[HS2] 自动加载：%s" % name)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("[HS2] 自动加载失败 %s：%s" % (name, e))


def schedule_clip_load(name, delay=0.35):
    """排一次导入；连续调用只会保留最后一次（防抖）。"""
    _clear_pending()
    name = (name or "").strip()
    if not name:
        return

    def run():
        _TIMER["fn"] = None
        _do_clip_load(name)
        return None

    _TIMER["fn"] = run
    try:
        bpy.app.timers.register(run, first_interval=delay)
    except Exception as e:
        print("[HS2] 定时器注册失败：%s" % e)
        _TIMER["fn"] = None
        _do_clip_load(name)


class HS2_OT_show_in_front(bpy.types.Operator):
    """让控制器画在网格前面（默认已开，老场景可能没有）"""
    bl_idname = "hs2.show_in_front"
    bl_label = "控制器显示在网格前面"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            return {"CANCELLED"}
        arm.show_in_front = True
        for nm in (rig.COLL_CTRL, rig.COLL_IK):
            coll = arm.data.collections_all.get(nm)
            if coll is not None:
                coll.is_visible = True
        self.report({"INFO"}, "已打开「在前面」")
        return {"FINISHED"}


def clip_items(self, context):
    try:
        g = STATE.get("game") or get_assets(game_dir_of(context))
        idx = g.clip_index()
    except Exception:
        return [("", "请先扫描动作库", "")]
    if not idx:
        return [("", "没有扫描到动作", "")]

    filt = ""
    scn = getattr(context, "scene", None)
    if scn is not None:
        filt = (getattr(scn, "hs2_clip_filter", "") or "").strip().lower()
    names = sorted(idx.keys())
    if filt:
        names = [n for n in names if filt in n.lower()]
    if not names:
        return [("", "没有匹配的动作", "")]
    return [(n, n, ",".join(_files_of(idx[n]))) for n in names[:400]]


# --------------------------------------------------------------------------
# 下拉框用的枚举（带缓存：1261 个动作每次都重建会很卡）
# --------------------------------------------------------------------------

_ENUM = {"key": None, "items": []}


def clip_enum_items(self, context):
    """动作下拉框的候选项。

    EnumProperty 在每次绘制 UI 时都会回调这里，所以必须缓存 ——
    不然 1261 个字符串每帧重排，面板会明显发卡。
    """
    names = clip_list(context)
    scn = getattr(context, "scene", None)
    cur = (getattr(scn, "hs2_clip_name", "") or "") if scn else ""
    key = (len(names), names[0] if names else "", names[-1] if names else "", cur)
    if _ENUM["key"] == key:
        return _ENUM["items"]

    items = []
    if cur and cur not in names:
        # 当前选的名字被过滤掉了，也要能显示出来
        items.append((cur, cur, ""))
    items.extend([(n, n, "") for n in names])
    if not items:
        items = [("", "（先扫描动作库）", "")]
    _ENUM["key"] = key
    _ENUM["items"] = items
    return items


def clear_enum_cache():
    _ENUM["key"] = None
    _ENUM["items"] = []


def mmdcfg_items(self, context):
    """可用的 MMDD 角色配置（下拉框）。"""
    from . import mmdcfg
    out = mmdcfg.list_configs()
    if not out:
        return [("", "（没找到 _ref/mmdd/mmdd 下的配置）", "")]
    items = [(p, n, p) for n, p in out]
    # 默认配置（MMDD TDA v2.0）排最前
    def key(it):
        return (0 if it[1] == mmdcfg.DEFAULT_INI else 1, it[1])
    items.sort(key=key)
    return items


def _names_for(arm, skel):
    """骨架下标 -> Blender 骨骼名（和 bdata._unique_names 的去重规则一致）。

    重新打开 .blend 时 STATE 是空的，所以不能依赖内存里的缓存。
    """
    used = {}
    out = []
    for b in skel.bones:
        n = b.name or "bone"
        if n in used:
            used[n] += 1
            n = "%s_%03d" % (n, used[n])
        else:
            used[n] = 0
        out.append(n if n in arm.data.bones else None)
    return out


def _apply_clip(context, arm, name, fps, follow=True):
    """把游戏里的动作装进当前骨架。返回 (action, info, keyed)。"""
    g = get_assets(game_dir_of(context))
    skel = STATE.get("skel") or g.skeleton()
    names = STATE.get("names") or _names_for(arm, skel)
    STATE["skel"] = skel
    STATE["names"] = names

    clip = g.load_clip(name, fps=fps)
    act, info = anim.apply_clip(arm, skel, names, clip, scale=STATE.get("scale"))

    scn = context.scene
    scn.frame_start = 1
    scn.frame_end = max(1, info["frames"])
    scn.render.fps = int(round(info["fps"]))
    scn.frame_set(1)

    keyed = 0
    if follow and rig.has_rig(arm):
        keyed = rig.key_targets(arm, 1, info["frames"])
        scn.frame_set(1)

    STATE["action"] = act
    STATE["clip"] = clip
    return act, info, keyed


class HS2_OT_clip_step(bpy.types.Operator):
    """上一个 / 下一个动作"""
    bl_idname = "hs2.clip_step"
    bl_label = "切换动作"
    bl_options = {"REGISTER"}

    delta: IntProperty(default=1)

    def execute(self, context):
        names = clip_list(context)
        if not names:
            return {"CANCELLED"}
        scn = context.scene
        auto = bool(getattr(scn, "hs2_clip_auto", True))
        if len(names) <= 1:
            scn.hs2_clip_pos = 0.0
            scn.hs2_clip_name = names[0]
            if auto and current_arm() is not None:
                schedule_clip_load(names[0], delay=0.05)
            return {"FINISHED"}
        i = int(round(float(scn.hs2_clip_pos) * (len(names) - 1)))
        i = max(0, min(i + self.delta, len(names) - 1))
        scn.hs2_clip_pos = i / float(len(names) - 1)
        scn.hs2_clip_name = names[i]
        # 一次点击 = 一次加载，不需要再点「导入」
        if auto and current_arm() is not None:
            schedule_clip_load(names[i], delay=0.05)
        return {"FINISHED"}


class HS2_OT_toggle_jointfix(bpy.types.Operator):
    """开关关节补偿（dam 骨随关节按比例旋转）"""
    bl_idname = "hs2.toggle_jointfix"
    bl_label = "关节补偿"
    bl_options = {"REGISTER", "UNDO"}

    enable: BoolProperty(name="开启", default=True)

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            return {"CANCELLED"}
        arm.hs2_jointfix = self.enable      # 属性回调里会去开关约束
        self.report({"INFO"}, "关节补偿 %s（改关节时 dam 骨会跟着转）"
                    % ("开" if self.enable else "关"))
        return {"FINISHED"}


class HS2_OT_import_clip(bpy.types.Operator):
    """从游戏目录导入一个动作，生成 Blender Action（可播放、可编辑）"""
    bl_idname = "hs2.import_clip"
    bl_label = "2. 导入动作"
    bl_options = {"REGISTER", "UNDO"}

    clip_name: StringProperty(name="动作名")
    fps: FloatProperty(name="帧率", default=30.0, min=1.0, max=120.0)
    follow: BoolProperty(name="控制器跟随动作", default=True,
                         description="导入后把 IK 控制器钉到这段动作上")

    def invoke(self, context, event):
        if not self.clip_name:
            self.clip_name = current_clip_name(context)
        if self.fps <= 0.0:
            self.fps = float(getattr(context.scene.render, "fps", 30.0)) or 30.0
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, context):
        _i, names = clip_index(context)
        self.layout.label(text="共 %d 个动作（滑条/◀▶ 切换会自动加载）" % len(names)
                          if names else "先扫描动作库",
                          icon="INFO")
        self.layout.prop(self, "clip_name", text="动作名")
        self.layout.prop(self, "fps")
        if rig.has_rig(current_arm()):
            self.layout.prop(self, "follow")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        handpose.release(arm)

        name = (self.clip_name or "").strip()
        if not name:
            # 没指定就用滑条当前选中的那个
            name = current_clip_name(context)
        if not name:
            self.report({"ERROR"}, "没有指定动作名（先点「扫描动作库」）")
            return {"CANCELLED"}

        t0 = time.time()
        try:
            act, info, keyed = _apply_clip(context, arm, name, self.fps, self.follow)
        except Exception as e:
            self.report({"ERROR"}, "读取动作失败：%s" % e)
            return {"CANCELLED"}

        self.report({"INFO"}, "%s：%d 帧 / %d 根骨%s，用时 %.1fs"
                    % (name, info["frames"], info["bone_tracks"],
                       ("，控制器 %d 帧" % keyed) if keyed else "",
                       time.time() - t0))
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 外部动作：VMD / Mixamo
# --------------------------------------------------------------------------

def _after_import(context, arm, act, frames, follow):
    """导入后收尾：设帧范围 + 可选控制器打帧。

    （原「贴地」逻辑已彻底删除 —— MMDD 插件里没有这个功能，它 100% 忠实
    还原 VMD 数据，脚在哪儿就是哪儿。）
    """
    scn = context.scene
    scn.frame_start = 1
    scn.frame_end = max(1, frames)
    scn.frame_set(1)
    keyed = 0
    if follow and rig.has_rig(arm):
        keyed = rig.key_targets(arm, 1, frames)
        scn.frame_set(1)
    STATE["action"] = act
    return keyed


class HS2_OT_import_vmd(bpy.types.Operator, ImportHelper):
    """导入 MMD 动作（.vmd），重定向到当前 HS2 骨架"""
    bl_idname = "hs2.import_vmd"
    bl_label = "导入 VMD 动作"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".vmd"
    filter_glob: StringProperty(default="*.vmd", options={"HIDDEN"})

    scale: FloatProperty(name="单位换算 (米/MMD单位)", default=0.0832,
                         min=0.005, max=0.5, precision=4,
                         description="MMDD: AxisScale 0.832 × 角色缩放 0.1 = 0.0832。"
                                     "旧默认 0.08 会让位移和足 IK 目标短 4%")
    chara: EnumProperty(
        name="角色配置",
        description="MMDD 的角色骨骼配置（决定虚拟 MMD 骨架怎么搭）",
        items=lambda self, context: mmdcfg_items(self, context))
    use_dummy: BoolProperty(
        name="用虚拟 MMD 骨架（和 MMDD 一致）", default=True,
        description="在 HS2 骨上搭一棵虚拟 MMD 骨架，VMD 驱动它再同步回来。"
                    "MMD 的层级/腰キャンセル/扭转分散都原样保留，效果最接近 MMD；"
                    "关掉则退回「直接映射」，动作更轻量但躯干会走样")
    use_ik: BoolProperty(
        name="启用足部 IK", default=True,
        description="按 ini 的 BoneIKSet 用两骨链解析解（余弦定理 + CCD）"
                    "解算膝盖，脚踝精确落到足ＩＫ 目标。"
                    "有足ＩＫ 帧的 VMD 必开（腿的 FK 数据只是占位）；"
                    "关掉则 FK 直驱")
    joint_fix: BoolProperty(
        name="关节校正（dam 骨）", default=True,
        description="VMD 没有 dam 骨的烘焙曲线，默认打开膝/肘/臀内侧校正；"
                    "导入后也可以在「控制器」面板里随时开关")
    follow: BoolProperty(name="控制器跟随", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        self.layout.prop(self, "chara")
        self.layout.prop(self, "use_dummy")
        if self.use_dummy:
            self.layout.prop(self, "use_ik")
            self.layout.prop(self, "joint_fix")
        self.layout.prop(self, "scale")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        handpose.release(arm)
        try:
            data = vmdlib.parse(self.filepath)
        except Exception as e:
            self.report({"ERROR"}, "VMD 解析失败：%s" % e)
            return {"CANCELLED"}
        if not data["bones"]:
            self.report({"ERROR"}, "VMD 里没有骨骼帧")
            return {"CANCELLED"}

        t0 = time.time()
        fps = context.scene.render.fps
        name = os.path.basename(self.filepath)

        if self.use_dummy:
            act, frames, ntrack = self._via_dummy(context, arm, data, fps, name)
        else:
            act, frames, ntrack = self._direct(context, arm, data, fps, name)
        if act is None:
            return {"CANCELLED"}

        keyed = _after_import(context, arm, act, frames, self.follow)
        self.report({"INFO"}, "%s：%d 帧 / %d 骨（%s），用时 %.1fs"
                    % (data.get("model") or "VMD", frames, ntrack,
                       "虚拟骨架" if self.use_dummy else "直接映射",
                       time.time() - t0))
        return {"FINISHED"}

    # ---- 方式 A：虚拟 MMD 骨架（复刻 MMDD）----
    def _via_dummy(self, context, arm, data, fps, name):
        from . import mmdcfg, mmdrig

        cfg = mmdcfg.CharaConfig(self.chara or None)
        if not cfg.bones:
            self.report({"ERROR"}, "角色配置读不出来（%s）" % cfg.path)
            return None, 0, 0

        # 1) VMD 里所有骨的逐帧旋转/位置（不映射，原样保留）
        raw = vmdlib.all_tracks(data, fps=fps)
        if not raw:
            self.report({"ERROR"}, "VMD 里没有可用的骨骼帧")
            return None, 0, 0

        # 2) 建虚拟骨架 + 同步。
        # 足 IK 用 write_curves 里的**解析解**（余弦定理 + CCD，直接写 fcurve），
        # 不再用 Blender IK 约束 —— 两套 IK 会打架（约束把解析结果拉歪，
        # 膝盖 51° vs 1° 就是这么来的）。
        # bo 偏移是 HS2 prefab 单位（×骨架缩放），不是 MMD 单位
        scene_scale = float(arm.get("hs2_scale", 0.1) or 0.1)
        stats = mmdrig.build(arm, cfg, unit=scene_scale)
        nsync = mmdrig.sync(arm, cfg)

        # 2.5) dam 骨约束（膝盖后/肘后等，随主骨按比例转）。
        # MMDD 官方不做这个（BoneWeight 里 dam 条目全注释），但膝盖/手肘
        # 弯曲时 dam 骨不跟随会导致体积塌陷 —— 实测比例已验证正确。
        from . import rig as _rig
        nfix = _rig._build_correctives(arm)
        # VMD 没有 dam 骨的烘焙曲线，默认把校正打开（导入对话框可关）。
        # 注意：游戏动作导入不走这里，仍保持自己的 dam 曲线，不受影响。
        use_fix = bool(getattr(self, "joint_fix", True))
        try:
            arm.hs2_jointfix = use_fix
        except Exception:
            arm["hs2_jointfix"] = 1 if use_fix else 0
        _rig.set_joint_fix(arm, use_fix)

        # 3) 写动作
        tilt = bool(getattr(context.scene, "hs2_arm_tilt", True))
        act, frames = mmdrig.write_curves(
            arm, cfg, raw, action_name="HS2_VMD_" + name,
            unit=self.scale, arm_tilt=tilt, vmd_data=data)
        bpy.context.view_layer.update()
        print("[MMDD] %s：虚拟骨 %d，同步 %d 条，帧 %d"
              % (cfg.name, stats["bones"], nsync, frames))
        return act, frames, len(raw)

    # ---- 方式 B：直接映射（老路径）----
    def _direct(self, context, arm, data, fps, name):
        mmdrig.clear(arm)      # 清掉之前虚拟骨架的残留
        tracks, frames = vmdlib.to_hs2(data, fps=fps, scale=self.scale,
                                       arm_deg=35.0)
        if not tracks:
            self.report({"ERROR"}, "没有一个 MMD 骨骼能对上 HS2")
            return None, 0, 0
        act, _n = retarget.write_curves(
            arm, tracks, action_name="HS2_VMD_" + name, fps=fps,
            frame_count=frames)
        return act, frames, len(tracks)


class HS2_OT_import_mixamo(bpy.types.Operator, ImportHelper):
    """导入带皮肤的 Mixamo FBX，把动作重定向到当前 HS2 骨架"""
    bl_idname = "hs2.import_mixamo"
    bl_label = "导入 Mixamo 动作"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    with_fingers: BoolProperty(name="含手指", default=True)
    follow: BoolProperty(name="控制器跟随", default=True)
    keep_source: BoolProperty(name="保留源骨架", default=False)

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        handpose.release(arm)

        # 清掉上一次 VMD 虚拟骨架留下的虚拟骨和 MMDD 约束，
        # 否则那些 world→world 的 COPY_ROTATION 会把 Mixamo 的动画整个盖掉。
        mmdrig.clear(arm)

        before = {o.name for o in bpy.data.objects}
        try:
            bpy.ops.import_scene.fbx(filepath=self.filepath)
        except Exception as e:
            self.report({"ERROR"}, "FBX 导入失败：%s" % e)
            return {"CANCELLED"}
        new_objs = [o for o in bpy.data.objects if o.name not in before]
        src = retarget.find_source_armature(new_objs)
        if src is None:
            self.report({"ERROR"}, "这个 FBX 里没有 Mixamo 骨架")
            return {"CANCELLED"}

        scn = context.scene
        act_src = src.animation_data.action if src.animation_data else None
        if act_src is None:
            self.report({"ERROR"}, "FBX 里没有动画")
            return {"CANCELLED"}
        f0, f1 = act_src.frame_range
        f0, f1 = int(round(f0)), int(round(f1))

        src_name = src.name
        t0 = time.time()
        tracks, frames = retarget.mixamo_tracks(src, arm, f0, f1,
                                                with_fingers=self.with_fingers)
        act, n = retarget.write_curves(arm, tracks, action_name="HS2_Mixamo_" + src.name,
                                       fps=scn.render.fps, frame_count=frames)
        keyed = _after_import(context, arm, act, frames, self.follow)

        if not self.keep_source:
            for ob in new_objs:
                if ob.name not in bpy.data.objects:
                    continue
                data = ob.data
                bpy.data.objects.remove(ob, do_unlink=True)
                if isinstance(data, bpy.types.Armature) and data.users == 0:
                    bpy.data.armatures.remove(data)
                elif isinstance(data, bpy.types.Mesh) and getattr(data, "users", 0) == 0:
                    bpy.data.meshes.remove(data)

        self.report({"INFO"}, "%s：%d 帧 / %d 骨，用时 %.1fs"
                    % (src_name, frames, len(tracks), time.time() - t0))
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


# --------------------------------------------------------------------------
# 工作室手势（cf_anmShapeHand）
# --------------------------------------------------------------------------

class HS2_OT_apply_hand_pose(bpy.types.Operator):
    """应用工作室手势（21 种，直接写手指骨）"""
    bl_idname = "hs2.apply_hand_pose"
    bl_label = "应用手势"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(
        name="目标", default="BOTH",
        items=(("L", "左手", ""), ("R", "右手", ""),
               ("BOTH", "双手", ""), ("RESET", "还原", "")))
    keyframe: BoolProperty(name="在当前帧打关键帧", default=True)

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        g = game_dir_of(context)
        if not g:
            self.report({"ERROR"}, "请先设置游戏目录")
            return {"CANCELLED"}
        try:
            poses = handpose.load_poses(g)
        except Exception as e:
            self.report({"ERROR"}, "读取手势失败：%s" % e)
            return {"CANCELLED"}
        scn = context.scene
        if self.mode == "RESET":
            n = handpose.apply_pose(arm, -1, side="BOTH",
                                    keyframe=self.keyframe, poses=poses)
            msg = "已还原手势"
        else:
            targets = [self.mode]
            if self.mode == "BOTH":
                targets = ["L", "R"]
            n = 0
            for t in targets:
                idx = int(scn.hs2_hand_L if t == "L" else scn.hs2_hand_R)
                n += handpose.apply_pose(arm, idx, side=t,
                                         keyframe=self.keyframe, poses=poses)
            msg = "按当前选择写入"
        if n == 0:
            self.report({"WARNING"}, "没有写入任何手指骨")
            return {"CANCELLED"}
        self.report({"INFO"}, "%s：写入 %d 根手指骨%s"
                    % (msg, n, "（已打帧）" if self.keyframe else ""))
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 控制器
# --------------------------------------------------------------------------

class HS2_OT_add_rig(bpy.types.Operator):
    """生成 IK/FK 控制层（可重复执行：会先清掉旧的再重建）"""
    bl_idname = "hs2.add_rig"
    bl_label = "3. 生成控制器"
    bl_options = {"REGISTER", "UNDO"}

    follow: BoolProperty(name="控制器跟随当前动作", default=True)
    fingers: BoolProperty(name="含手指控制器", default=False,
                          description="每根手指 3 个 FK 控制器（30 个），默认收起")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        handpose.release(arm)

        for ob in list(bpy.context.selected_objects):
            ob.select_set(False)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm

        t0 = time.time()
        try:
            stats = rig.build(arm, key_animation=self.follow,
                              fingers=self.fingers)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, "生成失败：%s" % e)
            return {"CANCELLED"}

        self.report({"INFO"},
                    "%d 控制器 / %d 约束 / %d 补偿%s，用时 %.1fs"
                    % (stats["controls"], stats["constraints"],
                       stats["correctives"],
                       ("，极向角 %s" % stats["pole"]) if stats["pole"] else "",
                       time.time() - t0))
        _tag_redraw(context)
        return {"FINISHED"}


class HS2_OT_clear_rig(bpy.types.Operator):
    """删掉控制层，只留游戏骨架"""
    bl_idname = "hs2.clear_rig"
    bl_label = "清除控制器"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            return {"CANCELLED"}
        handpose.release(arm)
        rig.clear(arm)
        self.report({"INFO"}, "已清除控制层")
        return {"FINISHED"}


class HS2_OT_toggle_ik(bpy.types.Operator):
    """开关 IK（走 ik 属性，可以逐肢体控制）"""
    bl_idname = "hs2.toggle_ik"
    bl_label = "开关 IK"
    bl_options = {"REGISTER", "UNDO"}

    enable: BoolProperty(default=True)

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            self.report({"ERROR"}, "还没有控制器")
            return {"CANCELLED"}
        rig.set_ik(arm, self.enable)
        self.report({"INFO"}, "%s IK" % ("启用" if self.enable else "关闭"))
        return {"FINISHED"}


class HS2_OT_snap_ik(bpy.types.Operator):
    """把控制器吸附到当前姿势（先吸附再开 IK，姿势不会跳）"""
    bl_idname = "hs2.snap_ik"
    bl_label = "控制器吸附当前姿势"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            return {"CANCELLED"}
        rig.snap_ik_to_fk(arm)
        self.report({"INFO"}, "控制器已吸附")
        return {"FINISHED"}


class HS2_OT_snap_fk(bpy.types.Operator):
    """把 IK 解算结果写回 FK 骨骼（切回 FK 不跳）"""
    bl_idname = "hs2.snap_fk"
    bl_label = "IK 结果写回 FK"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            return {"CANCELLED"}
        n = rig.snap_fk_to_ik(arm, key=True)
        self.report({"INFO"}, "%d 根骨已写回，IK 已关闭" % n)
        return {"FINISHED"}


class HS2_OT_key_targets(bpy.types.Operator):
    """把控制器重新钉到整段动作上（改完动作后重新对一次）"""
    bl_idname = "hs2.key_targets"
    bl_label = "控制器跟随整段动作"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None or not rig.has_rig(arm):
            return {"CANCELLED"}
        if arm.animation_data is None or arm.animation_data.action is None:
            self.report({"ERROR"}, "当前没有动作")
            return {"CANCELLED"}
        scn = context.scene
        n = rig.key_targets(arm, scn.frame_start, scn.frame_end)
        self.report({"INFO"}, "已重钉 %d 帧" % n)
        return {"FINISHED"}


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def _bake_to_fk(arm, context):
    """把当前动作（含 IK 结果）烘成纯 FK。返回 (帧数, 骨骼数)。

    只烘「有动画的骨 + IK 链上的骨」：整副骨架 693 根全打帧会让 FBX 大一倍，
    而且大部分骨本来就不动。
    """
    import math  # noqa: F401  (nla.bake 需要 context 里没有的东西也无妨)

    scn = context.scene
    for ob in list(bpy.context.selected_objects):
        ob.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    mode = bpy.context.object.mode
    if mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")

    rig.set_ik(arm, True)
    bpy.ops.pose.select_all(action="DESELECT")

    deform = set(rig.deform_bone_names(arm))
    wanted = set()
    act = arm.animation_data.action if arm.animation_data else None
    if act is not None:
        from . import compat
        for fc in (compat.bag(act, arm) or []):
            dp = fc.data_path
            if dp.startswith('pose.bones["'):
                wanted.add(dp[12:dp.index('"]')])
    # IK 链上的骨即使没动画也要烘（手/脚可能只靠 IK 摆位）
    for side, ch in rig.LEG.items():
        wanted.update([ch["thigh"], ch["calf"], ch["roll"], ch["foot"], ch["toe"]])
    for side, ch in rig.ARM.items():
        wanted.update([ch["upper"], ch["lower"], ch["hand"]])
    # 带约束的变形骨全部要烘（Root/Hips/Head 上有叠加偏移）
    for pb in arm.pose.bones:
        if pb.constraints and pb.bone.use_deform:
            wanted.add(pb.name)

    sel = [n for n in wanted if n in deform]
    if len(sel) < 8:
        sel = list(deform)
    # Blender 5.x 移除了 Bone.select，只剩 PoseBone.select
    for pb in arm.pose.bones:
        want = pb.name in sel
        try:
            pb.select = want
        except Exception:
            pass
        try:
            pb.bone.select = want
        except Exception:
            pass

    bpy.ops.nla.bake(frame_start=scn.frame_start, frame_end=scn.frame_end,
                     only_selected=True, visual_keying=True,
                     clear_constraints=True, clear_parents=False,
                     use_current_action=True, bake_types={"POSE"})
    rig.set_ik(arm, False)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    arm["hs2_baked"] = 1
    return scn.frame_end - scn.frame_start + 1, len(sel)


class HS2_OT_bake_fk(bpy.types.Operator):
    """把当前动作（含 IK 结果）烘焙成纯 FK 关键帧"""
    bl_idname = "hs2.bake_fk"
    bl_label = "4. 烘焙为纯 FK"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        t0 = time.time()
        frames, bones = _bake_to_fk(arm, context)
        self.report({"INFO"}, "已烘焙 %d 帧 / %d 骨，用时 %.1fs"
                    % (frames, bones, time.time() - t0))
        return {"FINISHED"}


class HS2_OT_export_fbx(bpy.types.Operator, ExportHelper):
    """导出给游戏用的 FBX（骨架 + 可选蒙皮 + 动画）"""
    bl_idname = "hs2.export_fbx"
    bl_label = "5. 导出 FBX"
    filename_ext = ".fbx"
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    include_mesh: BoolProperty(name="带蒙皮网格", default=False,
                               description="只在需要看效果时勾上；纯动作导出更干净")
    deform_only: BoolProperty(name="只导出变形骨", default=True)
    all_bones: BoolProperty(name="所有骨都打帧", default=False,
                            description="关掉可以显著减小文件；有些引擎要求所有骨都有曲线")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        path = _export(arm, bpy.path.abspath(self.filepath),
                       self.include_mesh, self.deform_only, self.all_bones)
        self.report({"INFO"}, "已导出 %s" % path)
        return {"FINISHED"}


def _export(arm, path, include_mesh=False, deform_only=True, all_bones=False):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    if include_mesh:
        for name in (BODY_NAME, HEAD_NAME):
            ob = bpy.data.objects.get(name)
            if ob is not None:
                ob.select_set(True)
    bpy.context.view_layer.objects.active = arm

    bpy.ops.export_scene.fbx(
        filepath=path, use_selection=True,
        object_types={"ARMATURE", "MESH"},
        apply_scale_options="FBX_SCALE_ALL",
        use_mesh_modifiers=True, mesh_smooth_type="FACE",
        add_leaf_bones=False,
        use_armature_deform_only=deform_only,
        primary_bone_axis="Y", secondary_bone_axis="X",
        armature_nodetype="NULL",
        bake_anim=True, bake_anim_use_all_bones=all_bones,
        bake_anim_use_nla_strips=False, bake_anim_use_all_actions=False,
        bake_anim_force_startend_keying=True, bake_anim_simplify_factor=0.0,
        path_mode="COPY", embed_textures=False,
    )
    return path


class HS2_OT_batch_export(bpy.types.Operator):
    """批量导出：按名字过滤，一次导一排动作（原始 FK，不带控制器改动）"""
    bl_idname = "hs2.batch_export"
    bl_label = "批量导出动作"
    bl_options = {"REGISTER"}

    directory: StringProperty(name="输出目录", subtype="DIR_PATH", default="")
    filter: StringProperty(name="名字过滤", default="",
                           description="子串匹配；留空 = 全部")
    limit: bpy.props.IntProperty(name="最多导出", default=0, min=0,
                                 description="0 = 不限制")
    include_mesh: BoolProperty(name="带蒙皮网格", default=False)

    def invoke(self, context, event):
        if not self.directory:
            blend = bpy.data.filepath
            base = os.path.dirname(blend) if blend else os.path.expanduser("~")
            self.directory = os.path.join(base, "hs2_fbx")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "directory")
        col.prop(self, "filter")
        col.prop(self, "limit")
        col.prop(self, "include_mesh")
        col.label(text="导出的游戏原生动作（纯 FK），不含控制器上的改动",
                  icon="INFO")

    def execute(self, context):
        arm = current_arm()
        if arm is None:
            self.report({"ERROR"}, "请先「加载默认角色」")
            return {"CANCELLED"}
        out_dir = bpy.path.abspath(self.directory)
        if not out_dir:
            self.report({"ERROR"}, "没有输出目录")
            return {"CANCELLED"}
        os.makedirs(out_dir, exist_ok=True)

        g = get_assets(game_dir_of(context))
        idx = g.clip_index()
        names = sorted(idx.keys())
        if self.filter.strip():
            f = self.filter.strip().lower()
            names = [n for n in names if f in n.lower()]
        if self.limit:
            names = names[:self.limit]
        if not names:
            self.report({"ERROR"}, "没有匹配的动作")
            return {"CANCELLED"}

        had_rig = rig.has_rig(arm)
        if had_rig:
            rig.set_ik(arm, False)

        wm = context.window_manager
        wm.progress_begin(0, len(names))
        ok = 0
        failed = []
        t0 = time.time()
        try:
            for i, name in enumerate(names):
                wm.progress_update(i)
                try:
                    _apply_clip(context, arm, name, 30.0, follow=False)
                    safe = "".join(c if c not in '\\/:*?"<>|' else "_" for c in name)
                    _export(arm, os.path.join(out_dir, safe + ".fbx"),
                            self.include_mesh, True)
                    ok += 1
                except Exception as e:
                    failed.append(name)
                    print("[HS2] 批量导出失败", name, e)
                finally:
                    act = arm.animation_data.action if arm.animation_data else None
                    if act is not None:
                        bpy.data.actions.remove(act)
        finally:
            wm.progress_end()
            if had_rig:
                rig.set_ik(arm, True)

        msg = "导出 %d / %d 个动作，用时 %.0fs" % (ok, len(names), time.time() - t0)
        if failed:
            msg += "；失败 %d 个（见控制台）" % len(failed)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


CLASSES = (
    HS2_OT_detect_dir,
    HS2_OT_apply_hand_pose,
    HS2_OT_import_vmd,
    HS2_OT_import_mixamo,
    HS2_OT_load_default,
    HS2_OT_add_head,
    HS2_OT_scan_clips,
    HS2_OT_clip_step,
    HS2_OT_show_in_front,
    HS2_OT_toggle_jointfix,
    HS2_OT_import_clip,
    HS2_OT_add_rig,
    HS2_OT_clear_rig,
    HS2_OT_toggle_ik,
    HS2_OT_snap_ik,
    HS2_OT_snap_fk,
    HS2_OT_key_targets,
    HS2_OT_bake_fk,
    HS2_OT_export_fbx,
    HS2_OT_batch_export,
)


def _do_reload():
    """在操作符上下文之外执行重载（RNA 指针已释放，安全）。"""
    import sys

    import addon_utils

    pkg = __name__.rsplit(".", 1)[0]

    # 安全序列：先正规注销（RNA 类不能在注册状态下被 reload），
    # 再删掉模块缓存，最后 enable 全新导入。
    addon_utils.disable(pkg, default_set=False)
    for m in [m for m in list(sys.modules)
              if m == pkg or m.startswith(pkg + ".")]:
        del sys.modules[m]
    addon_utils.enable(pkg, default_set=True)
    print("[hs2] 插件代码已重载")
    return None                      # 不重复


class HS2_OT_reload(bpy.types.Operator):
    """重载插件代码（改完代码后点这个，不用重启 Blender）"""
    bl_idname = "hs2.reload"
    bl_label = "重载插件代码"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # 重载会注销本操作符自己的类 —— 不能在本体里直接做（RNA 指针
        # 还被 C 层拿着，report/返回都会段错误）。挂到 timer，
        # 等操作符完整返回后再执行。
        bpy.app.timers.register(_do_reload)
        self.report({"INFO"}, "正在重载插件代码…")
        return {"FINISHED"}


CLASSES = CLASSES + (HS2_OT_reload,)
