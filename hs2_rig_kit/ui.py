"""侧栏面板。

布局原则：
1. 主面板只放「一次性的准备工作」——游戏目录、加载角色、状态、IK 状态；
2. 「动作库 / 控制器 / 输出」三个子面板按使用顺序排；
3. 开关一律用真正的勾选框/滑块（不是一次性按钮），状态一眼可见。
"""

import os

import bpy

from . import ops, rig


def _arm(context):
    return ops.current_arm()


def _stamp():
    """插件代号 + 所在目录。用来确认 Blender 跑的到底是哪一份代码。"""
    import sys

    from . import bl_info
    mod = sys.modules.get(__package__)
    f = getattr(mod, "__file__", None) or ""
    full = os.path.dirname(os.path.dirname(os.path.abspath(f))) if f else ""
    parts = full.replace("\\", "/").rstrip("/").split("/")
    short = "/".join(parts[-2:]) if len(parts) >= 2 else full
    v = ".".join(str(x) for x in bl_info.get("version", (0,)))
    return "v%s · …/%s" % (v, short), full


def _safe(fn):
    """draw 出错时不要留白，把异常显示在面板上并打印到控制台。"""
    def wrap(self, context):
        try:
            fn(self, context)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.layout.label(text="HS2 面板出错：%s" % e, icon="ERROR")
    return wrap


def _pref_bool(prefs, name, default):
    return getattr(prefs, name, default) if prefs else default


# ==========================================================================

class HS2_PT_main(bpy.types.Panel):
    bl_label = "HS2 动画工具"
    bl_idname = "HS2_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HS2"

    @_safe
    def draw(self, context):
        layout = self.layout
        scn = context.scene
        prefs = ops.addon_prefs(context)

        # ---- 版本 / 路径（确认跑的是哪份代码）----
        try:
            text, _p = _stamp()
            row = layout.row(align=True)
            row.label(text=text, icon="SCRIPT")
            # 重载插件：注销旧面板/属性 → 重载模块 → 重新注册。
            # （script.reload 只重载模块不重新注册面板，改了 UI 看不出来）
            row.operator("hs2.reload", text="", icon="FILE_REFRESH")
        except Exception:
            pass

        # ---- 游戏目录 ----
        box = layout.box()
        row = box.row(align=True)
        row.label(text="游戏目录", icon="FILE_FOLDER")
        row.operator("hs2.detect_dir", text="", icon="VIEWZOOM")
        if prefs:
            box.prop(prefs, "game_dir", text="")
        if prefs and not prefs.game_dir:
            box.label(text="没设置过就点右边的放大镜", icon="INFO")

        # ---- 加载角色 ----
        col = layout.column(align=True)
        col.scale_y = 1.25
        op = col.operator("hs2.load_default", text="加载默认角色",
                          icon="ARMATURE_DATA")
        op.include_head = _pref_bool(prefs, "include_head", True)
        op.cleanup = True

        arm = _arm(context)
        if arm is None:
            layout.label(text="还没有骨架", icon="INFO")
            return

        # ---- 状态 ----
        box = layout.box()
        col = box.column(align=True)
        col.label(text=arm.name, icon="OUTLINER_OB_ARMATURE")
        col.label(text="骨架 %d 骨 · 变形 %d" % (len(arm.data.bones),
                                                len(rig.deform_bone_names(arm))))
        body = bpy.data.objects.get(ops.BODY_NAME)
        head = bpy.data.objects.get(ops.HEAD_NAME)
        row = col.row(align=True)
        row.label(text="身体 %s" % (len(body.data.vertices) if body else "—"))
        row.label(text="头部 %s" % (len(head.data.vertices) if head else "—"))
        if head is None:
            col.operator("hs2.add_head", icon="ADD", text="补头部")

        # ---- IK / 关节校正状态 ----
        if rig.has_rig(arm):
            on = rig.ik_state(arm)
            row = layout.row(align=True)
            row.label(text="IK %s" % ("开" if on else "关（纯 FK）"),
                      icon="CONSTRAINT")
            row.label(text="开关在下方「控制器」面板", icon="INFO")
        else:
            layout.label(text="下一步：动作库选动作 → 控制器生成", icon="INFO")
        if hasattr(arm, "hs2_jointfix"):
            row = layout.row(align=True)
            row.label(text="关节校正 %s" % ("开" if arm.hs2_jointfix else "关"),
                      icon="MODIFIER")
            row.prop(arm, "hs2_jointfix", text="", toggle=True)

        # ---- 工作室手势（21 种）----
        box = layout.box()
        box.label(text="工作室手势（21 种，直接写手指骨）", icon="HAND")
        row = box.row(align=True)
        row.prop(scn, "hs2_hand_L", text="左手")
        row = box.row(align=True)
        row.prop(scn, "hs2_hand_R", text="右手")
        row = box.row(align=True)
        op = row.operator("hs2.apply_hand_pose", text="写入关键帧", icon="KEYINGSET")
        op.mode = "BOTH"
        op.keyframe = scn.hs2_hand_key
        op = row.operator("hs2.apply_hand_pose", text="还原", icon="LOOP_BACK")
        op.mode = "RESET"
        box.label(text="下拉即预览；「写入关键帧」把当前选择写进选中帧",
                  icon="INFO")
        box.prop(scn, "hs2_hand_key", text="写入时在当前帧打关键帧")


