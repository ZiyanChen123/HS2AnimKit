"""把解码出来的动作写成 Blender 的 Action。

做法：逐帧算出每个骨骼的世界矩阵 -> 换算成 pose 矩阵 -> 反解 matrix_basis。
这样和游戏里的结果逐帧一致，不受 Blender 骨骼 roll 的影响。
"""

import bpy
from mathutils import Matrix, Quaternion, Vector

from .ripper import mathutil as mu


def _mat(m):
    return Matrix(((m[0][0], m[0][1], m[0][2], m[0][3]),
                   (m[1][0], m[1][1], m[1][2], m[1][3]),
                   (m[2][0], m[2][1], m[2][2], m[2][3]),
                   (0.0, 0.0, 0.0, 1.0)))


def apply_clip(arm_obj, skel, names, clip, scale=None, action_name=None,
               start_frame=0):
    """生成 Action 并挂到 arm_obj 上。返回 (action, info)。"""
    scale = scale if scale is not None else float(arm_obj.get("hs2_scale", 0.1))
    n = clip.frame_count
    if n <= 0:
        raise RuntimeError("这个动作没有帧")

    bones = skel.bones
    nb = len(bones)
    fps = clip.fps or 30.0

    # 每根骨的静止局部 TRS（Unity 空间，已乘缩放）
    rest_local = []
    for b in bones:
        pos = (b.pos[0] * scale, b.pos[1] * scale, b.pos[2] * scale)
        rest_local.append((pos, b.rot, b.scale))

    # 每帧的轨道取值
    tracks = clip.tracks
    track_idx = {}
    for i, b in enumerate(bones):
        tr = tracks.get(b.path)
        if tr:
            track_idx[i] = tr

    # Blender 静止矩阵与父级关系
    bl_rest = []
    for nm in names:
        bn = arm_obj.data.bones.get(nm) if nm else None
        bl_rest.append(bn.matrix_local.copy() if bn else Matrix())
    rel_rest = []
    for i, b in enumerate(bones):
        if b.parent >= 0:
            rel_rest.append(bl_rest[b.parent].inverted() @ bl_rest[i])
        else:
            rel_rest.append(bl_rest[i].copy())

    # 预先把轨道转成 (帧 -> 局部 TRS) 的快速查表
    data = {}
    for i, tr in track_idx.items():
        pos = tr.get("pos")
        rot = tr.get("rot")
        scl = tr.get("scale")
        data[i] = (pos, rot, scl)

    # 逐帧解算
    locs = {i: [] for i in data}
    quats = {i: [] for i in data}
    scales = {i: [] for i in data}

    prev_q = {}
    world = [None] * nb
    prev_basis_q = {}

    for f in range(n):
        for i, b in enumerate(bones):
            pos, rot, scl = rest_local[i]
            tr = data.get(i)
            if tr is not None:
                pos_t, rot_t, scl_t = tr
                if pos_t is not None:
                    pos = (pos_t[f][0] * scale, pos_t[f][1] * scale, pos_t[f][2] * scale)
                if rot_t is not None:
                    rot = tuple(rot_t[f])
                if scl_t is not None:
                    scl = tuple(scl_t[f])
            local = mu.trs_to_mat(pos, rot, scl)
            if b.parent >= 0:
                world[i] = mu.mat_mul(world[b.parent], local)
            else:
                world[i] = local

        for i in data:
            b = bones[i]
            a_i = _mat(mu.mat_to_blender(world[i]))
            if b.parent >= 0:
                a_p = _mat(mu.mat_to_blender(world[b.parent]))
            else:
                a_p = Matrix()
            basis = rel_rest[i].inverted() @ a_p.inverted() @ a_i

            loc, rot_q, scl = basis.decompose()
            prev = prev_basis_q.get(i)
            if prev is not None and prev.dot(rot_q) < 0.0:
                rot_q = Quaternion((-rot_q.w, -rot_q.x, -rot_q.y, -rot_q.z)).normalized()
            prev_basis_q[i] = rot_q
            locs[i].append((loc.x, loc.y, loc.z))
            quats[i].append((rot_q.w, rot_q.x, rot_q.y, rot_q.z))
            scales[i].append((scl.x, scl.y, scl.z))

    from . import compat

    action = compat.new_action(arm_obj, action_name or ("HS2_" + clip.name))
    curves = compat.bag(action, arm_obj)

    groups = {}
    if hasattr(action, "groups"):
        for i in data:
            if names[i] and names[i] not in groups:
                try:
                    groups[names[i]] = action.groups.new(names[i])
                except Exception:
                    break

    def _curve(path, index, group, values, interp="LINEAR"):
        fc = compat.new_fcurve(curves, path, index,
                               group.name if group else None)
        fc.keyframe_points.add(count=len(values))
        flat = []
        for k, v in enumerate(values):
            flat.extend((start_frame + k, v))
        fc.keyframe_points.foreach_set("co", flat)
        for kp in fc.keyframe_points:
            kp.interpolation = interp
        fc.update()
        return fc

    for i in data:
        nm = names[i]
        if not nm:
            continue
        g = groups.get(nm)
        pos_t, rot_t, scl_t = data[i]
        base = 'pose.bones["%s"]' % nm

        if pos_t is not None:
            for c in range(3):
                _curve(base + ".location", c, g, [v[c] for v in locs[i]])
        if rot_t is not None:
            for c in range(4):
                _curve(base + ".rotation_quaternion", c, g, [v[c] for v in quats[i]])
        if scl_t is not None:
            for c in range(3):
                _curve(base + ".scale", c, g, [v[c] for v in scales[i]])

    # 姿态模式设为四元数，避免欧拉跳变
    for i in data:
        if not names[i]:
            continue
        pb = arm_obj.pose.bones.get(names[i])
        if pb:
            pb.rotation_mode = "QUATERNION"

    info = {
        "frames": n,
        "bone_tracks": len(data),
        "fcurves": compat.fcurve_count(action, arm_obj),
        "duration": clip.duration,
        "fps": fps,
    }
    return action, info
