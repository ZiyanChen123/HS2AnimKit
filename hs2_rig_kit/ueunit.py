"""导出到 UE 之前，把场景数据换成**厘米**。

为什么必须换
------------
Blender 场景里 1 单位 = 1 米，UE 里 1 单位 = 1 厘米。Blender 的 FBX 导出器
默认把这层换算记在**文件单位**上（``UnitScaleFactor = 100``），几何数据仍是
米。UE 5.x 的导入器（Interchange / ufbx）读到之后，会把「米 → 厘米」这 100
倍乘到骨架的**根骨**上：模型的显示大小是对的，但 ``p_cf_anim`` 的缩放变成
100。根骨缩放跟别的骨架不一样，做「兼容骨架」重定向时动画就会被放大或压扁
（UE 的骨骼参考姿势来自 FBX 里的 BindPose，根骨那一层带着这个 100）。

修法就是把 100 直接做进数据里：骨头 rest 位置、蒙皮顶点、姿势位移统统 ×100，
再把场景单位声明成「1 单位 = 1 厘米」，于是 ``UnitScaleFactor = 1``，UE 导入
时不做任何换算，``p_cf_anim`` 的缩放就是干净的 1，和别的骨架一致。

为什么不能只改导出选项
----------------------
``apply_scale_options`` 的几种组合，要么把这 100 记在文件单位上（UE 会乘到
根骨），要么记在骨架物体节点上（BindPose 里根骨缩放也会变成 100），没有一种
能把「数据变大、节点不变」做出来 —— 骨架的骨矩阵是相对物体空间的，导出器只
会把全局缩放挂在物体节点上。所以只能在导出期间临时改数据。

这里所有改动都在导出结束后精确还原（rest 骨、顶点、关键帧都存了原值，
不是乘完再除，避免浮点误差累积）。
"""

import contextlib

import bpy
from mathutils import Matrix

from . import compat

CM = 100.0        # 1 米 = 100 厘米
CM_UNIT = 0.01    # 场景单位：1 Blender 单位 = 1 厘米


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def _object_actions(ob):
    ad = getattr(ob, "animation_data", None)
    if ad is None:
        return []
    out = []
    if ad.action is not None:
        out.append(ad.action)
    for tr in getattr(ad, "nla_tracks", ()) or ():
        for st in tr.strips:
            act = getattr(st, "action", None)
            if act is not None:
                out.append(act)
    return out


def _location_curves(ob):
    """物体/骨骼的位移曲线（旋转、缩放不用动，缩放是相对的）。"""
    curves = []
    seen = set()
    for act in _object_actions(ob):
        try:
            bag = compat.bag(act, ob)
        except Exception:
            bag = None
        if bag is None:
            continue
        for fc in bag:
            path = fc.data_path
            if path != "location" and not path.endswith(".location"):
                continue
            key = (act.name, path, fc.array_index)
            if key in seen:
                continue
            seen.add(key)
            curves.append(fc)
    return curves


def _select_only(objs):
    for ob in bpy.context.view_layer.objects:
        try:
            ob.select_set(False)
        except Exception:
            pass
    for ob in objs:
        try:
            ob.select_set(True)
        except Exception:
            pass


def _with_object_mode(ob):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


# --------------------------------------------------------------------------
# 备份 / 还原
# --------------------------------------------------------------------------

class _Snapshot:
    def __init__(self, arm, meshes):
        self.arm = arm
        self.meshes = meshes
        self.bones = None          # [(name, head, tail, roll)]
        self.pose_loc = None       # {bone: Vector}
        self.arm_matrix = None
        self.curves = None         # [(fcurve, [co, co, ...])]
        self.verts = None          # [(mesh, array)]
        self.shape_keys = None     # [(key_block, array)]


def _save(arm, meshes):
    st = _Snapshot(arm, meshes)

    if arm is not None:
        # rest 骨：只在 edit 模式下可读
        keep = [ob for ob in bpy.context.selected_objects]
        active = bpy.context.view_layer.objects.active
        _with_object_mode(arm)
        _select_only([arm])
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            st.bones = [(eb.name, eb.head.copy(), eb.tail.copy(), eb.roll)
                        for eb in arm.data.edit_bones]
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
            _select_only(keep)
            if active is not None:
                try:
                    bpy.context.view_layer.objects.active = active
                except Exception:
                    pass

        st.pose_loc = {pb.name: pb.location.copy() for pb in arm.pose.bones}
        st.arm_matrix = arm.matrix_world.copy()

    curves = _location_curves(arm) if arm is not None else []
    for ob in meshes:
        curves.extend(_location_curves(ob))
    st.curves = []
    seen = set()
    for fc in curves:
        if fc.as_pointer() in seen:
            continue
        seen.add(fc.as_pointer())
        st.curves.append((fc, [tuple(k.co) for k in fc.keyframe_points]))

    import numpy as np
    st.verts = []
    st.shape_keys = []
    for ob in meshes:
        me = ob.data
        arr = np.empty(len(me.vertices) * 3, dtype=np.float32)
        me.vertices.foreach_get("co", arr)
        st.verts.append((me, arr))
        if me.shape_keys is not None:
            for kb in me.shape_keys.key_blocks:
                arr = np.empty(len(kb.data) * 3, dtype=np.float32)
                kb.data.foreach_get("co", arr)
                st.shape_keys.append((kb, arr))
    return st


