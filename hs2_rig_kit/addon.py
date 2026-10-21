"""Blender 插件部分：注册面板、偏好设置、操作符。"""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

from . import ops, ops2, rig, ui


def _ver_text():
    from . import bl_info
    mod = __import__(__package__, fromlist=["x"])
    f = getattr(mod, "__file__", "") or ""
    v = ".".join(str(x) for x in bl_info.get("version", (0,)))
    return "HS2 Anim Kit v%s · %s" % (v, os.path.dirname(os.path.dirname(f)) if f else "?")


def _on_game_dir(self, context):
    """换目录：把解包器缓存和面板列表都清掉，免得拿旧数据。"""
    ops._ASSETS.clear()
    ops.STATE.pop("game", None)
    ops.STATE.pop("index", None)
    try:
        from . import ops2
        ops2.invalidate_lists()
    except Exception:
        pass


class HS2Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    game_dir: StringProperty(
        name="HS2 游戏目录",
        subtype="DIR_PATH",
        default="",
        description="含 abdata/chara/oo_base.unity3d 的目录",
        update=_on_game_dir,
    )
    include_head: BoolProperty(
        name="加载默认角色时带头部",
        default=True,
        description="从 abdata/chara/00/fo_head_00.unity3d 取头骨和头部网格",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_dir")
        if not self.game_dir:
            auto = ops.detect_game_dir()
            if auto:
                layout.label(text="自动检测到：%s" % auto, icon="INFO")
        layout.prop(self, "include_head")
        layout.label(text="解包器已内置，不需要额外装 Python 包。", icon="INFO")

        layout.separator()
        row = layout.row(align=True)
        row.label(text=_ver_text(), icon="SCRIPT")
        row.operator("hs2.reload", text="重载插件代码", icon="FILE_REFRESH")
        layout.label(text="改过插件代码才需要重载；平时改动作不用。", icon="NONE")


# --------------------------------------------------------------------------
# 动作滑条：用 0~1 的进度值，长度随列表自动缩放
# --------------------------------------------------------------------------

_HAND_FALLBACK = [("-1", "无（还原）", "")]
_HAND_CACHE = {"key": None, "items": _HAND_FALLBACK}


def _hand_items(self, context):
    try:
        g = ops.game_dir_of(context)
        n = ops.handpose.pose_count(g)
        key = (g, n)
        if _HAND_CACHE["key"] != key:
            _HAND_CACHE["key"] = key
            _HAND_CACHE["items"] = ops.handpose.enum_items(g)
        return _HAND_CACHE["items"]
    except Exception:
        return _HAND_CACHE["items"]


def _hand_preview(scene, context, side):
    """下拉框一改就预览（不写关键帧）。"""
    arm = ops.current_arm()
    g = ops.game_dir_of(context)
    if arm is None or not g:
        return
    val = getattr(scene, "hs2_hand_L" if side == "L" else "hs2_hand_R")
    try:
        idx = int(val)
        poses = ops.handpose.load_poses(g)
        ops.handpose.apply_pose(arm, idx, side=side, keyframe=False,
                                poses=poses)
    except Exception:
        pass


def _on_hand_L(self, context):
    _hand_preview(self, context, "L")


def _on_hand_R(self, context):
    _hand_preview(self, context, "R")


def _on_jointfix(self, context):
    if self.type == "ARMATURE":
        rig.set_joint_fix(self, bool(self.hs2_jointfix))


def _on_studio(prop, group):
    def cb(self, context):
        if self.type == "ARMATURE":
            rig.set_studio_group(self, group, bool(getattr(self, prop)))
    return cb


STUDIO_FIELDS = (
    ("hs2_studio_body", "Body"),
    ("hs2_studio_arm_L", "Arm_L"),
    ("hs2_studio_arm_R", "Arm_R"),
    ("hs2_studio_leg_L", "Leg_L"),
    ("hs2_studio_leg_R", "Leg_R"),
)


def register():
    bpy.utils.register_class(HS2Preferences)

    bpy.types.Scene.hs2_hand_L = EnumProperty(
        name="左手势", items=_hand_items, update=_on_hand_L,
        description="工作室 cf_anmShapeHand 的 21 个手势（选择即预览，只写手指骨）")
    bpy.types.Scene.hs2_hand_R = EnumProperty(
        name="右手势", items=_hand_items, update=_on_hand_R,
        description="工作室 cf_anmShapeHand 的 21 个手势（选择即预览，只写手指骨）")
    bpy.types.Scene.hs2_hand_key = BoolProperty(
        name="应用到当前帧并打关键帧", default=True,
        description="勾选：当前帧写入手指关键帧（有动作时）；不勾：只改当前姿势")
    bpy.types.Object.hs2_jointfix = BoolProperty(
        name="关节补偿", default=False, update=_on_jointfix,
        description="dam 骨随膝盖/手肘按比例旋转（改关节时褶皱会跟着动）。"
                    "VMD 导入默认开；游戏动作导入默认关（原动作自带 dam 曲线）")
    bpy.types.Scene.hs2_arm_tilt = BoolProperty(
        name="手臂轴倾校准", default=True,
        description="（v3.0 起固定开启）按 MMDD 的 autoBoneAxisAdjust 从父骨"
                    "派生轴倾。这个属性保留只为兼容旧场景")
    for prop, group in STUDIO_FIELDS:
        setattr(bpy.types.Object, prop, BoolProperty(
            name="Studio " + group, default=True,
            update=_on_studio(prop, group),
            description="Studio 的 ActiveIK 分组（Body/左右臂/左右腿）"))

    for c in ops.CLASSES:
        bpy.utils.register_class(c)
    ops2.register()
    ui.register()


def unregister():
    ui.unregister()
    ops2.unregister()
    for c in reversed(ops.CLASSES):
        bpy.utils.unregister_class(c)
    ops._clear_pending()
    for attr in ("hs2_hand_L", "hs2_hand_R", "hs2_hand_key", "hs2_arm_tilt"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for attr in ("hs2_jointfix",) + tuple(p for p, _g in STUDIO_FIELDS):
        if hasattr(bpy.types.Object, attr):
            delattr(bpy.types.Object, attr)
    bpy.utils.unregister_class(HS2Preferences)
