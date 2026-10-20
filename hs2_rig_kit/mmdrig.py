"""虚拟 MMD 骨架 —— 复刻 MMDD v2.2.1（HS2 舞蹈插件）的做法。

MMDD 不是把 VMD 动作直接套到 HS2 骨上，而是：

    1. 在 HS2 骨架上造一棵**虚拟的 MMD 骨架**（位置/层级来自 chara ini）
    2. VMD 驱动这套虚拟骨（旋转 + 位置 + IK + 扭转）
    3. 按 ini 的 BoneWeight，把虚拟骨的世界变换**同步**回 HS2 实骨

本模块的 ``write_curves`` 是逐条对照 ``vmdlib.py`` 的
``MotionController.updatePose / setFrame(indexMode)`` 重写的：

    for 每根骨（按 ini 顺序）:
        localRot = GetAdjustedRotation(VMD 旋转)   # spec(-x,y,-z) + r/a/s + limitAxis
        localPos = GetAdjustedPosition(VMD 位移)   # base + sign*scale*pos
        if IK 骨: 解 IK（MMDIKSolverLeg / MMDIKSolverNoLimit）
        if track 骨: local = SlerpUnclamped(I, src.local, scale) * own
    最后统一做 BoneTwistDisperse

关键点（旧实现没有的）：
* VMD 的**贝塞尔插值曲线**（位置 X/Y/Z + 旋转），不是线性；
* 位移的 Unity 符号 ``(-x, y, -z)``、每骨骼 basePosition、positionScale；
* 手臂轴倾由 ``autoBoneAxisAdjust`` 从父骨派生，不是拍脑袋的 ±10/±42；
* 足 IK 的 ``baseInvQ / legSum / minKneeRot`` 与官方 ``MMDIKSolverLeg`` 一致；
* 足 IK 目标 = 虚拟 ``IK_FOOT`` 骨的世界位置（含 IK_ROOT 的位移/旋转）；
* VMD 的 IK 可见帧（``ik`` 段）逐帧开关；
* D 骨 track 在 IK **之后**执行（官方顺序）。
"""

import math
import os

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from . import boneadj, mmdcfg

DUMMY_PREFIX = "MMD_"
COLL_MMD = "HS2 MMD虚拟"
CON_PREFIX = "MMDD_"

# 1 MMD 单位 = 多少米。MMDD 的换算是 AxisScale(0.832) × 角色缩放(HS2 默认 0.1)。
# 旧实现用 0.08，短了 4%：一段位移 1 m 的舞会差 4 cm，足 IK 目标也跟着偏。
MMD_UNIT = 0.0832

# Unity 四元数 / 向量工具（MMD 和 HS2 都是 Unity 空间，内部统一在这里算）
IDQ = (0.0, 0.0, 0.0, 1.0)


def dummy_name(name):
    return DUMMY_PREFIX + name


def is_dummy(name):
    return name.startswith(DUMMY_PREFIX)


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q):
    n = math.sqrt(sum(c * c for c in q))
    if n < 1e-12:
        return IDQ
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def qrot(q, v):
    p = qmul(qmul(q, (v[0], v[1], v[2], 0.0)), qconj(q))
    return (p[0], p[1], p[2])


def qangle(q):
    """旋转角（弧度，0~π）。"""
    w = max(-1.0, min(1.0, abs(q[3])))
    return 2.0 * math.acos(w)


def q_from_axis_angle(axis, angle):
    n = math.sqrt(axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2)
    if n < 1e-12 or abs(angle) < 1e-12:
        return IDQ
    h = angle * 0.5
    s = math.sin(h) / n
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def q_to_axis_angle(q):
    q = qnorm(q)
    if q[3] < 0.0:
        q = (-q[0], -q[1], -q[2], -q[3])
    ang = 2.0 * math.acos(max(-1.0, min(1.0, q[3])))
    s = math.sqrt(max(0.0, 1.0 - q[3] * q[3]))
    if s < 1e-9:
        return (0.0, 1.0, 0.0), 0.0
    return (q[0] / s, q[1] / s, q[2] / s), ang