class HS2_PT_anim(bpy.types.Panel):
    bl_label = "动作库"
    bl_idname = "HS2_PT_anim"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HS2"
    bl_parent_id = "HS2_PT_main"

    @_safe
    def draw(self, context):
        layout = self.layout
        scn = context.scene
        names = ops.clip_list(context)

        if not names:
            col = layout.column(align=True)
            col.label(text="动作库还没扫描（第一次约 20 秒）", icon="INFO")
            col.operator("hs2.scan_clips", text="开始扫描", icon="FILE_REFRESH")
        else:
            row = layout.row(align=True)
            row.label(text="%d 个可用动作" % len(names), icon="ACTION")
            row.operator("hs2.scan_clips", text="", icon="FILE_REFRESH")
            layout.prop(scn, "hs2_clip_filter", text="", icon="VIEWZOOM")

        # 下拉框：点开可以直接打字搜索，比「输入框实时过滤」舒服
        layout.prop(scn, "hs2_clip_enum", text="")

        if names:
            idx, names2 = ops.clip_index(context)
            box = layout.box()
            row = box.row(align=True)
            row.operator("hs2.clip_step", text="", icon="TRIA_LEFT").delta = -1
            sub = row.row(align=True)
            sub.alignment = "CENTER"
            sub.label(text=scn.hs2_clip_name or names2[idx])
            row.operator("hs2.clip_step", text="", icon="TRIA_RIGHT").delta = 1

            box.prop(scn, "hs2_clip_pos", text="%d / %d" % (idx + 1, len(names2)),
                     slider=True)

            # 选完就自动装，不用再点「导入」
            box.prop(scn, "hs2_clip_auto", text="切换后自动加载")
            row = box.row(align=True)
            op = row.operator("hs2.import_clip", text="重新导入",
                              icon="FILE_REFRESH")
            op.clip_name = scn.hs2_clip_name or names2[idx]
            op.follow = True

        # ---- 外部动作 ----
        box = layout.box()
        box.label(text="外部动作", icon="IMPORT")
        col = box.column(align=True)
        col.operator("hs2.import_vmd", text="导入 VMD（MMD 舞蹈）", icon="FILE_MOVIE")
        col.operator("hs2.import_mixamo", text="导入 Mixamo FBX", icon="FILE_3D")
        box.prop(scn, "hs2_arm_tilt", text="手臂轴倾校准")

        clip = ops.STATE.get("clip")
        if clip is not None:
            box = layout.box()
            box.label(text="当前：%s" % clip.name, icon="ACTION")
            box.label(text="%d 帧 · %.2f 秒 · %d 根骨"
                      % (clip.frame_count, clip.duration, len(clip.tracks)))


