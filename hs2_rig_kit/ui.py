"""侧栏面板（v6 重新排版）。

从上到下四块，都是 `HS2` 分类下的子面板：

    动作   页签（工作室 / H 动作 / 换装）+ 演员 + 动作列表 + 底部快捷导出
    调整   控制器、动作工具、根动画、手势
    导出   模型 / 模型+动作 / 只动画 / 按物体（单位已经是厘米）
    外部动作 VMD、Mixamo、手动放人（默认收起）

约定：面板里只摆位置，所有动作都在 ops / ops2 的操作符里。
"""

import os

import bpy

from . import ops, rig


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _arm(context):
    return ops.current_arm()


def _safe(fn):
    def wrap(self, context):
        # 面板真的在画才让定时器去读目录/刷列表（启动时不碰游戏数据）
        try:
            from . import ops2
            ops2.note_panel_draw()
        except Exception:
            pass
        try:
            fn(self, context)
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.layout.label(text="面板出错：%s" % e, icon="ERROR")
    return wrap


def _right(layout):
    r = layout.row(align=True)
    r.alignment = "RIGHT"
    return r


def _title(layout, text, icon="NONE", note=None):
    """一节的标题：左图标 + 文字，右边可以带个灰色小字。"""
    row = layout.row(align=True)
    row.label(text=text, icon=icon)
    if note:
        _right(row).label(text=note)
    return row


def _grid(layout, columns=2):
    """等宽按钮格（次要操作都用它，别一个按钮占一整行）。"""
    return layout.grid_flow(row_major=True, columns=columns, align=True)


def _big(layout, factor=1.45):
    """主操作：整行、加高。"""
    layout.scale_y = factor
    return layout


# --------------------------------------------------------------------------
# 演员
# --------------------------------------------------------------------------

def actor_label():
    """当前角色叫什么（女1 / 男1 / 对象名），给标题右边用。"""
    from . import api
    active = api.active_character()
    if active is None:
        return "没有角色"
    for slot, ch in api.characters().items():
        if ch is active:
            return api.SLOT_CN.get(slot, slot)
    return active.name


def draw_cast_strip(layout, context, import_button=True):
    """谁在演：一排演员按钮（当前那个按下）+ 导入工作室 FBX 的入口。"""
    from . import api
    chars = api.characters()
    active = api.active_character()

    if not chars:
        row = layout.row(align=True)
        _big(row, 1.35)
        row.operator("hs2.import_character_fbx", text="导入工作室 FBX",
                     icon="FILE_3D")
        layout.label(text="也可以直接点一条动作，会自动放一个人", icon="INFO")
        return chars, None

    row = layout.row(align=True)
    row.scale_y = 1.25
    for slot in sorted(chars, key=lambda s: (s[0] != "f", s)):
        ch = chars[slot]
        op = row.operator("hs2.select_char", text=api.SLOT_CN.get(slot, slot),
                          emboss=True, depress=(ch is active))
        op.name = ch.name
    if import_button:
        row.operator("hs2.import_character_fbx", text="", icon="FILE_3D")
    return chars, active


def draw_actor_buttons(box, context):
    """（换装页在用）返回 (chars, active)。"""
    return draw_cast_strip(box, context, import_button=False)


# --------------------------------------------------------------------------
# 列表（数据由 ops2 在面板外面维护，draw 只画）
# --------------------------------------------------------------------------

class HS2_UL_studio(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.label, icon="PLAY")
        if item.sub:
            _right(row).label(text=item.sub)


class HS2_UL_h(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.label, icon="IMPORT")
        if item.sub:
            _right(row).label(text=item.sub)


class HS2_UL_stage(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.label, icon="KEYFRAME")
        if item.sub:
            _right(row).label(text=item.sub)


# --------------------------------------------------------------------------
# 动作（主面板）
# --------------------------------------------------------------------------

