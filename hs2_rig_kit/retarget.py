"""外部动作重定向：VMD / Mixamo FBX → HS2 骨架。

两条路都归到同一个出口：算出每根 HS2 骨的**局部旋转**（和根骨位移），
批量写成 fcurve，再交给 `rig.key_targets` 镜像到控制层。

坐标系与单位
------------
* VMD：MMD 是 Y-up 左手、前后与 Unity 相反（`mmd_quat_to_blender` 里
  补了 spec "-x,y,-z" 那步绕 Y 180° 的共轭），再换基到 Blender。
  MMD 模型高约 20 单位，HS2 高 16 单位，所以 1 MMD 单位 ≈ 0.08 m。
* Mixamo：源骨架有任意静止朝向，用「世界旋转增量」搬运：
  `Δ = R_src_world · R_src_rest⁻¹`，因为 HS2 骨静止旋转是单位四元数，
  目标世界旋转直接就是 Δ。

（原「贴地」逻辑已彻底删除 —— MMDD 插件没有这个功能，它 100% 忠实
还原 VMD 数据，脚在哪儿就是哪儿。）
"""

import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from .ripper import mathutil as mu

MIXAMO_MAP = {
    "Hips": "cf_J_Hips",
    "Spine": "cf_J_Spine01",
    "Spine1": "cf_J_Spine02",
    "Spine2": "cf_J_Spine03",
    "Neck": "cf_J_Neck",
    "Head": "cf_J_Head",
    "LeftShoulder": "cf_J_Shoulder_L",
    "RightShoulder": "cf_J_Shoulder_R",
    "LeftArm": "cf_J_ArmUp00_L",
    "RightArm": "cf_J_ArmUp00_R",
    "LeftForeArm": "cf_J_ArmLow01_L",
    "RightForeArm": "cf_J_ArmLow01_R",
    "LeftHand": "cf_J_Hand_L",
    "RightHand": "cf_J_Hand_R",
    "LeftUpLeg": "cf_J_LegUp00_L",
    "RightUpLeg": "cf_J_LegUp00_R",
    "LeftLeg": "cf_J_LegLow01_L",
    "RightLeg": "cf_J_LegLow01_R",
    "LeftFoot": "cf_J_Foot01_L",
    "RightFoot": "cf_J_Foot01_R",
    "LeftToeBase": "cf_J_Foot02_L",
    "RightToeBase": "cf_J_Foot02_R",
}

FINGER_MAP = {
    "LeftHandIndex1": "cf_J_Hand_Index01_L",
    "LeftHandIndex2": "cf_J_Hand_Index02_L",
    "LeftHandIndex3": "cf_J_Hand_Index03_L",
    "RightHandIndex1": "cf_J_Hand_Index01_R",
    "RightHandIndex2": "cf_J_Hand_Index02_R",
    "RightHandIndex3": "cf_J_Hand_Index03_R",
    "LeftHandMiddle1": "cf_J_Hand_Middle01_L",
    "LeftHandMiddle2": "cf_J_Hand_Middle02_L",
    "LeftHandMiddle3": "cf_J_Hand_Middle03_L",
    "RightHandMiddle1": "cf_J_Hand_Middle01_R",
    "RightHandMiddle2": "cf_J_Hand_Middle02_R",
    "RightHandMiddle3": "cf_J_Hand_Middle03_R",
    "LeftHandRing1": "cf_J_Hand_Ring01_L",
    "LeftHandRing2": "cf_J_Hand_Ring02_L",
    "LeftHandRing3": "cf_J_Hand_Ring03_L",
    "RightHandRing1": "cf_J_Hand_Ring01_R",
    "RightHandRing2": "cf_J_Hand_Ring02_R",
    "RightHandRing3": "cf_J_Hand_Ring03_R",
    "LeftHandPinky1": "cf_J_Hand_Little01_L",
    "LeftHandPinky2": "cf_J_Hand_Little02_L",
    "LeftHandPinky3": "cf_J_Hand_Little03_L",
    "RightHandPinky1": "cf_J_Hand_Little01_R",
    "RightHandPinky2": "cf_J_Hand_Little02_R",
    "RightHandPinky3": "cf_J_Hand_Little03_R",
    "LeftHandThumb1": "cf_J_Hand_Thumb01_L",
    "LeftHandThumb2": "cf_J_Hand_Thumb02_L",
    "LeftHandThumb3": "cf_J_Hand_Thumb03_L",
    "RightHandThumb1": "cf_J_Hand_Thumb01_R",
    "RightHandThumb2": "cf_J_Hand_Thumb02_R",
    "RightHandThumb3": "cf_J_Hand_Thumb03_R",
}