def from_to(a, b):
    """等价 Unity Quaternion.FromToRotation(a, b) → (axis, angle)。"""
    na = math.sqrt(sum(c * c for c in a))
    nb = math.sqrt(sum(c * c for c in b))
    if na < 1e-12 or nb < 1e-12:
        return (0.0, 1.0, 0.0), 0.0
    a = (a[0] / na, a[1] / na, a[2] / na)
    b = (b[0] / nb, b[1] / nb, b[2] / nb)
    d = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))
    ang = math.acos(d)
    if ang < 1e-9 or math.isnan(ang):
        return (0.0, 1.0, 0.0), 0.0
    axis = (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])
    n = math.sqrt(sum(c * c for c in axis))
    if n < 1e-9:
        return (0.0, 1.0, 0.0), ang
    return (axis[0] / n, axis[1] / n, axis[2] / n), ang


def slerp(a, b, t):
    """Unity Quaternion.Slerp（最短路径）。"""
    d = sum(x * y for x, y in zip(a, b))
    if d < 0.0:
        b = tuple(-x for x in b)
        d = -d
    if d > 0.9995:
        return qnorm(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))
    th = math.acos(max(-1.0, min(1.0, d)))
    s = math.sin(th)
    return qnorm(tuple(a[i] * math.sin((1 - t) * th) / s +
                       b[i] * math.sin(t * th) / s for i in range(4)))


def slerp_unclamped(a, b, t):
    """Unity Quaternion.SlerpUnclamped（允许 t 超出 [0,1]）。"""
    if abs(t) < 1e-9:
        return a
    if abs(t - 1.0) < 1e-9:
        return b
    d = sum(x * y for x, y in zip(a, b))
    if d < 0.0:
        b = tuple(-x for x in b)
        d = -d
    th = math.acos(max(-1.0, min(1.0, d)))
    if th < 1e-7:
        return qnorm(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))
    s = math.sin(th)
    return qnorm(tuple(a[i] * math.sin((1 - t) * th) / s +
                       b[i] * math.sin(t * th) / s for i in range(4)))


# 坐标转换：Unity (x,y,z) ↔ Blender (x,-z,y)
def u2b_pos(p):
    return (p[0], -p[2], p[1])


def b2u_pos(p):
    return (p[0], p[2], -p[1])


def u2b_quat(q):
    """Unity (x,y,z,w) → Blender (w,x,y,z)（mathutil 返回的仍是 x,y,z,w）。"""
    from .ripper import mathutil as mu
    bx, by, bz, bw = mu.to_blender_quat(q)
    return (bw, bx, by, bz)


# --------------------------------------------------------------------------
# 清理
# --------------------------------------------------------------------------

def clear(arm):
    """删掉虚拟骨架 + 它加的所有约束（游戏骨回到原样）。"""
    if arm is None:
        return
    for pb in arm.pose.bones:
        for c in list(pb.constraints):
            if c.name.startswith(CON_PREFIX):
                pb.constraints.remove(c)

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for ob in list(bpy.context.selected_objects):
        ob.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    ebs = arm.data.edit_bones
    names = [e.name for e in ebs if is_dummy(e.name)]

    def depth(n):
        d, cur = 0, ebs.get(n)
        while cur is not None and cur.parent is not None:
            d += 1
            cur = cur.parent
        return d

    for n in sorted(names, key=depth, reverse=True):
        eb = ebs.get(n)
        if eb is not None:
            ebs.remove(eb)
    bpy.ops.object.mode_set(mode="OBJECT")


def has_dummy(arm):
    return arm is not None and arm.data.bones.get(dummy_name("ROOT")) is not None


# --------------------------------------------------------------------------
# 位置
# --------------------------------------------------------------------------

def _head(arm, name):
    b = arm.data.bones.get(name)
    return b.head_local.copy() if b is not None else None


def _pos_of(arm, bone_def, heads, unit=MMD_UNIT):
    """按 BonePosition 算虚拟骨位置（骨架局部空间，米）。"""
    if not bone_def.pos:
        return None
    kind, val = bone_def.pos
    if kind == "b":
        return heads.get(val[0])
    if kind == "bbs":
        a = heads.get(val[0])
        c = heads.get(val[1])
        if a is None or c is None:
            return None
        return a.lerp(c, val[2])
    if kind in ("bo", "br"):
        base = heads.get(val[0])
        if base is None:
            return None
        if kind == "bo":
            # bo 的偏移是 **Unity** 局部坐标（HS2 prefab 单位），
            # 不能直接加到 Blender 的 (x,y,z) 上：IK_ROOT 的 -0.8Y 是
            # 「向下 8cm」，直接加会变成向后 8cm。
            off = Vector(u2b_pos(val[1])) * unit
            return base + off
        # br：在 Unity 坐标里替换指定轴，再转回 Blender
        u = list(b2u_pos(base))
        for i, v in enumerate(val[1]):
            if v == v:              # 不是 NaN
                u[i] = v * unit
        return Vector(u2b_pos(u))
    return None