class HS2_PT_main(bpy.types.Panel):
    bl_label = "动作"
    bl_idname = "HS2_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HS2"

    @_safe
    def draw(self, context):
        layout = self.layout
        scn = context.scene
        prefs = ops.addon_prefs(context)
        from . import api, ops2

        # ---- 没配游戏目录 ----
        if not api.game_dir():
            box = layout.box()
            box.label(text="先指定 HS2 游戏目录", icon="ERROR")
            row = box.row(align=True)
            row.scale_y = 1.3
            if prefs:
                row.prop(prefs, "game_dir", text="")
            row.operator("hs2.detect_dir", text="", icon="VIEWZOOM")
            box.label(text="选 abdata 的上一级，例如 D:\\game\\HS2", icon="FILE_FOLDER")
            return

        # ---- 页签 ----
        row = layout.row(align=True)
        row.scale_y = 1.25
        row.prop(scn, "hs2_tab", expand=True)

        if scn.hs2_tab == "CLOTH":
            from . import clothui
            clothui.draw_page(layout, context)
            return

        # ---- 演员 ----
        box = layout.box()
        _title(box, "演员", "ARMATURE_DATA")
        draw_cast_strip(box, context)

        # ---- 动作列表 ----
        if scn.hs2_tab == "STUDIO":
            self.draw_studio(context, layout, scn, api, ops2)
        else:
            self.draw_h(context, layout, scn, api, ops2)

        # ---- 正在演 ----
        last = (scn.hs2_last or "").strip()
        if last:
            box = layout.box()
            box.label(text=last, icon="SOUND")

        # ---- 底部快捷导出（完整选项在「导出」面板）----
        self.draw_quick_export(layout, api)

    # ---- 工作室动作 ----
    def draw_studio(self, context, layout, scn, api, ops2):
        box = layout.box()
        n = len(scn.hs2_studio_items)
        row = _title(box, "动作库", "ACTION")
        right = _right(row)
        if n:
            right.label(text="%d 个" % n)
        op = right.operator("hs2.scan_clips", text="", icon="FILE_REFRESH")
        op.rebuild = True

        if ops2.scanning():
            box.label(text="正在建索引…（首次约 30 秒）", icon="TIME")
            return

        # 搜索框永远先画：搜不到时也不能让输入框消失
        row = box.row(align=True)
        row.prop(scn, "hs2_clip_filter", text="", icon="VIEWZOOM")
        row.prop(scn, "hs2_clip_cat", text="")
        row = box.row(align=True)
        row.prop(scn, "hs2_clip_sub", text="")

        if n == 0:
            self.draw_empty(box, scn, api, "动作")
            return

        box.template_list("HS2_UL_studio", "", scn, "hs2_studio_items",
                          scn, "hs2_studio_index", rows=12)
        box.label(text="点一行就演（演员会自动进场）", icon="INFO")

    # ---- H 动作 ----
    def draw_h(self, context, layout, scn, api, ops2):
        box = layout.box()
        n = len(scn.hs2_h_items)
        _title(box, "H 动作", "SEQUENCE", note=("%d 段" % n) if n else None)

        if ops2.scanning():
            box.label(text="正在建索引…（首次约 30 秒）", icon="TIME")
            return

        row = box.row(align=True)
        row.prop(scn, "hs2_h_cast", text="")
        row.prop(scn, "hs2_h_filter", text="", icon="VIEWZOOM")
        row = box.row(align=True)
        row.prop(scn, "hs2_h_props", text="自动带道具", icon="MESH_DATA")

        if n == 0:
            self.draw_empty(box, scn, api, "H 动作")
            return

        box.template_list("HS2_UL_h", "", scn, "hs2_h_items",
                          scn, "hs2_h_index", rows=9)

        if len(scn.hs2_stage_items):
            box.separator()
            box.label(text="阶段（直接切，演员不动）", icon="KEYFRAME")
            box.template_list("HS2_UL_stage", "", scn, "hs2_stage_items",
                              scn, "hs2_stage_index", rows=6)

    # ---- 空列表 ----
    def draw_empty(self, box, scn, api, what):
        filt = ((getattr(scn, "hs2_clip_filter", "") or "").strip()
                or (getattr(scn, "hs2_h_filter", "") or "").strip())
        if filt:
            box.label(text="没有匹配的%s" % what, icon="INFO")
            return
        box.label(text="还没读到%s" % what, icon="INFO")
        op = box.operator("hs2.scan_clips", text="现在建索引",
                          icon="FILE_REFRESH")
        op.rebuild = True

    # ---- 底部快捷导出 ----
    def draw_quick_export(self, layout, api):
        arm = api.active_character() or api.arm()
        act = (arm.animation_data.action
               if arm is not None and arm.animation_data is not None else None)
        box = layout.box()
        _title(box, "导出", "EXPORT", note="厘米单位")
        row = box.row(align=True)
        row.scale_y = 1.3
        left = row.row(align=True)
        left.enabled = arm is not None
        op = left.operator("hs2.export_model", text="模型", icon="FILE_3D")
        if arm is not None:
            op.name = arm.name
        right = row.row(align=True)
        right.enabled = act is not None
        op = right.operator("hs2.export_char_anim", text="当前动作", icon="ACTION")
        if arm is not None:
            op.name = arm.name
        box.label(text="更多选项在下面的「导出」面板", icon="NONE")


