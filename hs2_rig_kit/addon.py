"""Blender 插件部分：注册面板、偏好设置、操作符。"""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty

from . import ops, rig, ui


class HS2Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    game_dir: StringProperty(
        name="HS2 游戏目录",
        subtype="DIR_PATH",
        default="",
        description="含 abdata/chara/oo_base.unity3d 的目录",
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


# --------------------------------------------------------------------------
# 动作滑条：用 0~1 的进度值，长度随列表自动缩放
# --------------------------------------------------------------------------

def _on_clip_pos(self, context):
    """滑条动了 → 记住名字；开着「自动加载」就排一次导入（防抖，停手才读）。"""
    i, names = ops.clip_index(context)
    if not names:
        return
    self.hs2_clip_name = names[i]
    if getattr(self, "hs2_clip_auto", True) and ops.current_arm() is not None:
        ops.schedule_clip_load(names[i])


def _on_clip_enum(self, context):
    """下拉框选了动作 → 记住名字、同步滑条位置、按需自动加载。"""
    name = (self.hs2_clip_enum or "").strip()
    if not name:
        return
    self.hs2_clip_name = name
    names = ops.clip_list(context)
    if len(names) > 1 and name in names:
        self.hs2_clip_pos = names.index(name) / float(len(names) - 1)
    if getattr(self, "hs2_clip_auto", True) and ops.current_arm() is not None:
        ops.schedule_clip_load(name, delay=0.05)


def _on_clip_filter(self, context):
    self.hs2_clip_pos = 0.0
    names = ops.clip_list(context)
    self.hs2_clip_name = names[0] if names else ""
    ops.clear_enum_cache()
    if getattr(self, "hs2_clip_auto", True) and names and ops.current_arm() is not None:
        ops.schedule_clip_load(names[0])


def _hand_items(self, context):
    try:
        return ops.handpose.enum_items(ops.game_dir_of(context))
    except Exception:
        return [("-1", "无（还原）", "")]


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

    bpy.types.Scene.hs2_clip_pos = FloatProperty(
        name="进度", default=0.0, min=0.0, max=1.0, precision=4,
        options={"SKIP_SAVE"}, update=_on_clip_pos,
        description="拖动选择动作（按当前过滤结果按比例对应）")
    bpy.types.Scene.hs2_clip_name = StringProperty(name="动作名", default="",
                                                   options={"SKIP_SAVE"})
    bpy.types.Scene.hs2_clip_filter = StringProperty(
        name="过滤", default="", options={"SKIP_SAVE"},
        description="按名字过滤动作（不区分大小写的子串）",
        update=_on_clip_filter)
    bpy.types.Scene.hs2_clip_auto = BoolProperty(
        name="切换动作时自动加载", default=True,
        description="拖动滑条 / 点 ◀▶ 停手 0.35 秒后自动把动作装进来，"
                    "不用再点「导入」")
    bpy.types.Scene.hs2_clip_enum = EnumProperty(
        name="动作", items=ops.clip_enum_items, update=_on_clip_enum,
        description="下拉选择动作（点开可以直接打字搜索）")
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
    ui.register()


def unregister():
    ui.unregister()
    for c in reversed(ops.CLASSES):
        bpy.utils.unregister_class(c)
    ops._clear_pending()
    for attr in ("hs2_clip_pos", "hs2_clip_name", "hs2_clip_filter",
                 "hs2_clip_auto", "hs2_clip_enum", "hs2_hand_L", "hs2_hand_R",
                 "hs2_hand_key"):
        if hasattr(bpy.types.Scene, attr):
            delattr(bpy.types.Scene, attr)
    for attr in ("hs2_jointfix",) + tuple(p for p, _g in STUDIO_FIELDS):
        if hasattr(bpy.types.Object, attr):
            delattr(bpy.types.Object, attr)
    bpy.utils.unregister_class(HS2Preferences)