# --------------------------------------------------------------------------
# 建虚拟骨架
# --------------------------------------------------------------------------

def build(arm, cfg=None, unit=MMD_UNIT):
    """在 HS2 骨架上建一棵虚拟 MMD 骨架。返回统计信息。"""
    cfg = cfg or mmdcfg.CharaConfig()
    clear(arm)

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for ob in list(bpy.context.selected_objects):
        ob.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    ebs = arm.data.edit_bones

    made = 0
    # 关节校准：按 PrepareBoneBeforeCreateDummyBone 微调算 HS2 骨世界位置，
    # 让虚拟骨架定位点对齐 MMD 静止姿态（腿/手臂 1~3° 轴向差）。
    heads = _prepare_heads(arm, cfg)
    for name in cfg.parents_first():
        b = cfg.bones.get(name)
        if b is None:
            continue
        pos = _pos_of(arm, b, heads, unit)
        if pos is None:
            continue
        eb = ebs.new(dummy_name(name))
        eb.head = pos
        # 虚拟骨取**单位矩阵 rest**（tail 沿 +Y、roll=0），
        # 这样「本地旋转 = 世界旋转」。
        eb.tail = pos + Vector((0.0, 0.05, 0.0))
        eb.use_deform = False
        parent = dummy_name(b.parent) if b.parent else None
        if parent and ebs.get(parent) is not None:
            eb.parent = ebs[parent]
            eb.use_connect = False
        made += 1

    bpy.ops.object.mode_set(mode="OBJECT")

    coll = arm.data.collections_all.get(COLL_MMD)
    if coll is None:
        try:
            coll = arm.data.collections.new(COLL_MMD)
        except Exception:
            coll = None
    if coll is not None:
        for b in arm.data.bones:
            if is_dummy(b.name):
                coll.assign(b)

    return {"bones": made, "config": cfg.name}


# --------------------------------------------------------------------------
# 同步到 HS2 实骨
# --------------------------------------------------------------------------

def sync(arm, cfg):
    """按 BoneWeight 把虚拟骨的世界变换同步回 HS2 骨。

    MMDD 的 SyncFlag 支持 local / global 两种；内置配置都是 global，但
    这里保留 local 分支（有些第三方 ini 会用 ``:l``）。
    """
    n = 0
    for name in cfg.parents_first():
        b = cfg.bones.get(name)
        if b is None or not b.weight:
            continue
        tgt = arm.pose.bones.get(b.weight)
        src = arm.pose.bones.get(dummy_name(name))
        if tgt is None or src is None:
            continue

        local = (b.weight_mode == "local")
        con = tgt.constraints.new("COPY_ROTATION")
        con.name = CON_PREFIX + "ROT_" + name
        con.target = arm
        con.subtarget = src.name
        con.target_space = "LOCAL" if local else "WORLD"
        con.owner_space = "LOCAL" if local else "WORLD"
        con.mix_mode = "REPLACE"
        con.influence = 1.0
        n += 1

        # 位移：只有 ini 里标了 p 的骨才带位置（センター/グルーブ 的位移从这里走）
        if b.has_pos:
            lc = tgt.constraints.new("COPY_LOCATION")
            lc.name = CON_PREFIX + "LOC_" + name
            lc.target = arm
            lc.subtarget = src.name
            lc.target_space = "LOCAL" if local else "WORLD"
            lc.owner_space = "LOCAL" if local else "WORLD"
            lc.influence = 1.0
            n += 1
    return n


# --------------------------------------------------------------------------
# BoneAdjustment 表（官方 GetAdjustedRotation + autoBoneAxisAdjust）
# --------------------------------------------------------------------------