# --------------------------------------------------------------------------
# 调整
# --------------------------------------------------------------------------

class HS2_PT_edit(bpy.types.Panel):
    bl_label = "调整"
    bl_idname = "HS2_PT_edit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HS2"
    bl_parent_id = "HS2_PT_main"

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "hs2_tab", "STUDIO") != "CLOTH"

    @_safe
    def draw(self, context):
        layout = self.layout
        scn = context.scene
        from . import api, animlib

        arm = _arm(context)
        if arm is None:
            box = layout.box()
            box.label(text="还没有角色", icon="INFO")
            draw_cast_strip(box, context)
            return

        # ---- 编辑对象 ----
        box = layout.box()
        _title(box, "编辑对象", "RESTRICT_SELECT_OFF", note=actor_label())
        draw_cast_strip(box, context)
        if arm.get("hs2_custom"):
            row = box.row(align=True)
            row.operator("hs2.align_fbx_pose", text="对齐默认骨架姿势",
                         icon="ARMATURE_DATA")
            row.label(text="T-pose / 直手")

        # ---- 当前动作 ----
        act = api.current_action()
        box = layout.box()
        if act is None:
            _title(box, "当前动作", "ACTION")
            box.label(text="还没装动作，去上面点一条", icon="INFO")
        else:
            frames = 0
            try:
                lo, hi = animlib.action_range(act)
                frames = hi - lo + 1
            except Exception:
                pass
            _title(box, "当前动作", "FILE_MOVIE",
                   note=("%d 帧" % frames) if frames else None)
            box.label(text=act.name, icon="NONE")

        self.draw_rig(layout, context, scn, arm)

        if act is not None:
            self.draw_tools(layout, scn)
            self.draw_root_motion(layout, scn)

        self.draw_hands(layout, scn, arm)

    # ---- 控制器 ----
    def draw_rig(self, layout, context, scn, arm):
        box = layout.box()
        if not rig.has_rig(arm):
            _title(box, "控制器", "CONSTRAINT", note="未安装")
            row = box.row(align=True)
            _big(row, 1.35)
            op = row.operator("hs2.add_rig_all",
                              text="给所有演员加控制器（FK + 手脚 IK）",
                              icon="CONSTRAINT")
            op.fingers = scn.hs2_fingers
            box.prop(scn, "hs2_fingers", text="连手指一起做")
            return

        on = rig.ik_state(arm)
        row = _title(box, "控制器 · IK %s" % ("开" if on else "关"),
                     "CONSTRAINT")
        op = row.operator("hs2.toggle_ik", text="开", icon="CHECKBOX_HLT")
        op.enable = True
        op = _right(row).operator("hs2.toggle_ik", text="关", icon="CHECKBOX_DEHLT")
        op.enable = False

        grid = _grid(box, 2)
        for side in ("L", "R"):
            for is_leg in (True, False):
                nm = rig.foot_ctrl(side) if is_leg else rig.hand_ctrl(side)
                pb = arm.pose.bones.get(nm)
                if pb is None:
                    continue
                grid.prop(pb, '["ik"]',
                          text=("脚 " if is_leg else "手 ") + side, slider=True)

        row = box.row(align=True)
        row.prop(arm, "hs2_jointfix", text="关节校正", toggle=True)
        row.operator("hs2.key_targets", text="重打控制器帧", icon="KEYFRAME")

        grid = _grid(box, 2)
        grid.operator("hs2.snap_ik", text="控制器吸附姿势", icon="SNAP_ON")
        grid.operator("hs2.snap_fk", text="IK 写回 FK", icon="SNAP_OFF")

        row = box.row(align=True)
        op = row.operator("hs2.add_rig_all", text="重建", icon="FILE_REFRESH")
        op.fingers = scn.hs2_fingers
        row.operator("hs2.clear_rig", text="清除", icon="CANCEL")

    # ---- 动作工具 ----
    def draw_tools(self, layout, scn):
        box = layout.box()
        _title(box, "动作工具", "TOOL_SETTINGS", note="生成新动作")
        grid = _grid(box, 3)
        grid.operator("hs2.edit_action", text="左右镜像",
                      icon="MOD_MIRROR").mode = "MIRROR"
        grid.operator("hs2.edit_action", text="放回地面",
                      icon="SNAP_ON").mode = "GROUND"
        op = grid.operator("hs2.edit_action", text="慢放一倍", icon="TIME")
        op.mode = "SPEED"
        op.value = 2.0
        box.label(text="原动作保留，新动作名字后面加后缀", icon="NONE")

    # ---- 根动画 ----
    def draw_root_motion(self, layout, scn):
        box = layout.box()
        _title(box, "根动画", "ARMATURE_DATA", note="Hips → p_cf_anim")
        grid = _grid(box, 3)
        grid.prop(scn, "hs2_rm_move", toggle=True, text="水平")
        grid.prop(scn, "hs2_rm_turn", toggle=True, text="转身")
        grid.prop(scn, "hs2_rm_jump", toggle=True, text="垂直")
        row = box.row(align=True)
        _big(row, 1.25)
        row.operator("hs2.root_motion", text="生成根动画动作", icon="FORWARD")

    # ---- 手势 ----
    def draw_hands(self, layout, scn, arm):
        row = layout.row(align=True)
        row.prop(scn, "hs2_show_hand", text="手势", toggle=True, icon="HAND")
        if not scn.hs2_show_hand:
            return
        box = layout.box()
        _title(box, "手势", "HAND", note="选好再写帧")
        grid = _grid(box, 2)
        grid.prop(scn, "hs2_hand_L", text="左手")
        grid.prop(scn, "hs2_hand_R", text="右手")
        box.prop(scn, "hs2_hand_key", text="在当前帧写关键帧")
        row = box.row(align=True)
        row.scale_y = 1.25
        op = row.operator("hs2.apply_hand_pose", text="写入关键帧",
                          icon="KEYINGSET")
        op.mode = "BOTH"
        op.keyframe = scn.hs2_hand_key
        row.operator("hs2.apply_hand_pose", text="还原",
                     icon="LOOP_BACK").mode = "RESET"


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------