def _restore(st):
    arm, meshes = st.arm, st.meshes

    if st.bones and arm is not None:
        _with_object_mode(arm)
        _select_only([arm])
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            for name, head, tail, roll in st.bones:
                eb = arm.data.edit_bones.get(name)
                if eb is None:
                    continue
                eb.head = head
                eb.tail = tail
                eb.roll = roll
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")

    if st.pose_loc and arm is not None:
        for name, loc in st.pose_loc.items():
            pb = arm.pose.bones.get(name)
            if pb is not None:
                pb.location = loc
    if st.arm_matrix is not None and arm is not None:
        arm.matrix_world = st.arm_matrix

    for fc, co in st.curves or ():
        fc.keyframe_points.foreach_set("co", [c for pt in co for c in pt])
        fc.update()

    for me, arr in st.verts or ():
        me.vertices.foreach_set("co", arr)
        me.update()
    for kb, arr in st.shape_keys or ():
        kb.data.foreach_set("co", arr)


# --------------------------------------------------------------------------
# 缩放
# --------------------------------------------------------------------------

def _scale_rest(arm, f):
    """rest 骨整体缩放 f 倍（骨架局部空间，不碰 pose）。"""
    keep = [ob for ob in bpy.context.selected_objects]
    active = bpy.context.view_layer.objects.active
    _with_object_mode(arm)
    _select_only([arm])
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        m = Matrix.Scale(f, 4)
        for eb in arm.data.edit_bones:
            eb.transform(m)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        _select_only(keep)
        if active is not None:
            try:
                bpy.context.view_layer.objects.active = active
            except Exception:
                pass


def _scale_curves(curves, f, cm=None):
    """位移曲线整体缩放。

    骨骼位移是骨架局部单位，按 ``f`` 缩放；物体位移是世界单位，按 ``cm``。
    两者只在骨架物体本身带缩放时才不同（工作室 FBX 角色是 0.1）。
    """
    cm = f if cm is None else cm
    seen = set()
    for fc in curves:
        if fc.as_pointer() in seen:
            continue
        seen.add(fc.as_pointer())
        k = f if fc.data_path.startswith("pose.bones") else cm
        for point in fc.keyframe_points:
            point.co[1] *= k
            point.handle_left[1] *= k
            point.handle_right[1] *= k
        fc.update()


def _scale_mesh(ob, f):
    """网格数据在自己的局部空间里缩放 f 倍。"""
    ob.data.transform(Matrix.Scale(f, 4))


def _under(ob, root):
    """ob 是不是挂在 root 下面（缩放会跟着 root 变）。"""
    p = ob.parent
    while p is not None:
        if p is root:
            return True
        p = p.parent
    return False


def _uniform_scale(mw):
    s = mw.to_scale()
    return (s.x + s.y + s.z) / 3.0


def _world_scale_mesh(ob, f):
    """按世界原点缩放网格数据，物体自身变换保持不变（没有骨架时用）。"""
    ob.data.transform(ob.matrix_world.inverted() @ Matrix.Scale(f, 4) @ ob.matrix_world)


def _patch_unit_factor():
    """让导出器写 ``UnitScaleFactor = 1.0``。

    只改场景单位的话，导出器算出来是 ``100 * float32(0.01)`` = 0.99999998，
    有些工具会当成「不是厘米」。这里直接把换算函数替成 1.0，写进文件的就是
    精确的 1。拿不到导出器模块时返回 None，调用方退回改场景单位。
    """
    try:
        from io_scene_fbx import export_fbx_bin
        orig = export_fbx_bin.units_blender_to_fbx_factor
    except Exception:
        return None
    export_fbx_bin.units_blender_to_fbx_factor = lambda scene: 1.0
    return orig


def _unpatch_unit_factor(orig):
    if orig is None:
        return
    try:
        from io_scene_fbx import export_fbx_bin
        export_fbx_bin.units_blender_to_fbx_factor = orig
    except Exception:
        pass


@contextlib.contextmanager
def centimeters(arm=None, meshes=()):
    """在 with 里面导出的 FBX 用厘米，出来自动还原场景。

    ``arm`` 可以是 None（只导网格/道具时，没有骨架要处理）。
    """
    meshes = [ob for ob in meshes if ob is not None and ob.type == "MESH"]
    scene = bpy.context.scene
    units = (scene.unit_settings.system, scene.unit_settings.scale_length)

    st = _save(arm, meshes)
    orig_unit_fn = None
    try:
        curves = []
        if arm is not None:
            # 骨架物体本身可能带缩放（工作室 FBX 角色是 0.1）：先把缩放收进数据
            mw = arm.matrix_world.copy()
            s = _uniform_scale(mw) or 1.0
            f = CM * s

            _scale_rest(arm, f)
            for pb in arm.pose.bones:
                pb.location = pb.location * f

            # 节点：位置 ×CM、缩放归 1，100 倍全部落在数据上
            arm.matrix_world = (Matrix.Translation(mw.translation * CM)
                                @ mw.to_quaternion().to_matrix().to_4x4())

            curves.extend(_location_curves(arm))
            for ob in meshes:
                curves.extend(_location_curves(ob))
            _scale_curves(curves, f, CM)

            for ob in meshes:
                # 挂在骨架下面的网格跟着父级缩放走；独立的按世界原点缩
                if _under(ob, arm):
                    _scale_mesh(ob, f)
                else:
                    _world_scale_mesh(ob, CM)
        else:
            for ob in meshes:
                curves.extend(_location_curves(ob))
            _scale_curves(curves, CM)
            for ob in meshes:
                _world_scale_mesh(ob, CM)

        # 声明 1 单位 = 1 厘米，导出器才会写 UnitScaleFactor = 1
        orig_unit_fn = _patch_unit_factor()
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = CM_UNIT
    except Exception:
        _unpatch_unit_factor(orig_unit_fn)
        scene.unit_settings.system, scene.unit_settings.scale_length = units
        _restore(st)
        raise

    try:
        yield
    finally:
        _unpatch_unit_factor(orig_unit_fn)
        scene.unit_settings.system, scene.unit_settings.scale_length = units
        _restore(st)