def build_adjustments(cfg):
    """给每根虚拟骨建一个 BoneAdjustment，完全按 vmdlib 的规则。"""
    adjs = {}
    explicit_axis = set()
    for name in cfg.parents_first():
        adj = boneadj.BoneAdjustment("-x,y,-z")   # MMDD 的 spec
        for mode, vals in (cfg.rot_adjust.get(name) or []):
            v = list(vals) + [0.0, 0.0, 0.0]
            if mode == "r":
                adj.set_rot(v[0], v[1], v[2])
            elif mode == "a":
                adj.set_axis(v[0], v[1], v[2])
                explicit_axis.add(name)
            elif mode == "s":
                adj.set_rotation_scale(v[0] if v[0] else 1.0)
        adjs[name] = adj

    # autoBoneAxisAdjust：
    #   autoAdj = Inverse(parent.rotAdjustment) * Euler(parent.axisAdjustmentVec)
    #   自己的 axisAdjustment = autoAdj.eulerAngles
    for name in cfg.parents_first():
        adj = adjs[name]
        if name in explicit_axis or adj.use_axis:
            continue
        parent = cfg.bones[name].parent
        if not parent or parent not in adjs:
            continue
        p = adjs[parent]
        auto = qmul(qconj(p.rot_q), boneadj.euler_zxy(*p.axis_vec))
        ex, ey, ez = boneadj.to_euler_zxy(auto)
        adj.set_axis(ex, ey, ez)
    return adjs


def _limit_axis(q, limit):
    """vmdlib 的 limitAxis：旋转拆成角度轴，轴与 refAx 反向则角度取反，
    再绕 lmtAx 重新施回（手臂扭转骨的旋转都被规范到一根轴上）。"""
    ref, lmt = limit
    rv = Vector(ref).normalized()
    lv = Vector(lmt).normalized()
    angle = q.angle
    if angle < 1e-9:
        return Quaternion()
    axis = q.axis
    if (axis + rv).length < 1.0:
        angle = -angle
    return Quaternion(lv, angle)


def _unity_euler_to_blender(x, y, z):
    """Unity 欧拉（度，顺序 ZXY = Ry·Rx·Rz）→ Blender 四元数。

    只用于 PrepareBoneBeforeCreateDummyBone（那些值本身就是 Unity 局部旋转），
    不用于 VMD 旋转（VMD 走 BoneAdjustment 的 spec）。
    """
    xr, yr, zr = math.radians(x), math.radians(y), math.radians(z)
    qw = lambda ax, a: Quaternion((math.cos(a / 2.0), ax[0] * math.sin(a / 2.0),
                                   ax[1] * math.sin(a / 2.0),
                                   ax[2] * math.sin(a / 2.0)))
    q_unity = qw((0, 1, 0), yr) @ qw((1, 0, 0), xr) @ qw((0, 0, 1), zr)
    from .ripper import mathutil as mu
    bx, by, bz, bw = mu.to_blender_quat((q_unity.x, q_unity.y,
                                         q_unity.z, q_unity.w))
    return Quaternion((bw, bx, by, bz))


def _prepare_heads(arm, cfg):
    """按 PrepareBoneBeforeCreateDummyBone 微调，算每根 HS2 骨的世界 head。

    官方在建虚拟骨定位**之前**，把腿/手臂/手指等 HS2 骨的 localRotation
    临时设成 ini 里的欧拉角（约 1~3°，消 HS2 与 MMD 骨架的轴向差），
    再按微调后的位置去定位虚拟骨。HS2 骨是纯 pivot（head 由父决定、
    localRotation 只影响子骨），所以传播规则：

        world_head[骨] = world_head[父] + world_rot[父] @ (微调 @ 相对父偏移)
        world_rot[骨]  = world_rot[父] @ 微调

    返回 {HS2骨名: 微调后世界 head}。
    """
    prepare_q = {}
    for name, deg in cfg.prepare.items():
        x, y, z = (list(deg) + [0, 0, 0])[:3]
        prepare_q[name] = _unity_euler_to_blender(x, y, z)

    heads = {}
    rots = {}
    for b in arm.data.bones:
        q = prepare_q.get(b.name, Quaternion())
        if b.parent is not None and b.parent.name in heads:
            relative = b.head_local - b.parent.head_local
            adjusted = q @ relative
            heads[b.name] = heads[b.parent.name] + (rots[b.parent.name] @ adjusted)
            rots[b.name] = rots[b.parent.name] @ q
        else:
            heads[b.name] = b.head_local.copy()
            rots[b.name] = q
    return heads