# --------------------------------------------------------------------------
# 写曲线
# --------------------------------------------------------------------------

def new_action(arm, name):
    from . import compat
    return compat.new_action(arm, name)


def _bind_slot(arm, act):
    """Blender 4.4+ 的 slotted action：不绑 slot 的话动作不会求值。"""
    from . import compat
    compat.ensure_slot(act, arm)


def write_curves(arm, tracks, start=1, action_name="HS2_Import",
                 fps=30.0, frame_count=None):
    """tracks: {bone: {"rot": [(w,x,y,z)], "pos": [(x,y,z)]}} → 建 Action。

    直接写 fcurve（逐帧关键帧），比 keyframe_insert 快一个数量级。
    """
    from . import compat

    act = compat.new_action(arm, action_name)
    curves = compat.bag(act, arm)
    n = frame_count or max((len(v.get("rot") or v.get("pos") or [])
                            for v in tracks.values()), default=0)
    for bone, tr in tracks.items():
        if bone not in arm.pose.bones:
            continue
        base = 'pose.bones["%s"]' % bone
        rot = tr.get("rot")
        pos = tr.get("pos")
        if rot:
            for i in range(4):
                fc = compat.new_fcurve(curves, base + ".rotation_quaternion",
                                       i, bone)
                _fill(fc, [q[i] for q in rot], start)
        if pos:
            for i in range(3):
                fc = compat.new_fcurve(curves, base + ".location", i, bone)
                _fill(fc, [p[i] for p in pos], start)
        pb = arm.pose.bones.get(bone)
        if pb is not None:
            pb.rotation_mode = "QUATERNION"
    compat.ensure_slot(act, arm)
    return act, n


def _fill(fc, values, start):
    fc.keyframe_points.add(count=len(values))
    flat = []
    for k, v in enumerate(values):
        flat.extend((start + k, float(v)))
    fc.keyframe_points.foreach_set("co", flat)
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
    fc.update()


# Mixamo
# --------------------------------------------------------------------------

def _bone_key(name):
    """'mixamorig:LeftArm' -> 'LeftArm'"""
    return name.split(":")[-1]


def find_source_armature(objects):
    for ob in objects:
        if ob.type == "ARMATURE" and any(
                _bone_key(b.name) in MIXAMO_MAP or _bone_key(b.name) in FINGER_MAP
                for b in ob.data.bones):
            return ob
    return None


