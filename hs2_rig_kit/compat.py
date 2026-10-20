"""Blender 版本兼容层：Action / fcurve 的读写。

4.x
    ``Action.fcurves`` 直接可读写（legacy action）。
4.4+
    引入 slotted action：不绑 ``animation_data.action_slot`` 动作不求值，
    但 ``Action.fcurves`` 还在，老写法能跑。
5.x
    legacy API 被**彻底移除**（没有 ``Action.fcurves`` / ``Action.groups``），
    只剩 layored action：
    ``action.layers[0].strips[0].channelbag(slot).fcurves``

所以对外统一成一个「拿到 fcurve 容器」的函数，调用方不用关心版本。
"""

import bpy


def is_layered(act):
    """这个 action 是不是新版分层 action（没有 .fcurves）。"""
    return act is not None and not hasattr(act, "fcurves")


def ensure_slot(act, arm=None):
    """保证 action 有一个 slot，并把它绑到 arm 的 animation_data 上。"""
    if act is None or not hasattr(act, "slots"):
        return None
    try:
        slots = act.slots
        if len(slots):
            slot = slots[0]
        else:
            slot = slots.new(id_type="OBJECT",
                             name=(arm.name if arm is not None else "HS2"))
        if arm is not None:
            ad = arm.animation_data
            if ad is not None and hasattr(ad, "action_slot"):
                try:
                    ad.action_slot = slot
                except Exception:
                    pass
        return slot
    except Exception:
        return None


def channelbag(act, arm=None):
    """分层 action → channelbag；legacy action → None。"""
    if not is_layered(act):
        return None
    try:
        if not len(act.layers):
            act.layers.new("HS2")
        layer = act.layers[0]
        if not len(layer.strips):
            layer.strips.new(type="KEYFRAME")
        strip = layer.strips[0]
        slot = ensure_slot(act, arm)
        if slot is None:
            return None
        return strip.channelbag(slot, ensure=True)
    except Exception:
        return None


def bag(act, arm=None):
    """统一的 fcurve 容器：legacy 用 act.fcurves，分层用 channelbag.fcurves。"""
    if act is None:
        return None
    if not is_layered(act):
        return act.fcurves
    cb = channelbag(act, arm)
    return cb.fcurves if cb is not None else None


def new_fcurve(container, data_path, index, group=None):
    """建一条 fcurve。group 只是显示分组，建不出来不影响功能。"""
    if group:
        try:
            return container.new(data_path, index=index, action_group=group)
        except Exception:
            pass
    return container.new(data_path, index=index)


def fill(fc, values, start=1, interp="LINEAR"):
    """往一条 fcurve 里灌逐帧数值（比 keyframe_insert 快一个数量级）。"""
    if fc is None:
        return
    fc.keyframe_points.add(count=len(values))
    flat = []
    for k, v in enumerate(values):
        flat.extend((start + k, float(v)))
    fc.keyframe_points.foreach_set("co", flat)
    for kp in fc.keyframe_points:
        kp.interpolation = interp
    fc.update()


def find_fcurve(container, data_path, index):
    if container is None:
        return None
    try:
        return container.find(data_path, index=index)
    except Exception:
        return None


def fcurve_count(act, arm=None):
    c = bag(act, arm)
    return len(c) if c is not None else 0


def new_action(arm, name):
    """建一个 action 并挂到 arm 上（含 slot 绑定）。"""
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = act
    ensure_slot(act, arm)
    return act