# --------------------------------------------------------------------------
# 姿态求值（Unity 空间）
# --------------------------------------------------------------------------

class _Pose(object):
    """虚拟骨树：local TRS → world TRS。全部用 Unity 坐标/四元数。"""

    def __init__(self, arm, cfg):
        self.cfg = cfg
        self.parent = {}
        self.children = {}
        self.offset = {}       # 相对父的静止位移（Unity 米）
        self.local_rot = {}
        self.local_pos = {}
        self._wcache = {}
        for name in cfg.parents_first():
            b = cfg.bones[name]
            bd = arm.data.bones.get(dummy_name(name))
            if bd is None:
                continue
            p = b.parent if b.parent in cfg.bones else ""
            self.parent[name] = p
            self.children.setdefault(p, []).append(name)
            head = b2u_pos(bd.head_local)
            if p:
                ph = b2u_pos(arm.data.bones[dummy_name(p)].head_local)
            else:
                ph = (0.0, 0.0, 0.0)
            self.offset[name] = (head[0] - ph[0], head[1] - ph[1],
                                 head[2] - ph[2])
            self.local_rot[name] = IDQ
            self.local_pos[name] = self.offset[name]

    def _invalidate(self, name):
        stack = [name]
        while stack:
            n = stack.pop()
            self._wcache.pop(n, None)
            stack.extend(self.children.get(n, ()))

    def set_local_rot(self, name, q):
        self.local_rot[name] = q
        self._invalidate(name)

    def set_local_pos(self, name, p):
        self.local_pos[name] = p
        self._invalidate(name)

    def _ensure(self, name):
        hit = self._wcache.get(name)
        if hit is not None:
            return hit
        p = self.parent.get(name, "")
        if p:
            pq, pp = self._ensure(p)
            q = qmul(pq, self.local_rot[name])
            r = qrot(pq, self.local_pos[name])
            pos = (pp[0] + r[0], pp[1] + r[1], pp[2] + r[2])
        else:
            q = self.local_rot[name]
            pos = self.local_pos[name]
        self._wcache[name] = (q, pos)
        return self._wcache[name]

    def world_rot(self, name):
        return self._ensure(name)[0]

    def world_pos(self, name):
        return self._ensure(name)[1]

    def world(self, name):
        return self._ensure(name)


# --------------------------------------------------------------------------
# IK（官方 MMDIKSolverLeg / MMDIKSolverNoLimit）
# --------------------------------------------------------------------------

def _ik_target_pos(pose, ik_name):
    """IK 控制骨的世界位置。"""
    return pose.world_pos(ik_name)


def _set_world_rot(pose, name, q):
    p = pose.parent.get(name, "")
    if p:
        local = qmul(qconj(pose.world_rot(p)), q)
    else:
        local = q
    pose.set_local_rot(name, qnorm(local))