class HS2_PT_rig(bpy.types.Panel):
    bl_label = "控制器"
    bl_idname = "HS2_PT_rig"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HS2"
    bl_parent_id = "HS2_PT_main"

    @_safe
    def draw(self, context):
        layout = self.layout
        arm = _arm(context)
        if arm is None:
            layout.label(text="请先加载角色")
            return

        if not rig.has_rig(arm):
            col = layout.column(align=True)
            col.scale_y = 1.25
            op = col.operator("hs2.add_rig", text="生成控制器", icon="CONSTRAINT")
            op.fingers = False
            op.follow = True
            layout.label(text="全身 FK + 手脚 IK + 可选手指控制器", icon="INFO")
            return

        col = layout.column(align=True)
        op = col.operator("hs2.add_rig", text="重建控制器", icon="FILE_REFRESH")
        op.fingers = bool(arm.get("hs2_fingers"))
        op.follow = True

        # ---- IK ----
        box = layout.box()
        on = rig.ik_state(arm)
        row = box.row(align=True)
        row.label(text="IK 总开关：%s" % ("开" if on else "关"), icon="CONSTRAINT")
        row.operator("hs2.toggle_ik", text="开", icon="CHECKBOX_HLT").enable = True
        row.operator("hs2.toggle_ik", text="关", icon="CHECKBOX_DEHLT").enable = False

        grid = box.grid_flow(row_major=True, columns=2, align=True)
        for side in ("L", "R"):
            for is_leg in (True, False):
                nm = rig.foot_ctrl(side) if is_leg else rig.hand_ctrl(side)
                pb = arm.pose.bones.get(nm)
                if pb is None:
                    continue
                grid.prop(pb, '["ik"]', text=("脚 " if is_leg else "手 ") + side,
                          slider=True)

        # ---- Studio 分组（对应工作室的 ActiveIK 5 组）----
        box = layout.box()
        box.label(text="Studio 分组（ActiveIK）", icon="GROUP")
        row = box.row(align=True)
        for prop, label in (("hs2_studio_body", "Body"),
                            ("hs2_studio_arm_L", "臂 L"),
                            ("hs2_studio_arm_R", "臂 R"),
                            ("hs2_studio_leg_L", "腿 L"),
                            ("hs2_studio_leg_R", "腿 R")):
            if hasattr(arm, prop):
                row.prop(arm, prop, text=label, toggle=True)

        # ---- 关节补偿 ----
        box = layout.box()
        box.prop(arm, "hs2_jointfix", text="关节校正（dam 骨随膝/肘/臀转）",
                 toggle=True)
        box.label(text="VMD 导入默认开；游戏动作默认关（原动作自带 dam 曲线）",
                  icon="INFO")

        # ---- 吸附 / 打帧 ----
        col = layout.column(align=True)
        col.operator("hs2.snap_ik", text="控制器吸附当前姿势", icon="SNAP_ON")
        col.operator("hs2.snap_fk", text="IK 结果写回 FK", icon="SNAP_OFF")
        col.operator("hs2.key_targets", text="控制器重新跟随整段动作", icon="KEYFRAME")

        # ---- 显示 ----
        box = layout.box()
        box.label(text="显示", icon="HIDE_OFF")
        row = box.row(align=True)
        for name, label in ((rig.COLL_CTRL, "控制"), (rig.COLL_IK, "IK"),
                            (rig.COLL_DEF, "变形"), (rig.COLL_FACE, "面部"),
                            (rig.COLL_MISC, "其它"), (rig.COLL_FINGER, "手指")):
            coll = arm.data.collections_all.get(name)
            if coll is None:
                continue
            sub = row.row(align=True)
            sub.prop(coll, "is_visible", text=label, toggle=True, emboss=False)
        if not arm.show_in_front:
            box.operator("hs2.show_in_front", icon="RESTRICT_VIEW_OFF")


class HS2_PT_out(bpy.types.Panel):
    bl_label = "输出"
    bl_idname = "HS2_PT_out"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HS2"
    bl_parent_id = "HS2_PT_main"

    @_safe
    def draw(self, context):
        layout = self.layout
        arm = _arm(context)
        col = layout.column(align=True)
        col.operator("hs2.bake_fk", text="烘焙为纯 FK", icon="ACTION")
        op = col.operator("hs2.export_fbx", text="导出 FBX", icon="EXPORT")
        op.include_mesh = False
        op.deform_only = True
        col.operator("hs2.batch_export", icon="PACKAGE")
        if arm is not None and arm.get("hs2_baked"):
            layout.label(text="已烘焙为纯 FK（控制器不再影响姿势）", icon="CHECKMARK")


CLASSES = (HS2_PT_main, HS2_PT_anim, HS2_PT_rig, HS2_PT_out)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