def mixamo_tracks(src, dst, start=None, end=None, with_fingers=True,
                  scale=None):
    """把源骨架的动画转成 HS2 的局部旋转。返回 (tracks, frames)。

    关键：源骨架的物体本身通常带 +90°X 旋转（Mixamo 是 Y-up、厘米制），
    ``pose.matrix`` 是**骨架空间**，直接把那里的旋转搬到 HS2（Z-up）会整体拧掉。
    所以统一用**世界空间**：先乘上 matrix_world 拿到世界朝向，再取相对静止的增量。
    HS2 静止旋转≈单位四元数，所以目标世界朝向就是 Δ，再除回父级得到 basis。
    """
    scn = bpy.context.scene
    f0 = int(start if start is not None else scn.frame_start)
    f1 = int(end if end is not None else scn.frame_end)
    frames = f1 - f0 + 1

    mapping = dict(MIXAMO_MAP)
    if with_fingers:
        mapping.update(FINGER_MAP)
    src_map = {}
    for b in src.data.bones:
        key = _bone_key(b.name)
        if key in mapping and mapping[key] in dst.pose.bones:
            src_map[key] = b.name

    src_q = src.matrix_world.to_quaternion()
    src_m = src.matrix_world
    dst_q = dst.matrix_world.to_quaternion()

    dst_names = {mapping[k] for k in src_map}

    def main_child_dir(bone, is_dst):
        """主链方向：优先取「也被映射到」的那个子骨，否则取最近的子骨。"""
        best = None
        for c in bone.children:
            if is_dst:
                if c.name not in dst_names:
                    continue
            else:
                if _bone_key(c.name) not in mapping:
                    continue
            d = (c.head_local - bone.head_local)
            if d.length > 1e-5:
                return d
        for c in bone.children:
            d = (c.head_local - bone.head_local)
            if d.length > 1e-5 and (best is None or d.length < best.length):
                best = d
        return best

    rest = {}
    rest_head = {}
    rest_dir = {}
    for key, bone_name in src_map.items():
        b = src.data.bones[bone_name]
        m = src_m @ b.matrix_local
        rest[key] = m.to_quaternion()
        rest_head[key] = m.to_translation()
        # 静止时的「肢体方向」= 本骨 head -> 主链子骨 head
        d = main_child_dir(b, False)
        if d is not None:
            rest_dir[key] = (src_m.to_3x3() @ d)

    # 静止朝向对齐
    # ----------------
    # 数学上正确的搬运公式是：目标世界旋转 = Δ · R_dst_rest
    #   Δ = R_src_world · R_src_rest⁻¹（相对源骨静止姿势的世界增量）
    # 因为 HS2 骨的 R_dst_rest 就是单位矩阵，所以 world = Δ。
    # 以前这里额外加了「主链子骨方向差」(rotation_difference)，那玩意只对齐
    # 了骨骼的**朝向轴**、没管绕这个轴的 roll（扭转），脚踝就是这么被拧歪的
    # —— 表现是「脚背朝奇怪的方向」。默认关掉，需要时可用 basis_align 打开。
    align = {}
    for key, bone_name in src_map.items():
        dst_name = mapping[key]
        align[dst_name] = Quaternion()

    if scale is None:
        src_h = max((rest_head[k].z for k in rest_head), default=1.0)
        dst_h = max((b.head_local.z for b in dst.data.bones), default=1.0)
        scale = (dst_h / src_h) if abs(src_h) > 1e-6 else 1.0

    order = []
    for key in src_map:
        dst_name = mapping[key]
        d = 0
        cur = dst.data.bones[dst_name]
        while cur.parent is not None:
            d += 1
            cur = cur.parent
        order.append((d, key, dst_name))
    order.sort()

    out = {mapping[k]: {"rot": [], "pos": []} for _d, k, _n in order}
    out.setdefault("cf_J_Hips", {"rot": [], "pos": []})
    hips_key = "Hips" if "Hips" in src_map else None
    prev_q = {}

    for f in range(f0, f1 + 1):
        scn.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = src.evaluated_get(dg)
        worlds = {}
        for _d, key, dst_name in order:
            pb = ev.pose.bones.get(src_map[key])
            if pb is None:
                continue
            world_q = (src_m @ pb.matrix).to_quaternion()
            delta = world_q @ rest[key].inverted()      # 世界空间增量
            world = delta @ align.get(dst_name, Quaternion())
            worlds[dst_name] = dst_q.inverted() @ world @ dst_q
        for _d, key, dst_name in order:
            w = worlds.get(dst_name)
            if w is None:
                continue
            parent = dst.data.bones[dst_name].parent
            pw = worlds.get(parent.name, Quaternion()) if parent else Quaternion()
            q = pw.inverted() @ w
            prev = prev_q.get(dst_name)
            if prev is not None and prev.dot(q) < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
            prev_q[dst_name] = q
            out[dst_name]["rot"].append((q.w, q.x, q.y, q.z))

        if hips_key and "cf_J_Hips" in out:
            pb = ev.pose.bones.get(src_map[hips_key])
            if pb is not None:
                hp = (src_m @ pb.matrix).to_translation()
                d = (hp - rest_head[hips_key]) * scale
                d = (dst.matrix_world.to_quaternion() @ (d / max(
                    dst.matrix_world.to_scale().x, 1e-6)))
                out["cf_J_Hips"]["pos"].append((d.x, d.y, d.z))
            else:
                out["cf_J_Hips"]["pos"].append((0.0, 0.0, 0.0))

    for name in list(out):
        tr = out[name]
        if not tr.get("rot"):
            out.pop(name)
            continue
        if not tr.get("pos"):
            tr.pop("pos", None)
    return out, frames