class _LegIK(object):
    """MMDIKSolverLeg 的完整移植。"""

    def __init__(self, pose, ik_name, iksett, chain_names, min_knee):
        self.ik_name = ik_name
        self.iksett = iksett
        self.tgt = chain_names[0]         # 足首
        self.chain = list(chain_names[1:])  # [ひざ, 足]（末端→根）
        self.min_knee = float(min_knee)
        self.iterations = int(iksett.iterations)
        self.step = math.radians(float(iksett.step_angle))
        self.default_enable = bool(iksett.enable)
        self.valid = len(self.chain) == 2

        if not self.valid:
            return
        # Verify：先把膝设成 minKnee，再量静止几何
        knee, thigh = self.chain[0], self.chain[1]
        pose.set_local_rot(knee, boneadj.euler_zxy(self.min_knee, 0, 0))
        p_knee = pose.world_pos(knee)
        p_thigh = pose.world_pos(thigh)
        p_foot = pose.world_pos(self.tgt)
        thigh2knee = (p_knee[0] - p_thigh[0], p_knee[1] - p_thigh[1],
                      p_knee[2] - p_thigh[2])
        knee2foot = (p_foot[0] - p_knee[0], p_foot[1] - p_knee[1],
                     p_foot[2] - p_knee[2])
        self.l1 = math.sqrt(sum(c * c for c in thigh2knee))
        self.l2 = math.sqrt(sum(c * c for c in knee2foot))
        d_thigh_foot = (p_thigh[0] - p_foot[0], p_thigh[1] - p_foot[1],
                        p_thigh[2] - p_foot[2])
        self.leg_sum = math.sqrt(sum(c * c for c in d_thigh_foot))
        self.leg_mul2 = self.l1 * self.l2 * 2.0
        self.leg_sqr = self.l1 * self.l1 + self.l2 * self.l2
        axis, ang = from_to(thigh2knee, knee2foot)
        self.base_inv_q = qconj(q_from_axis_angle(axis, ang))
        self.min_knee_q = boneadj.euler_zxy(self.min_knee, 0, 0)
        pose.set_local_rot(knee, IDQ)

    def solve(self, pose):
        if not self.valid:
            return
        knee, thigh = self.chain[0], self.chain[1]
        tgt = _ik_target_pos(pose, self.ik_name)
        p_thigh = pose.world_pos(thigh)
        d = (tgt[0] - p_thigh[0], tgt[1] - p_thigh[1], tgt[2] - p_thigh[2])
        dlen = math.sqrt(sum(c * c for c in d))
        if dlen < self.leg_sum:
            cos_a = (self.leg_sqr - dlen * dlen) / max(self.leg_mul2, 1e-12)
            cos_a = max(-1.0, min(1.0, cos_a))
            knee_deg = 180.0 - math.degrees(math.acos(cos_a))
            pose.set_local_rot(knee, qmul(boneadj.euler_zxy(knee_deg, 0, 0),
                                          self.base_inv_q))
        else:
            pose.set_local_rot(knee, self.min_knee_q)

        for _ in range(self.iterations):
            end = _ik_target_pos(pose, self.tgt)
            bone = pose.world_pos(thigh)
            fv = (end[0] - bone[0], end[1] - bone[1], end[2] - bone[2])
            tv = (tgt[0] - bone[0], tgt[1] - bone[1], tgt[2] - bone[2])
            axis, ang = from_to(fv, tv)
            if ang <= 1e-9:
                break
            if ang > self.step:
                ang = self.step
            dq = q_from_axis_angle(axis, ang)
            result = qmul(dq, pose.world_rot(thigh))
            _set_world_rot(pose, thigh, result)


class _CCDIK(object):
    """MMDIKSolverNoLimit（无限制 CCD）的移植；用于 つま先IK。"""

    def __init__(self, pose, ik_name, chain_names, iterations, step_deg,
                 default_enable=True):
        self.ik_name = ik_name
        self.tgt = chain_names[0]
        self.chain = list(chain_names[1:])
        self.iterations = int(iterations)
        self.step = math.radians(float(step_deg))
        self.default_enable = default_enable

    def solve(self, pose):
        tgt = _ik_target_pos(pose, self.ik_name)
        for _ in range(self.iterations):
            all_skip = True
            for bone in self.chain:
                end = _ik_target_pos(pose, self.tgt)
                bp = pose.world_pos(bone)
                fv = (end[0] - bp[0], end[1] - bp[1], end[2] - bp[2])
                tv = (tgt[0] - bp[0], tgt[1] - bp[1], tgt[2] - bp[2])
                axis, ang = from_to(fv, tv)
                if ang <= 1e-9:
                    continue
                all_skip = False
                if ang > self.step:
                    ang = self.step
                dq = q_from_axis_angle(axis, ang)
                _set_world_rot(pose, bone, qmul(dq, pose.world_rot(bone)))
            if all_skip:
                break


def _make_ik(pose, cfg):
    """给每根 IK 骨建解算器（含 Verify 常量）。"""
    out = {}
    for s in cfg.ik_sets:
        if not s.enable:
            continue
        for ik_name in s.bones:
            b = cfg.bones.get(ik_name)
            if b is None or not b.ik:
                continue
            chain = [c for c in b.ik if c in cfg.bones and c in pose.offset]
            if len(chain) < 2:
                continue
            if s.solver == "MMDIKSolverLeg":
                out[ik_name] = _LegIK(pose, ik_name, s, chain, s.min_knee_rot)
            else:
                out[ik_name] = _CCDIK(pose, ik_name, chain, s.iterations,
                                      s.step_angle)
                out[ik_name].default_enable = bool(s.enable)
    return out


