"""侧栏面板（v4：动作驱动）。

之前的模型是「先放角色，再选动作装到某个角色身上」，要维护谁是谁、几个槽，
用户根本不该关心这些。现在反过来：

    **动作才是主角。点一条动作 → 演员和道具自己进场；换一条 → 清场重装。**

演员的个数和性别由动作决定（工作室动作 1 个，H 单人 1 个、百合 2 女、3P 3 人），
用户不用知道「女1/女2/男1」这些东西 —— 它们只在内部用来对齐数据。

面板只有三个：
    动作   ← 选什么就演什么
    调整   ← IK / 一键处理 / 手势
    外部动作 ← VMD、Mixamo、手动放人（另一种用法，收起）
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


def draw_actor_buttons(box, context):
    """共享的「编辑对象」按钮；返回 (chars, active)。"""
    from . import api
    chars = api.characters()
    if not chars:
        return chars, None
    box.label(text="编辑对象", icon="ARMATURE_DATA")
    n = len(chars)
    if n >= 3:
        grid = box.grid_flow(row_major=True, columns=n, align=True)
        container = grid
    else:
        container = box.row(align=True)
    active = api.active_character()
    for slot in sorted(chars, key=lambda s: (s[0] != "f", s)):
        ch = chars[slot]
        label = api.SLOT_CN.get(slot, slot)
        btn = container.operator("hs2.select_char", text=label,
                                 emboss=True, depress=(ch is active))
        btn.name = ch.name
    if active is not None:
        slot = tuple(k for k, v in chars.items() if v is active)
        slot = slot[0] if slot else ""
        label = api.SLOT_CN.get(slot, slot) if slot else active.name
        row = box.row(align=True)
        row.label(text="当前：%s" % label, icon="RESTRICT_SELECT_OFF")
    return chars, active


def draw_actor_selector(layout, context):
    """动画页的「编辑对象」：切换演员 + 导出当前动作。"""
    from . import api
    box = layout.box()
    row = box.row(align=True)
    row.scale_y = 1.25
    row.operator("hs2.import_character_fbx", text="导入工作室 FBX",
                 icon="FILE_3D")
    chars = api.characters()
    if not chars:
        return
    box = layout.box()
    chars, active = draw_actor_buttons(box, context)

    # 工作室 FBX 角色：骨架姿势可以重新对齐回默认骨架
    if active is not None and active.get("hs2_custom"):
        r = box.row(align=True)
        r.operator("hs2.align_fbx_pose", text="对齐默认骨架姿势",
                   icon="ARMATURE_DATA")
        r.label(text="T-pose/直手")

    # 只导「当前这个人 + 当前这条动作」，不带网格 —— 给 UE/Unity 用最干净
    if active is not None:
        act = (active.animation_data.action
               if active.animation_data is not None else None)
        box.separator()
        row = box.row(align=True)
        row.scale_y = 1.3
        row.enabled = act is not None
        op = row.operator("hs2.export_char_anim",
                          text="导出当前角色动画", icon="EXPORT")
        op.name = active.name
        if act is None:
            box.label(text="这个角色还没有动作", icon="INFO")
        else:
            box.label(text="当前动作：%s · 仅动画" % act.name, icon="ACTION")


# --------------------------------------------------------------------------
# 列表（数据由 ops2 在面板外面维护，draw 只画）
# --------------------------------------------------------------------------

class HS2_UL_studio(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.label, icon="ACTION")
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

        if not api.game_dir():
            row = layout.row(align=True)
            if prefs:
                row.prop(prefs, "game_dir", text="")
            row.operator("hs2.detect_dir", text="", icon="VIEWZOOM")
            layout.label(text="先选 HS2 游戏目录（abdata 的上一级）", icon="ERROR")
            return

        layout.prop(scn, "hs2_tab", expand=True)

        if scn.hs2_tab == "CLOTH":
            from . import clothui
            clothui.draw_page(layout, context)
            return

        if scn.hs2_tab == "STUDIO":
            self.draw_studio(context, layout, scn, api, ops2)
        else:
            self.draw_h(context, layout, scn, api, ops2)

        last = scn.hs2_last or ""
        if last:
            layout.label(text="正在演：%s" % last, icon="CHECKMARK")

    # ---- 工作室动作 ----
    def draw_studio(self, context, layout, scn, api, ops2):
        if ops2.scanning():
            self.draw_scanning(layout)
            return

        # 搜索框永远先画：关键词搜不到时只显示“没有匹配”，不能让输入框消失。
        row = layout.row(align=True)
        row.prop(scn, "hs2_clip_filter", text="", icon="VIEWZOOM")
        row.prop(scn, "hs2_clip_cat", text="")
        row.prop(scn, "hs2_clip_sub", text="")

        n = len(scn.hs2_studio_items)
        if n == 0:
            filt = (getattr(scn, "hs2_clip_filter", "") or "").strip()
            cat = (getattr(scn, "hs2_clip_cat", "") or "").strip()
            sub = (getattr(scn, "hs2_clip_sub", "") or "").strip()
            if filt or (cat and cat not in ("ALL", "～")) or (
                    sub and sub not in ("ALL", "～")):
                layout.label(text="没有匹配的动作", icon="INFO")
            else:
                self.draw_empty(layout, api)
            return

        layout.template_list("HS2_UL_studio", "", scn, "hs2_studio_items",
                             scn, "hs2_studio_index", rows=13)

        row = layout.row(align=True)
        row.label(text="%d 个 · 点一行就演" % n, icon="NONE")
        op = row.operator("hs2.scan_clips", text="", icon="FILE_REFRESH")
        op.rebuild = True

    # ---- H 动作 ----
    def draw_h(self, context, layout, scn, api, ops2):
        if ops2.scanning():
            self.draw_scanning(layout)
            return

        # H 页的搜索/人数/道具也先画，空结果时保留控件。
        row = layout.row(align=True)
        row.prop(scn, "hs2_h_cast", text="")
        row.prop(scn, "hs2_h_filter", text="", icon="VIEWZOOM")
        row.prop(scn, "hs2_h_props", text="", icon="MESH_DATA")

        if not len(scn.hs2_h_items):
            filt = (getattr(scn, "hs2_h_filter", "") or "").strip()
            cast = (getattr(scn, "hs2_h_cast", "ALL") or "ALL")
            if filt or cast != "ALL":
                layout.label(text="没有匹配的 H 动作", icon="INFO")
            else:
                self.draw_empty(layout, api)
            return

        layout.template_list("HS2_UL_h", "", scn, "hs2_h_items",
                             scn, "hs2_h_index", rows=10)

        if len(scn.hs2_stage_items):
            layout.label(text="阶段（直接切，演员不动）", icon="KEYFRAME")
            layout.template_list("HS2_UL_stage", "", scn, "hs2_stage_items",
                                 scn, "hs2_stage_index", rows=7)

    # ---- 状态提示 ----
    def draw_scanning(self, layout):
        box = layout.box()
        box.label(text="正在建立动作库索引…（首次约 30 秒）", icon="TIME")

    def draw_empty(self, layout, api):
        box = layout.box()
        if not api.game_dir():
            box.label(text="没找到游戏目录", icon="ERROR")
            return
        box.label(text="还没读到动作", icon="INFO")
        op = box.operator("hs2.scan_clips", text="现在建索引",
                          icon="FILE_REFRESH")
        op.rebuild = True


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
            draw_actor_selector(layout, context)
            layout.label(text="先点一条动作", icon="INFO")
            return

        # 多人场景：先确认正在编辑谁（下面所有工具都只针对这个演员）
        draw_actor_selector(layout, context)

        act = api.current_action()
        has_action = act is not None
        if has_action:
            lo, hi = 1, 1
            try:
                lo, hi = animlib.action_range(act)
            except Exception:
                pass
            box = layout.box()
            row = box.row(align=True)
            row.label(text=arm.name, icon="ARMATURE_DATA")
            _right(row).label(text="%d 帧" % (hi - lo + 1))
            box.label(text="当前动作：%s" % act.name, icon="ACTION")

        # ---- 控制器（只有装了动作、或者场景里有演员才需要）----
        if rig.has_rig(arm):
            box = layout.box()
            on = rig.ik_state(arm)
            row = box.row(align=True)
            row.label(text="IK %s" % ("开" if on else "关"), icon="CONSTRAINT")
            row.operator("hs2.toggle_ik", text="开",
                         icon="CHECKBOX_HLT").enable = True
            row.operator("hs2.toggle_ik", text="关",
                         icon="CHECKBOX_DEHLT").enable = False
            grid = box.grid_flow(row_major=True, columns=2, align=True)
            for side in ("L", "R"):
                for is_leg in (True, False):
                    nm = rig.foot_ctrl(side) if is_leg else rig.hand_ctrl(side)
                    pb = arm.pose.bones.get(nm)
                    if pb is None:
                        continue
                    grid.prop(pb, '["ik"]',
                              text=("脚 " if is_leg else "手 ") + side,
                              slider=True)
            row = box.row(align=True)
            row.prop(arm, "hs2_jointfix", text="关节校正", toggle=True)
            row.operator("hs2.key_targets", text="重打控制器帧",
                         icon="KEYFRAME")
            row = box.row(align=True)
            row.operator("hs2.snap_ik", text="控制器吸附姿势", icon="SNAP_ON")
            row.operator("hs2.snap_fk", text="IK 写回 FK", icon="SNAP_OFF")
            row = box.row(align=True)
            op = row.operator("hs2.add_rig_all", text="重建所有演员的控制器",
                              icon="FILE_REFRESH")
            op.fingers = scn.hs2_fingers
            row.operator("hs2.clear_rig", text="清除", icon="CANCEL")
        else:
            col = layout.column(align=True)
            col.scale_y = 1.3
            op = col.operator("hs2.add_rig_all",
                              text="给所有演员加控制器（FK + 手脚 IK）",
                              icon="CONSTRAINT")
            op.fingers = scn.hs2_fingers
            col.prop(scn, "hs2_fingers", text="连手指一起做")

        # ---- 动作后处理（只在有动作时可做，生成新动作、原动作保留）----
        if has_action:
            box = layout.box()
            box.label(text="动作后处理（生成新动作，原动作不丢）",
                      icon="TOOL_SETTINGS")
            grid = box.grid_flow(row_major=True, columns=2, align=True)
            grid.operator("hs2.edit_action", text="左右镜像",
                          icon="MOD_MIRROR").mode = "MIRROR"
            grid.operator("hs2.edit_action", text="放回地面",
                          icon="SNAP_ON").mode = "GROUND"
            op = grid.operator("hs2.edit_action", text="慢放一倍",
                               icon="TIME")
            op.mode = "SPEED"
            op.value = 2.0

            box = layout.box()
            box.label(text="根动画（把 Hips 运动抽到 p_cf_anim）",
                      icon="ARMATURE_DATA")
            row = box.row(align=True)
            row.prop(scn, "hs2_rm_move", toggle=True)
            row.prop(scn, "hs2_rm_turn", toggle=True)
            row.prop(scn, "hs2_rm_jump", toggle=True)
            row = box.row(align=True)
            row.scale_y = 1.2
            row.operator("hs2.root_motion", text="生成根动画动作",
                         icon="FORWARD")

        # ---- 手势 ----
        row = layout.row(align=True)
        row.prop(scn, "hs2_show_hand", text="手势", toggle=True, icon="HAND")
        if scn.hs2_show_hand:
            box = layout.box()
            box.label(text="选好左右手手势，再写入关键帧", icon="HAND")
            row = box.row(align=True)
            row.prop(scn, "hs2_hand_L", text="左手")
            row.prop(scn, "hs2_hand_R", text="右手")
            box.prop(scn, "hs2_hand_key", text="在当前帧写关键帧")
            row = box.row(align=True)
            op = row.operator("hs2.apply_hand_pose", text="写入关键帧",
                              icon="KEYINGSET")
            op.mode = "BOTH"
            op.keyframe = scn.hs2_hand_key
            row.operator("hs2.apply_hand_pose", text="还原",
                         icon="LOOP_BACK").mode = "RESET"


# --------------------------------------------------------------------------
# 外部动作（另一种用法：自己带动作进来）
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

        layout.label(text="手上有 VMD / Mixamo 动作时走这里", icon="NONE")
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("hs2.import_vmd", text="导入 VMD（MMD 舞蹈）",
                     icon="FILE_MOVIE")
        col.operator("hs2.import_mixamo", text="导入 Mixamo FBX",
                     icon="FILE_3D")
        layout.label(text="没演员会自动放一个，装完到「调整」里改", icon="NONE")

        box = layout.box()
        box.label(text="手动放人（自制动作用）", icon="ARMATURE_DATA")
        row = box.row(align=True)
        row.operator("hs2.load_default", text="+ 女角",
                     icon="ARMATURE_DATA").male = False
        row.operator("hs2.load_default", text="+ 男角",
                     icon="ARMATURE_DATA").male = True
        chars = api.characters()
        if chars:
            row = box.row(align=True)
            for slot in sorted(chars, key=lambda x: (x[0] != "f", x)):
                ch = chars[slot]
                op = row.operator("hs2.select_char", text=ch.name,
                                  emboss=True,
                                  depress=(ch is api.active_character()))
                op.name = ch.name
                op = row.operator("hs2.delete_char", text="", icon="X")
                op.name = ch.name


CLASSES = (HS2_PT_main, HS2_PT_edit, HS2_PT_ext,
           HS2_UL_studio, HS2_UL_h, HS2_UL_stage)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
