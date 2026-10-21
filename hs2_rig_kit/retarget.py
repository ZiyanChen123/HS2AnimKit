"""外部动作重定向：VMD / Mixamo FBX → HS2 骨架。

两条路都归到同一个出口：算出每根 HS2 骨的**局部旋转**（和根骨位移），
批量写成 fcurve，再交给 `rig.key_targets` 镜像到控制层。

坐标系与单位
------------
* VMD：MMD 是 Y-up 左手、前后与 Unity 相反（`mmd_quat_to_blender` 里
  补了 spec "-x,y,-z" 那步绕 Y 180° 的共轭），再换基到 Blender。
  MMD 模型高约 20 单位，HS2 高 16 单位，所以 1 MMD 单位 ≈ 0.08 m。
* Mixamo：按「本骨 → 主链子骨」的肢体方向重定向，源骨相对静止的
  扭转（twist）绕肢体方向保留。脚骨例外：HS2 的 Foot01/Toes01 才是
  真正的脚和脚趾（Foot02 是踝下校正骨），只搬相对静止的旋转增量，
  不按骨头方向强行重定向，免得把脚踝拧成麻花。

VMD 仍 100% 忠实还原数据，不做贴地；Mixamo 导入额外做鞋底贴地、
足部 IK，避免骨骼比例不同造成浮空/滑步。脚趾保留 Mixamo 自己的
ToeBase → cf_J_Toes01 动画，不套 HS2PE 的 Ankles 公式。
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
    "LeftToeBase": "cf_J_Toes01_L",
    "RightToeBase": "cf_J_Toes01_R",
}

# 每根骨用「本骨 → 主链子骨」的方向来定姿态。
# HS2 的骨骼 Y 轴大多朝前，不是沿肢体方向，所以不能直接搬 localRotation；
# 但肢体方向（head → 子骨 head）在两边都明确，按方向重定向才不会抬手/拧腿。
MIXAMO_CHILD = {
    "Hips": "Spine",
    "Spine": "Spine1",
    "Spine1": "Spine2",
    "Spine2": "Neck",
    "Neck": "Head",
    "LeftShoulder": "LeftArm",
    "RightShoulder": "RightArm",
    "LeftArm": "LeftForeArm",
    "RightArm": "RightForeArm",
    "LeftForeArm": "LeftHand",
    "RightForeArm": "RightHand",
    "LeftHand": "LeftHandMiddle1",
    "RightHand": "RightHandMiddle1",
    "LeftUpLeg": "LeftLeg",
    "RightUpLeg": "RightLeg",
    "LeftLeg": "LeftFoot",
    "RightLeg": "RightFoot",
    "LeftHandThumb1": "LeftHandThumb2",
    "LeftHandThumb2": "LeftHandThumb3",
    "RightHandThumb1": "RightHandThumb2",
    "RightHandThumb2": "RightHandThumb3",
}

for _pre in ("Index", "Middle", "Ring", "Pinky"):
    for _side in ("Left", "Right"):
        MIXAMO_CHILD[_side + "Hand" + _pre + "1"] = _side + "Hand" + _pre + "2"
        MIXAMO_CHILD[_side + "Hand" + _pre + "2"] = _side + "Hand" + _pre + "3"

# HS2 的脚有 Foot01 / Foot02 / Toes01 三节，但真正的「脚」和「脚趾」是
# Foot01 / Toes01；Foot02 是脚踝下方的校正骨。Mixamo 的 Foot/ToeBase 对应
# Foot01/Toes01，脚骨不参与肢体方向对齐，只搬相对静止的世界旋转增量，
# 这样不会把 HS2 的脚踝静止形状强行扭到 Mixamo 的骨头轴上。
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


def _world_joint(ob, name, pose=True):
    """源骨架关节世界坐标；顺带沿 X 镜像到 HS2 的左右方向。"""
    b = ob.pose.bones.get(name) if pose else ob.data.bones.get(name)
    if b is None:
        return None
    v = ob.matrix_world @ (b.head if pose else b.head_local)
    v.x = -v.x
    return v


def _local_quat(pb, world_q):
    """骨架空间世界朝向 -> 该骨的局部旋转（吃父骨当前姿态）。"""
    db = pb.bone
    br = db.matrix_local.to_quaternion()
    if db.parent is None:
        return br.inverted() @ world_q
    par = pb.parent
    pr = par.bone.matrix_local.to_quaternion()
    pq = par.matrix.to_quaternion()
    return (pq @ pr.inverted() @ br).inverted() @ world_q


def foot_ik_pass(arm, src, act, start=1, end=None, scale=None):
    """可选后处理：用两骨 IK 把脚踝拉回源动作的脚部轨迹。

    FK 方向重定向能对上大腿/小腿角度，但两边腿长不同，脚会滑一点。
    这里不再改世界旋转，而是：
      1. 以目标骨架当前 FK 姿势为基础，保留膝盖弯曲方向；
      2. 用大腿/小腿长度解两骨 IK，把脚踝对准「源脚相对静止姿势的位移」；
      3. 只改大腿、小腿的局部旋转，脚骨保持原世界朝向，避免脚掌乱翻。
    目标脚轨迹按身高比例缩放，并以两边各自的静止脚位置为基准，所以
    不会把角色整体抬离地面。
    """
    from . import compat

    if act is None or src is None:
        return 0
    # 有控制器时先关 IK，否则约束会盖住这里写进去的 FK 腿骨旋转。
    rig_mod = None
    had_ik = False
    try:
        from . import rig as rig_mod
        had_ik = rig_mod.has_rig(arm) and rig_mod.ik_state(arm)
        if had_ik:
            rig_mod.set_ik(arm, False)
    except Exception:
        rig_mod = None
        had_ik = False

    scn = bpy.context.scene
    f0 = int(start)
    f1 = int(end if end is not None else (act.frame_range[1]))
    dst_m = arm.matrix_world
    dst_mi = dst_m.inverted()

    if scale is None:
        src_h = max(((src.matrix_world @ b.head_local).z
                     for b in src.data.bones), default=1.0)
        dst_h = max(((dst_m @ b.head_local).z
                     for b in arm.data.bones), default=1.0)
        scale = (dst_h / src_h) if abs(src_h) > 1e-6 else 1.0

    src_names = {}
    for b in src.data.bones:
        key = _bone_key(b.name)
        if key in ("LeftFoot", "RightFoot"):
            src_names[key] = b.name
    if len(src_names) < 2:
        return 0

    sides = {}
    for side, key in (("L", "LeftFoot"), ("R", "RightFoot")):
        src_name = src_names.get(key)
        dst_name = MIXAMO_MAP.get(key)
        if src_name is None or dst_name not in arm.pose.bones:
            return 0
        low_name = MIXAMO_MAP["LeftLeg" if side == "L" else "RightLeg"]
        up_name = MIXAMO_MAP["LeftUpLeg" if side == "L" else "RightUpLeg"]
        db = arm.data.bones
        if not (up_name in db and low_name in db and dst_name in db):
            return 0
        sr = _world_joint(src, src_name, pose=False)
        tr = dst_m @ db[dst_name].head_local
        l1 = (db[low_name].head_local - db[up_name].head_local).length
        l2 = (db[dst_name].head_local - db[low_name].head_local).length
        sides[side] = {
            "src": src_name, "up": up_name, "low": low_name,
            "foot": dst_name, "sr": sr, "tr": tr, "l1": l1, "l2": l2,
        }

    changes = {d["up"]: [] for d in sides.values()}
    for d in sides.values():
        changes[d["low"]] = []
        changes[d["foot"]] = []
    prev = {}
    old_frame = scn.frame_current

    for f in range(f0, f1 + 1):
        scn.frame_set(f)
        bpy.context.view_layer.update()
        for side, d in sides.items():
            up = arm.pose.bones[d["up"]]
            low = arm.pose.bones[d["low"]]
            foot = arm.pose.bones[d["foot"]]

            sp = _world_joint(src, d["src"], pose=True)
            if sp is None:
                continue
            foot_world = d["tr"] + (sp - d["sr"]) * scale
            target = dst_mi @ foot_world
            hip = up.head.copy()
            knee = low.head.copy()
            ankle = foot.head.copy()

            vec = target - hip
            dist = vec.length
            if dist < 1e-5:
                continue
            reach = d["l1"] + d["l2"] - 1e-4
            dist = min(max(dist, abs(d["l1"] - d["l2"]) + 1e-4), reach)
            axis = vec.normalized()
            target = hip + axis * dist

            # 膝盖往当前 FK 膝盖那一侧弯，保留源动作的膝盖方向。
            pole = knee - (hip + axis * (knee - hip).dot(axis))
            if pole.length < 1e-5:
                pole = axis.cross(Vector((0.0, 1.0, 0.0)))
                if pole.length < 1e-5:
                    pole = axis.cross(Vector((1.0, 0.0, 0.0)))
            pole.normalize()
            a = (d["l1"] * d["l1"] - d["l2"] * d["l2"]
                 + dist * dist) / (2.0 * dist)
            h = math.sqrt(max(0.0, d["l1"] * d["l1"] - a * a))
            knee_new = hip + axis * a + pole * h

            foot_old_q = foot.matrix.to_quaternion()
            up_old = (knee - hip).normalized()
            up_new = (knee_new - hip).normalized()
            q_up = up_old.rotation_difference(up_new) @ up.matrix.to_quaternion()
            q_up = _local_quat(up, q_up)
            up.rotation_mode = "QUATERNION"
            up.rotation_quaternion = q_up
            bpy.context.view_layer.update()

            low_old = (foot.head - low.head).normalized()
            low_new = (target - knee_new).normalized()
            q_low = low_old.rotation_difference(low_new) @ low.matrix.to_quaternion()
            q_low = _local_quat(low, q_low)
            low.rotation_mode = "QUATERNION"
            low.rotation_quaternion = q_low
            bpy.context.view_layer.update()

            q_foot = _local_quat(foot, foot_old_q)
            foot.rotation_mode = "QUATERNION"
            foot.rotation_quaternion = q_foot

            for name, q in ((up.name, q_up), (low.name, q_low),
                            (foot.name, q_foot)):
                p = prev.get(name)
                if p is not None and p.dot(q) < 0.0:
                    q = Quaternion((-q.w, -q.x, -q.y, -q.z))
                prev[name] = q
                changes[name].append(tuple(q))

    curves = compat.bag(act, arm)
    for name, vals in changes.items():
        if not vals:
            continue
        base = 'pose.bones["%s"].rotation_quaternion' % name
        for i in range(4):
            fc = compat.find_fcurve(curves, base, i)
            if fc is None:
                continue
            kps = {round(kp.co.x, 3): kp for kp in fc.keyframe_points}
            for k, f in enumerate(range(f0, f0 + len(vals))):
                kp = kps.get(round(float(f), 3))
                if kp is not None:
                    kp.co.y = vals[k][i]
            fc.update()
    scn.frame_set(old_frame)
    if had_ik and rig_mod is not None:
        try:
            rig_mod.set_ik(arm, True)
        except Exception:
            pass
    return len(changes.get(sides["L"]["up"], []))


def _foot_mesh_verts(arm):
    """找角色身上带脚骨权重的网格顶点，用来量鞋底离地多少。"""
    best = None
    best_n = 0
    for ob in bpy.data.objects:
        if ob.type != "MESH" or ob.data is None:
            continue
        if ob.parent != arm and not any(
                m.type == "ARMATURE" and m.object == arm for m in ob.modifiers):
            continue
        gi = {g.index for g in ob.vertex_groups
              if g.name.startswith(("cf_J_Foot", "cf_J_Toe"))}
        if not gi:
            continue
        n = 0
        for v in ob.data.vertices:
            if any(g.group in gi and g.weight > 0.05 for g in v.groups):
                n += 1
        if n > best_n:
            best, best_n = ob, n
    if best is None:
        for ob in bpy.data.objects:
            if (ob.type == "MESH" and ob.data is not None
                    and (ob.parent == arm or any(
                        m.type == "ARMATURE" and m.object == arm
                        for m in ob.modifiers))):
                return ob, None
        return None, None
    gi = {g.index for g in best.vertex_groups
          if g.name.startswith(("cf_J_Foot", "cf_J_Toe"))}
    ids = [v.index for v in best.data.vertices
           if any(g.group in gi and g.weight > 0.05 for g in v.groups)]
    return best, ids


def ground_contact_pass(arm, act, start=1, end=None):
    """按整段动画里最低的鞋底，给 Hips 加一个固定高度偏移。

    Mixamo 的骨骼比例和 HS2 不同，直接搬 Hips 高度时站立会整体浮空/陷地。
    这里逐帧用带脚骨权重的网格算真实鞋底 z，抬/降 Hips，
    整段动画只用一个常数偏移，不会破坏跳跃和蹲伏。
    """
    from . import compat

    if act is None:
        return 0.0
    scn = bpy.context.scene
    f0 = int(start)
    f1 = int(end if end is not None else act.frame_range[1])
    old_frame = scn.frame_current
    mesh, ids = _foot_mesh_verts(arm)
    if mesh is None:
        scn.frame_set(old_frame)
        return 0.0

    low = None
    for f in range(f0, f1 + 1):
        scn.frame_set(f)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        ev = mesh.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        if ids:
            z = min((mw @ me.vertices[i].co).z for i in ids)
        else:
            z = min((mw @ v.co).z for v in me.vertices)
        ev.to_mesh_clear()
        low = z if low is None else min(low, z)
    if low is None:
        scn.frame_set(old_frame)
        return 0.0
    offset = -low
    if abs(offset) < 0.0005 or abs(offset) > 0.5:
        scn.frame_set(old_frame)
        return 0.0

    world_off = Vector((0.0, 0.0, offset))
    q = arm.matrix_world.to_quaternion()
    sc = arm.matrix_world.to_scale()
    local_off = q.inverted() @ world_off
    local_off = Vector((local_off.x / max(sc.x, 1e-6),
                        local_off.y / max(sc.y, 1e-6),
                        local_off.z / max(sc.z, 1e-6)))
    curves = compat.bag(act, arm)
    base = 'pose.bones["cf_J_Hips"].location'
    for i in range(3):
        if abs(local_off[i]) < 1e-9:
            continue
        fc = compat.find_fcurve(curves, base, i)
        if fc is None:
            continue
        for kp in fc.keyframe_points:
            kp.co.y += local_off[i]
        fc.update()
    scn.frame_set(old_frame)
    return offset


def mixamo_tracks(src, dst, start=None, end=None, with_fingers=True,
                  scale=None):
    """把源骨架的动画转成 HS2 的局部旋转。返回 (tracks, frames)。

    HS2 的骨骼 Y 轴大多是「朝前」而不是沿肢体方向，所以不能直接搬
    localRotation；旧实现按世界旋转增量搬，结果手臂会从 A-pose 再抬一次。
    这里改用和 MMD 导入相同的思路：重定向**肢体方向**（本骨 head → 主链子骨
    head），源骨的 roll/扭转作为绕肢体方向的 twist 一起带过去。这样 HS2 的
    静止姿势和手臂角度不会被重复叠加。脚骨是例外：HS2 的 Foot02 是踝下
    校正骨，真正的脚/趾是 Foot01/Toes01。脚骨只搬相对静止的旋转增量，
    不按方向对齐，否则会破坏 HS2 的脚踝静止形状。
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

    # Mixamo 的左右和 HS2 是反的（Mixamo 左臂在 +X，HS2 左臂在 -X）。
    # 源姿势先沿 X 轴镜像，再对齐肢体方向，否则左右臂会往身体里交叉。
    src_m = Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0)) @ src.matrix_world
    dst_m = dst.matrix_world
    dst_obj_q = dst_m.to_quaternion()

    src_rest_q = {}
    src_rest_head = {}
    src_rest_dir = {}
    dst_rest_q = {}
    dst_rest_arm_q = {b.name: b.matrix_local.to_quaternion()
                      for b in dst.data.bones}
    for key, bone_name in src_map.items():
        b = src.data.bones[bone_name]
        m = src_m @ b.matrix_local
        src_rest_q[key] = m.to_quaternion()
        src_rest_head[key] = m.to_translation()
        dst_name = mapping[key]
        db = dst.data.bones[dst_name]
        dst_rest_q[key] = (dst_m @ db.matrix_local).to_quaternion()
        child_key = MIXAMO_CHILD.get(key)
        if child_key in src_map and mapping[child_key] in dst.pose.bones:
            cb = src.data.bones[src_map[child_key]]
            d = (src_m @ cb.head_local) - (src_m @ b.head_local)
        else:
            d = None
        if d is not None and d.length > 1e-6:
            src_rest_dir[key] = d.normalized()

    if scale is None:
        src_h = max((src_rest_head[k].z for k in src_rest_head), default=1.0)
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

    depth = {}
    for b in dst.data.bones:
        d = 0
        cur = b
        while cur.parent is not None:
            d += 1
            cur = cur.parent
        depth[b.name] = d
    bone_order = sorted((b.name for b in dst.data.bones),
                        key=lambda n: depth[n])

    out = {mapping[k]: {"rot": [], "pos": []} for _d, k, _n in order}
    out.setdefault("cf_J_Hips", {"rot": [], "pos": []})
    hips_key = "Hips" if "Hips" in src_map else None
    prev_q = {}

    for f in range(f0, f1 + 1):
        scn.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = src.evaluated_get(dg)
        wanted = {}
        for _d, key, dst_name in order:
            pb = ev.pose.bones.get(src_map[key])
            if pb is None:
                continue
            q_pose = (src_m @ pb.matrix).to_quaternion()
            q_rest = src_rest_q[key]
            q_dst_rest = dst_rest_q[key]
            child_key = MIXAMO_CHILD.get(key)
            dst_child = mapping.get(child_key)
            sd = td = None
            if (child_key in src_map and dst_child in dst.pose.bones
                    and key in src_rest_dir):
                cp = ev.pose.bones.get(src_map[child_key])
                if cp is not None:
                    sd = (src_m @ cp.head) - (src_m @ pb.head)
                    tdb = dst.data.bones[dst_name]
                    tcb = dst.data.bones[dst_child]
                    td = (dst_m @ tcb.head_local) - (dst_m @ tdb.head_local)
            if (sd is not None and td is not None and key in src_rest_dir
                    and sd.length > 1e-6 and td.length > 1e-6):
                sd.normalize()
                td.normalize()
                ds0 = src_rest_dir[key]
                # 源骨相对静止的完整旋转，拆成「肢体方向摆动 + 绕肢体扭转」
                delta = q_pose @ q_rest.inverted()
                swing_src = ds0.rotation_difference(sd)
                twist = delta @ swing_src.inverted()
                # 目标肢体方向直接对齐源姿势方向；扭转保留 roll
                swing_dst = td.rotation_difference(sd)
                world = twist @ swing_dst @ q_dst_rest
                wanted[dst_name] = dst_obj_q.inverted() @ world
                continue
            # 末端骨（头、脚趾、指尖）没有主链子骨：只搬相对静止增量
            world = (q_pose @ q_rest.inverted()) @ q_dst_rest
            wanted[dst_name] = dst_obj_q.inverted() @ world

        # 未映射的中间骨（ShoulderIK 等）必须跟着父骨动，不能按静止处理，
        # 否则子骨的局部旋转会算错，肩/手臂会整体偏出去。
        for name in bone_order:
            if name in wanted:
                continue
            b = dst.data.bones[name]
            if b.parent is None:
                wanted[name] = dst_rest_arm_q[name]
            else:
                rel = (dst_rest_arm_q[b.parent.name].inverted()
                       @ dst_rest_arm_q[name])
                wanted[name] = wanted[b.parent.name] @ rel

        for _d, key, dst_name in order:
            w = wanted.get(dst_name)
            if w is None:
                continue
            db = dst.data.bones[dst_name]
            if db.parent is not None:
                pq = wanted[db.parent.name]
                rel = (dst_rest_arm_q[db.parent.name].inverted()
                       @ dst_rest_arm_q[dst_name])
                q = (pq @ rel).inverted() @ w
            else:
                q = dst_rest_arm_q[dst_name].inverted() @ w
            prev = prev_q.get(dst_name)
            if prev is not None and prev.dot(q) < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
            prev_q[dst_name] = q
            out[dst_name]["rot"].append((q.w, q.x, q.y, q.z))

        if hips_key and "cf_J_Hips" in out:
            pb = ev.pose.bones.get(src_map[hips_key])
            if pb is not None:
                hp = (src_m @ pb.matrix).to_translation()
                d = (hp - src_rest_head[hips_key]) * scale
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