# --------------------------------------------------------------------------
# 写曲线
# --------------------------------------------------------------------------

def write_curves(arm, cfg, tracks, start=1, frames=None, action_name="MMDD",
                 unit=MMD_UNIT, arm_tilt=True, vmd_data=None):
    """tracks: {MMD骨名: {"rot": [(w,x,y,z)...], "pos": [...]}} → 写虚拟骨。

    逐帧复刻 vmdlib.updatePose：

        1. rot   ：localRot = GetAdjustedRotation(VMD旋转)
        2. pos   ：localPos = basePosition + sign * positionScale * VMD位置
        3. IK    ：MMDIKSolverLeg / NoLimit
        4. track ：local = SlerpUnclamped(I, src.local, scale) * own
        5. twist ：BoneTwistDisperse

    返回 (action, frames)。
    """
    from . import compat, vmd as vmdlib

    if frames is None:
        frames = vmd_data["frames"] if vmd_data else max(
            (len(v.get("rot") or v.get("pos") or []) for v in tracks.values()),
            default=1)
        frames = int(round(frames / 30.0 * bpy.context.scene.render.fps)) + 1
    n = max(1, frames)

    adjs = build_adjustments(cfg)
    pose = _Pose(arm, cfg)
    # 有些配置里的骨在当前 HS2 骨架上不存在（眼球/道具），对应虚拟骨没建出来，
    # 后续全部按「实际建出来的骨」过滤。
    order = [n for n in cfg.parents_first() if n in pose.offset]
    preset = {}
    for name in order:
        b = cfg.bones[name]
        if b.has_rot or (b.track and "r" in b.track[2]):
            preset[name] = adjs[name].apply(IDQ)
        else:
            preset[name] = IDQ

    for name, q in preset.items():
        pose.set_local_rot(name, q)

    # 位移基准（Unity 米）
    base = {name: pose.offset[name] for name in order}

    # IK 解算器
    ik_solvers = _make_ik(pose, cfg)

    # track 表
    tracks_of = {name: b.track for name, b in cfg.bones.items() if b.track}
    # twist 表
    twist_list = [(k, v[1].upper(), v[2].upper(), v[3])
                  for k, v in cfg.twist.items()]

    def sample_rot(name, f):
        b = cfg.bones[name]
        tr = tracks.get(b.vmd)
        if tr is None:
            return None
        raw = tr.get("raw_rot")
        if raw:
            q = raw[min(f, len(raw) - 1)]
        else:
            rot = tr.get("rot")
            if not rot:
                return None
            qb = rot[min(f, len(rot) - 1)]
            q = (qb[1], qb[2], qb[3], qb[0])
            q = (-q[0], q[1], -q[2], q[3])
        q = adjs[name].apply(qnorm(q))
        return q

    def sample_pos(name, f):
        b = cfg.bones[name]
        tr = tracks.get(b.vmd)
        if tr is None:
            return None
        pos = tr.get("pos")
        if not pos:
            return None
        i = min(f, len(pos) - 1)
        # tracks 里的 pos 还是 MMD 原始轴（all_tracks 原样保留）
        return pos[i]

    local_log = {}
    local_plog = {}
    for name in order:
        local_log[name] = []
        local_plog[name] = []

    vmd_bone = {name: cfg.bones[name].vmd for name in order}

    for f in range(n):
        for name in order:
            b = cfg.bones[name]

            # ---- 1. 旋转 ----
            q = sample_rot(name, f)
            if q is None:
                q = preset[name]
            if b.limit:
                qb = _limit_axis(Quaternion((q[3], q[0], q[1], q[2])),
                                 b.limit)
                q = (qb.x, qb.y, qb.z, qb.w)
            pose.set_local_rot(name, q)

            # ---- 2. 位置 ----
            if b.has_pos:
                p = sample_pos(name, f)
                if p is None:
                    pose.set_local_pos(name, base[name])
                else:
                    s = unit
                    d = (-p[0] * s, p[1] * s, -p[2] * s)
                    off = base[name]
                    pose.set_local_pos(name, (off[0] + d[0], off[1] + d[1],
                                              off[2] + d[2]))
            else:
                pose.set_local_pos(name, base[name])

            # ---- 3. IK ----
            if name in ik_solvers:
                solver = ik_solvers[name]
                enabled = solver.default_enable
                if vmd_data is not None:
                    enabled = vmdlib.ik_enabled(
                        vmd_data, b.vmd,
                        f * 30.0 / bpy.context.scene.render.fps,
                        default=solver.default_enable)
                if enabled:
                    solver.solve(pose)
                    # IK 会改链上骨骼的 localRot；它们的 frame log 已经在
                    # 主循环里 append 过，必须回填，否则写进 fcurve 的仍是
                    # FK 的旧值（旧实现 D 骨/腿「打帧不生效」的根因）。
                    for cn in ([solver.tgt] + solver.chain):
                        if cn in local_log and local_log[cn]:
                            local_log[cn][-1] = pose.local_rot[cn]

            # ---- 4. track ----
            if name in tracks_of:
                scale, src, modes = tracks_of[name]
                if "r" in modes:
                    src_q = pose.local_rot.get(src, IDQ)
                    own = pose.local_rot[name]
                    q2 = qmul(slerp_unclamped(IDQ, src_q, scale), own)
                    pose.set_local_rot(name, qnorm(q2))
                if "p" in modes:
                    src_p = pose.local_pos.get(src, (0.0, 0.0, 0.0))
                    own = pose.local_pos[name]
                    pose.set_local_pos(name, (own[0] + src_p[0] * scale,
                                              own[1] + src_p[1] * scale,
                                              own[2] + src_p[2] * scale))

            local_log[name].append(pose.local_rot[name])
            local_plog[name].append(pose.local_pos[name])

        # ---- 5. twist ----
        for _k, tw, dis, rate in twist_list:
            if tw not in pose.local_rot or dis not in pose.local_rot:
                continue
            org = pose.local_rot[tw]
            if qangle(org) < 1e-7:
                continue
            remain = slerp(IDQ, org, 1.0 - rate)
            delta = qmul(org, qconj(remain))
            pose.set_local_rot(tw, remain)
            pose.set_local_rot(dis, qmul(pose.local_rot[dis], delta))
            local_log[tw][-1] = pose.local_rot[tw]
            local_log[dis][-1] = pose.local_rot[dis]

    # ---- 写 fcurve ----
    act = compat.new_action(arm, action_name)
    curves = compat.bag(act, arm)
    written = 0

    for name in order:
        dname = dummy_name(name)
        if dname not in arm.pose.bones:
            continue
        qs = local_log[name]
        base_path = 'pose.bones["%s"]' % dname
        pb = arm.pose.bones[dname]
        # 旋转：只有变化过（或 preset 非单位）才写。注意**不能**在这里
        # continue —— 位置-only 的骨（如 グルーブ 只有位移）还要写 location。
        write_rot = not all(
            abs(q[0]) + abs(q[1]) + abs(q[2]) < 1e-9 and
            abs(abs(q[3]) - 1.0) < 1e-9 for q in qs)
        if not write_rot and qangle(preset[name]) > 1e-9:
            write_rot = True
        if write_rot:
            pb.rotation_mode = "QUATERNION"
            for i in range(4):
                fc = compat.new_fcurve(
                    curves, base_path + ".rotation_quaternion", i, name)
                compat.fill(fc, [u2b_quat(q)[i] for q in qs], start)
            written += 1

        if not cfg.bones[name].has_pos:
            continue
        ps = local_plog[name]
        if all(abs(p[i] - base[name][i]) < 1e-9 for p in ps for i in range(3)):
            continue
        # Blender 的 pose.location 是**相对 rest** 的偏移；虚拟骨的 rest head
        # 已经包含 basePosition，所以这里只能写 delta（旧实现写 base+delta，
        # センター/足 IK 全部被抬高了一倍，脚离地 0.58m）。
        bpos = base[name]
        for i in range(3):
            fc = compat.new_fcurve(curves, base_path + ".location", i, name)
            compat.fill(fc, [u2b_pos((p[0] - bpos[0], p[1] - bpos[1],
                                      p[2] - bpos[2]))[i] for p in ps], start)

    compat.ensure_slot(act, arm)
    return act, n
