"""动作层的剪辑工具：镜像、变速、贴地。

全部走「采样 → 改 → 重写」这条路：先把 Action 逐帧求值成
``matrix_basis``（Blender 自己算，分层 action 也不用特殊处理），在采样数据上
做插值/镜像，最后重新写成 fcurve。好处是任何 Action 都能这么处理，
不用去啃 fcurve 的通道结构。
"""
import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import compat

# --------------------------------------------------------------------------
# 采样 / 写回
# --------------------------------------------------------------------------

def _assign(arm, act):
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = act
    compat.ensure_slot(act, arm)


def action_range(act):
    lo, hi = None, None
    try:
        lo, hi = act.frame_range
    except Exception:
        pass
    if lo is None:
        curves = compat.bag(act)
        for fc in (curves or []):
            for kp in fc.keyframe_points:
                f = kp.co[0]
                lo = f if lo is None else min(lo, f)
                hi = f if hi is None else max(hi, f)
    return int(round(lo or 1)), int(round(hi or 1))


def sample_action(arm, act, start=None, end=None):
    """逐帧求值 → {骨名: [(loc, quat, scale), ...]}"""
    f0, f1 = action_range(act)
    start = int(f0 if start is None else start)
    end = int(f1 if end is None else end)
    _assign(arm, act)
    scn = bpy.context.scene
    names = [pb.name for pb in arm.pose.bones]
    data = {n: [] for n in names}
    for f in range(start, end + 1):
        scn.frame_set(f)
        for pb in arm.pose.bones:
            loc, q, scl = pb.matrix_basis.decompose()
            data[pb.name].append((tuple(loc), (q.w, q.x, q.y, q.z),
                                  tuple(scl)))
    return data, start, end


def write_action(arm, name, data, start=1, eps=1e-6):
    """把采样数据写成 Action（只写真正有变化的骨）。"""
    act = compat.new_action(arm, name)
    curves = compat.bag(act, arm)

    groups = {}
    if hasattr(act, "groups"):
        for n in data:
            try:
                groups[n] = act.groups.new(n)
            except Exception:
                break

    for bname, frames in data.items():
        if not frames:
            continue
        loc_max = 0.0
        rot_max = 0.0
        scl_max = 0.0
        for loc, q, scl in frames:
            loc_max = max(loc_max, abs(loc[0]), abs(loc[1]), abs(loc[2]))
            rot_max = max(rot_max, abs(q[0] - 1.0), abs(q[1]), abs(q[2]),
                          abs(q[3]))
            scl_max = max(scl_max, abs(scl[0] - 1.0), abs(scl[1] - 1.0),
                          abs(scl[2] - 1.0))
        if loc_max < eps and rot_max < eps and scl_max < eps:
            continue
        g = groups.get(bname)
        base = 'pose.bones["%s"]' % bname
        if loc_max >= eps:
            for c in range(3):
                compat.fill(compat.new_fcurve(curves, base + ".location", c,
                                              g.name if g else None),
                            [v[0][c] for v in frames], start=start)
        if rot_max >= eps:
            for c in range(4):
                compat.fill(compat.new_fcurve(
                    curves, base + ".rotation_quaternion", c,
                    g.name if g else None),
                    [v[1][c] for v in frames], start=start)
        if scl_max >= eps:
            for c in range(3):
                compat.fill(compat.new_fcurve(curves, base + ".scale", c,
                                              g.name if g else None),
                            [v[2][c] for v in frames], start=start)

    for bname in data:
        pb = arm.pose.bones.get(bname)
        if pb is not None:
            pb.rotation_mode = "QUATERNION"
    return act


# --------------------------------------------------------------------------
# 镜像
# --------------------------------------------------------------------------

def _q(i):
    return Quaternion((i[0], i[1], i[2], i[3]))


_SWAP = (("_L", "_R"), ("_R", "_L"))


def _mirror_name(n):
    for a, b in _SWAP:
        if n.endswith(a):
            return n[:-len(a)] + b
        if n.endswith(b):
            return n[:-len(b)] + a
    return n


def mirror(arm, act, name=None):
    """左右镜像（X 轴）。位置 x 取反、旋转的 y/z 分量取反，左右骨互换。"""
    data, f0, _f1 = sample_action(arm, act)
    out = {}
    for bname, frames in data.items():
        tgt = _mirror_name(bname)
        mix = []
        for loc, q, scl in frames:
            w, x, y, z = q
            mix.append(((-loc[0], loc[1], loc[2]),
                        (w, x, -y, -z),
                        (scl[0], scl[1], scl[2])))
        out[tgt] = mix
    return write_action(arm, name or (act.name + "_mirror"), out, start=f0)


# --------------------------------------------------------------------------
# 变速 / 贴地
# --------------------------------------------------------------------------

def retime(arm, act, factor=1.0, name=None):
    """整体变速：factor > 1 更慢（帧数变多），< 1 更快。"""
    data, f0, f1 = sample_action(arm, act)
    n = f1 - f0 + 1
    new_n = max(2, int(round(n * factor)))
    out = {}
    for bname, frames in data.items():
        mix = []
        for i in range(new_n):
            t = i / float(max(new_n - 1, 1)) * (n - 1)
            i0 = int(math.floor(t))
            i1 = min(n - 1, i0 + 1)
            a = t - i0
            l0, q0, s0 = frames[i0]
            l1, q1, s1 = frames[i1]
            qa = _q(q0)
            qb = _q(q1)
            if qa.dot(qb) < 0.0:
                qb = Quaternion((-qb.w, -qb.x, -qb.y, -qb.z))
            qq = qa.slerp(qb, a)
            mix.append((tuple(l0[c] + (l1[c] - l0[c]) * a for c in range(3)),
                        (qq.w, qq.x, qq.y, qq.z),
                        tuple(s0[c] + (s1[c] - s0[c]) * a for c in range(3))))
        out[bname] = mix
    act2 = write_action(arm, name or (act.name + "_x%g" % factor), out,
                        start=f0)
    scn = bpy.context.scene
    scn.frame_start = f0
    scn.frame_end = f0 + new_n - 1
    return act2