class HS2_PT_export(bpy.types.Panel):
    bl_label = "导出"
    bl_idname = "HS2_PT_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HS2"
    bl_parent_id = "HS2_PT_main"

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "hs2_tab", "STUDIO") != "CLOTH"

    @_safe
    def draw(self, context):
        layout = self.layout
        from . import api

        arm = api.active_character() or api.arm()
        act = (arm.animation_data.action
               if arm is not None and arm.animation_data is not None else None)

        box = layout.box()
        _title(box, "给 UE / Unity", "EXPORT", note=actor_label())

        row = box.row(align=True)
        _big(row, 1.45)
        row.enabled = arm is not None
        op = row.operator("hs2.export_model", text="导出模型（骨架 + 网格）",
                          icon="FILE_3D")
        if arm is not None:
            op.name = arm.name
        box.label(text="身体 / 头 / 眼睛 / 衣服全带上，rest 姿势", icon="NONE")

        row = box.row(align=True)
        row.scale_y = 1.25
        row.enabled = act is not None
        op = row.operator("hs2.export_model", text="模型 + 当前动作",
                          icon="EXPORT")
        if arm is not None:
            op.name = arm.name
        op.bake_anim = True

        row = box.row(align=True)
        row.scale_y = 1.25
        row.enabled = act is not None
        op = row.operator("hs2.export_char_anim", text="只导当前动作（不含网格）",
                          icon="ACTION")
        if arm is not None:
            op.name = arm.name

        box.separator()
        row = box.row(align=True)
        row.scale_y = 1.2
        row.operator("hs2.export_object", text="按物体导出（道具 / 衣服）…",
                     icon="OBJECT_DATA")

        tip = layout.box()
        tip.label(text="单位已换成厘米：UE 导入后 p_cf_anim 缩放 = 1", icon="CHECKMARK")
        tip.label(text="Import Uniform Scale 保持 1，别手填", icon="INFO")
        tip.label(text="别用 文件 → 导出 → FBX（根骨会变 100）", icon="ERROR")


