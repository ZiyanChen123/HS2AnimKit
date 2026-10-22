"""换装页的列表控件和面板内容。"""

import bpy

from . import api, clothops, ui
from .ripper import clothes as rc


class HS2_UL_cloth(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.label, icon="MOD_CLOTH")
        if item.sub:
            r = row.row(align=True)
            r.alignment = "RIGHT"
            r.label(text=item.sub)


def draw_page(layout, context):
    """在「动作」主面板里画换装页。"""
    scene = context.scene
    box = layout.box()
    chars, active = ui.draw_actor_buttons(box, context)
    if not chars:
        row = box.row(align=True)
        row.operator("hs2.load_default", text="+ 女角",
                     icon="ARMATURE_DATA").male = False
        row.operator("hs2.load_default", text="+ 男角",
                     icon="ARMATURE_DATA").male = True
        layout.label(text="先放一个角色，再给他穿衣服", icon="INFO")
        return

    if not clothops.ensure_loaded(context):
        row = layout.row(align=True)
        row.label(text=scene.hs2_cloth_status or "正在扫描衣服目录…",
                  icon="TIME")
        row.operator("hs2.cloth_scan", text="", icon="FILE_REFRESH")
        return

    row = layout.row(align=True)
    row.prop(scene, "hs2_cloth_slot", text="")
    row.prop(scene, "hs2_cloth_filter", text="", icon="VIEWZOOM")
    row.operator("hs2.cloth_scan", text="", icon="FILE_REFRESH")

    n = len(scene.hs2_cloth_items)
    layout.template_list("HS2_UL_cloth", "", scene, "hs2_cloth_items",
                         scene, "hs2_cloth_index", rows=10)
    row = layout.row(align=True)
    row.label(text="%d 件" % n, icon="NONE")

    rec = clothops.current_record(context)
    if rec is not None:
        row = layout.row(align=True)
        for sid, label, _desc in clothops.state_options(context):
            op = row.operator("hs2.cloth_state", text=label,
                              depress=(scene.hs2_cloth_state == sid))
            op.state = sid
        row = layout.row(align=True)
        row.operator("hs2.cloth_wear", text="穿上", icon="CHECKMARK")
        row.operator("hs2.cloth_clear", text="脱下", icon="X")
        row.prop(scene, "hs2_cloth_preview", text="预览")
        label = "%s · %s" % (
            rec.get("name") or "?",
            rc.SLOT_LABELS.get(rec.get("slot"), rec.get("slot")))
        layout.label(text=label, icon="MOD_CLOTH")

    worn = clothops.worn_records(context)
    box = layout.box()
    box.label(text="导出当前穿搭 · %d 件" % len(worn), icon="EXPORT")
    box.prop(scene, "hs2_cloth_out", text="")
    row = box.row(align=True)
    row.scale_y = 1.25
    row.enabled = bool(worn)
    row.operator("hs2.cloth_export", text="导出当前穿搭", icon="EXPORT")
    row = box.row(align=True)
    row.prop(scene, "hs2_cloth_tex", text="贴图")
    row.prop(scene, "hs2_cloth_skip", text="跳过已有")
    row.prop(scene, "hs2_cloth_flipg", text="翻转G")

    if scene.hs2_cloth_status:
        layout.label(text=scene.hs2_cloth_status, icon="INFO")


CLASSES = (HS2_UL_cloth,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