FOOT_BONES = ("cf_J_Toes01_L", "cf_J_Toes01_R", "cf_J_Foot01_L",
              "cf_J_Foot01_R", "cf_J_Foot02_L", "cf_J_Foot02_R")


def ground(arm, act=None, root="cf_J_Root", target=0.0):
    """把整段动作的最低点抬到地面上（默认 z=0）。

    只动 root 骨的位置通道，姿态一点不改 —— 挪完还能继续 K。
    """
    if act is None:
        act = arm.animation_data.action if arm.animation_data else None
    if act is None:
        return None
    data, f0, f1 = sample_action(arm, act)
    _assign(arm, act)
    scn = bpy.context.scene
    bones = [b for b in FOOT_BONES if arm.pose.bones.get(b)] or [root]
    lo = None
    for f in range(f0, f1 + 1):
        scn.frame_set(f)
        for bn in bones:
            pb = arm.pose.bones.get(bn)
            if pb is None:
                continue
            for p in (pb.head, pb.tail):
                z = (arm.matrix_world @ p).z
                lo = z if lo is None else min(lo, z)
    if lo is None:
        return act
    dz = target - lo
    if abs(dz) < 1e-5:
        return act
    pb = arm.pose.bones.get(root)
    if pb is None:
        return act
    m = arm.matrix_world @ pb.bone.matrix_local
    delta = m.to_3x3().inverted() @ Vector((0.0, 0.0, dz))
    for frames in [data.get(root)]:
        if not frames:
            continue
        for i, (loc, q, scl) in enumerate(frames):
            frames[i] = ((loc[0] + delta.x, loc[1] + delta.y,
                          loc[2] + delta.z), q, scl)
    act2 = write_action(arm, act.name + "_ground", data, start=f0)
    return act2

def _yaw_angle(q):
    """四元数绕 Blender Z 轴的扭转角。"""
    w, z = q.w, q.z
    n = math.hypot(w, z)
    if n < 1e-12:
        return 0.0
    a = 2.0 * math.atan2(z / n, w / n)
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def _signed(q, prev):
    if prev is not None and prev.dot(q) < 0.0:
        return Quaternion((-q.w, -q.x, -q.y, -q.z))
    return q


def root_motion(arm, act=None, horizontal=True, turn=True, vertical=False,
                root="p_cf_anim", hips="cf_J_Hips", name=None):
    """把 Hips 的位移/转身搬到根骨，姿态和原来逐帧完全一致。

    ``horizontal`` 提取前后左右位移，``turn`` 提取绕竖直轴的转身，
    ``vertical`` 提取跳跃高度。没提取的分量继续留在 Hips 上。
    """
    if act is None:
        act = arm.animation_data.action if arm.animation_data else None
    if act is None:
        return None
    pb_root = arm.pose.bones.get(root)
    if pb_root is None:
        pb_root = arm.pose.bones.get("cf_J_Root")
    pb_hips = arm.pose.bones.get(hips)
    if pb_root is None or pb_hips is None:
        return act
    root = pb_root.name

    data, f0, f1 = sample_action(arm, act)
    _assign(arm, act)
    scn = bpy.context.scene
    world_root, world_hips, world_parent = [], [], []
    for f in range(f0, f1 + 1):
        scn.frame_set(f)
        world_root.append(pb_root.matrix.copy())
        world_hips.append(pb_hips.matrix.copy())
        world_parent.append(pb_hips.parent.matrix.copy()
                            if pb_hips.parent else Matrix.Identity(4))

    h0 = world_hips[0]
    p0 = h0.to_translation()
    q0 = h0.to_quaternion().inverted()
    parent_rest = Matrix.Identity(4)
    if pb_hips.parent is not None:
        parent_rest = (pb_hips.parent.bone.matrix_local.inverted()
                       @ pb_hips.bone.matrix_local)
    root_rest = pb_root.bone.matrix_local.inverted()

    roots, hips_frames = [], []
    prev_r = prev_h = None
    for i in range(len(world_hips)):
        h = world_hips[i]
        p = h.to_translation()
        q_rel = h.to_quaternion() @ q0
        yaw = _yaw_angle(q_rel) if turn else 0.0
        t = Vector((0.0, 0.0, 0.0))
        if horizontal:
            t.x, t.y = p.x - p0.x, p.y - p0.y
        if vertical:
            t.z = p.z - p0.z

        delta = Matrix.Translation(t)
        if abs(yaw) > 1e-9:
            delta = (delta @ Matrix.Translation(p0)
                     @ Matrix.Rotation(yaw, 4, "Z")
                     @ Matrix.Translation(-p0))

        rb = root_rest @ (delta @ world_root[i])
        loc, q, scl = rb.decompose()
        q = _signed(q, prev_r)
        prev_r = q
        roots.append((tuple(loc), (q.w, q.x, q.y, q.z), tuple(scl)))

        new_parent = delta @ world_parent[i]
        hb = (new_parent @ parent_rest).inverted() @ h
        loc, q, scl = hb.decompose()
        q = _signed(q, prev_h)
        prev_h = q
        hips_frames.append((tuple(loc), (q.w, q.x, q.y, q.z), tuple(scl)))

    data[root] = roots
    data[pb_hips.name] = hips_frames
    return write_action(arm, name or (act.name + "_root"), data, start=f0)