# --------------------------------------------------------------------------
# 外部动作
# --------------------------------------------------------------------------

class HS2_PT_ext(bpy.types.Panel):
    bl_label = "外部动作"
    bl_idname = "HS2_PT_ext"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HS2"
    bl_parent_id = "HS2_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "hs2_tab", "STUDIO") != "CLOTH"

    @_safe
    def draw(self, context):
        layout = self.layout
        from . import api

        box = layout.box()
        _title(box, "导入动作", "IMPORT")
        col = box.column(align=True)
        col.scale_y = 1.25
        col.operator("hs2.import_vmd", text="导入 VMD（MMD 舞蹈）",
                     icon="FILE_MOVIE")
        col.operator("hs2.import_mixamo", text="导入 Mixamo FBX",
                     icon="FILE_3D")
        box.label(text="没演员会自动放一个", icon="NONE")

        box = layout.box()
        _title(box, "手动放人", "ARMATURE_DATA")
        grid = _grid(box, 2)
        grid.scale_y = 1.2
        grid.operator("hs2.load_default", text="+ 女角",
                      icon="ARMATURE_DATA").male = False
        grid.operator("hs2.load_default", text="+ 男角",
                      icon="ARMATURE_DATA").male = True
        row = box.row(align=True)
        row.operator("hs2.load_character_card", text="从角色卡加载…",
                     icon="IMAGE_DATA")

        chars = api.characters()
        if chars:
            row = box.row(align=True)
            for slot in sorted(chars, key=lambda x: (x[0] != "f", x)):
                ch = chars[slot]
                op = row.operator("hs2.select_char",
                                  text=api.SLOT_CN.get(slot, slot),
                                  emboss=True,
                                  depress=(ch is api.active_character()))
                op.name = ch.name
                op = row.operator("hs2.delete_char", text="", icon="X")
                op.name = ch.name

        box = layout.box()
        _title(box, "工作室 FBX 角色", "FILE_3D")
        row = box.row(align=True)
        row.scale_y = 1.25
        row.operator("hs2.import_character_fbx", text="导入工作室 FBX",
                     icon="FILE_3D")
        box.label(text="比角色卡完整：头模、脸型、体模都以导出的网格为准", icon="NONE")


CLASSES = (HS2_PT_main, HS2_PT_edit, HS2_PT_export, HS2_PT_ext,
           HS2_UL_studio, HS2_UL_h, HS2_UL_stage)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
