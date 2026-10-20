"""MMDD v2.2.1 的独立参考实现（只用于数值对拍，不进插件运行时）。

这份代码是照着 ``_ref/mmdd_v221/vmdlib.py`` 的
``MotionController.updatePose / setFrame(indexMode) / MMDIKSolverLeg``
重写的，刻意**不复用** ``hs2_rig_kit.mmdrig`` 的数学，用来当 oracle：

    python（Blender 自带）tools/mmdd_parity.py

参考实现只依赖：
  * 插件解析出来的 VMD 原始关键帧（含贝塞尔控制点）
  * chara ini（mmdcfg 解析）
  * Blender 里已经建好的虚拟骨 rest 位置
"""

import math

from hs2_rig_kit import mmdcfg

ID = (0.0, 0.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# 四元数（Unity 系，(x,y,z,w)）
# ---------------------------------------------------------------------------

def qm(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def qc(q):
    return (-q[0], -q[1], -q[2], q[3])


def qn(q):
    n = math.sqrt(sum(c * c for c in q))
    return ID if n < 1e-12 else tuple(c / n for c in q)


def axang(axis, deg):
    n = math.sqrt(sum(c * c for c in axis))
    if n < 1e-12 or abs(deg) < 1e-12:
        return ID
    h = math.radians(deg) * 0.5
    s = math.sin(h) / n
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def qrot(q, v):
    p = qm(qm(q, (v[0], v[1], v[2], 0.0)), qc(q))
    return (p[0], p[1], p[2])


def euler_zxy(x, y, z):
    return qm(qm(axang((0, 1, 0), y), axang((1, 0, 0), x)),
              axang((0, 0, 1), z))


def to_euler(q):
    """四元数 → Unity ZXY 欧拉角（度）。"""
    x, y, z, w = qn(q)
    m00 = 1 - 2 * (y * y + z * z)
    m01 = 2 * (x * y - w * z)
    m02 = 2 * (x * z + w * y)
    m10 = 2 * (x * y + w * z)
    m11 = 1 - 2 * (x * x + z * z)
    m12 = 2 * (y * z - w * x)
    m20 = 2 * (x * z - w * y)
    m21 = 2 * (y * z + w * x)
    m22 = 1 - 2 * (x * x + y * y)
    sa = max(-1.0, min(1.0, -m12))
    ax = math.asin(sa)
    ca = math.cos(ax)
    if abs(ca) > 1e-6:
        ay = math.atan2(m02, m22)
        az = math.atan2(m10, m11)
    else:
        az = 0.0
        ay = math.atan2(m01, m00) if sa > 0 else math.atan2(-m01, m00)
    out = []
    for a in (ax, ay, az):
        d = math.degrees(a) % 360.0
        out.append(0.0 if abs(d - 360.0) < 1e-9 else d)
    return tuple(out)


def slerp(a, b, t):
    d = sum(x * y for x, y in zip(a, b))
    if d < 0:
        b = tuple(-x for x in b)
        d = -d
    if d > 0.9995:
        return qn(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))
    th = math.acos(max(-1.0, min(1.0, d)))
    s = math.sin(th)
    return qn(tuple(a[i] * math.sin((1 - t) * th) / s +
                    b[i] * math.sin(t * th) / s for i in range(4)))


def slerp_unclamped(a, b, t):
    if abs(t) < 1e-9:
        return a
    if abs(t - 1.0) < 1e-9:
        return b
    d = sum(x * y for x, y in zip(a, b))
    if d < 0:
        b = tuple(-x for x in b)
        d = -d
    th = math.acos(max(-1.0, min(1.0, d)))
    if th < 1e-7:
        return qn(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))
    s = math.sin(th)
    return qn(tuple(a[i] * math.sin((1 - t) * th) / s +
                    b[i] * math.sin(t * th) / s for i in range(4)))


# ---------------------------------------------------------------------------
# 独立 VMD 采样（MMDD mmdCubicBezierInterpolate_2）
# ---------------------------------------------------------------------------

def _bezier(h1, h2, t):
    u = 1.0 - t
    x = 3 * u * u * t * h1[0] + 3 * u * t * t * h2[0] + t ** 3
    y = 3 * u * u * t * h1[1] + 3 * u * t * t * h2[1] + t ** 3
    return x, y


def bezier_y(curve, x):
    h1 = (curve[0], curve[1])
    h2 = (curve[2], curve[3])
    lo, hi = 0.0, 1.0
    t = x
    vy = 0.0
    for _ in range(10):
        vx, vy = _bezier(h1, h2, t)
        if abs(vx - x) < 0.001:
            break
        if vx > x:
            hi = t
        else:
            lo = t
        t = (lo + hi) * 0.5
    return vy


def sample(keys, frame):
    if frame <= keys[0][0]:
        return keys[0][1], keys[0][2]
    if frame >= keys[-1][0]:
        return keys[-1][1], keys[-1][2]
    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid][0] <= frame:
            lo = mid
        else:
            hi = mid
    f0, p0, q0, _ = keys[lo]
    f1, p1, q1, c1 = keys[hi]
    if frame == f0:
        return p0, q0
    t = (frame - f0) / float(f1 - f0)
    c = c1
    tx = t if c[0][0] == c[0][1] and c[0][2] == c[0][3] else bezier_y(c[0], t)
    ty = t if c[1][0] == c[1][1] and c[1][2] == c[1][3] else bezier_y(c[1], t)
    tz = t if c[2][0] == c[2][1] and c[2][2] == c[2][3] else bezier_y(c[2], t)
    tr = t if c[3][0] == c[3][1] and c[3][2] == c[3][3] else bezier_y(c[3], t)
    pos = (p0[0] + (p1[0] - p0[0]) * tx,
           p0[1] + (p1[1] - p0[1]) * ty,
           p0[2] + (p1[2] - p0[2]) * tz)
    return pos, slerp(q0, q1, tr)


# ---------------------------------------------------------------------------
# 独立 GetAdjustedRotation / autoBoneAxisAdjust
# ---------------------------------------------------------------------------

class Adjust(object):
    def __init__(self):
        self.rot = ID
        self.axis = ID
        self.axis_vec = (0.0, 0.0, 0.0)
        self.use_axis = False
        self.rot_scale = 1.0

    def apply(self, q):
        e = to_euler(q)
        # spec "-x,y,-z"
        e = (-e[0], e[1], -e[2])
        if self.use_axis:
            ax = qrot(self.axis, (1, 0, 0))
            ay = qrot(self.axis, (0, 1, 0))
            az = qrot(self.axis, (0, 0, 1))
            out = qm(qm(qm(axang(ay, e[1]), axang(ax, e[0])),
                        axang(az, e[2])), self.rot)
        else:
            out = qm(euler_zxy(*e), self.rot)
        if abs(self.rot_scale - 1.0) > 1e-12:
            out = slerp(ID, out, self.rot_scale)
        return qn(out)


def make_adjustments(cfg):
    adjs = {}
    explicit = set()
    for name in cfg.parents_first():
        a = Adjust()
        for mode, vals in (cfg.rot_adjust.get(name) or []):
            v = list(vals) + [0, 0, 0]
            if mode == "r":
                a.rot = euler_zxy(v[0], v[1], v[2])
            elif mode == "a":
                a.axis = euler_zxy(v[0], v[1], v[2])
                a.axis_vec = (v[0], v[1], v[2])
                a.use_axis = True
                explicit.add(name)
            elif mode == "s":
                a.rot_scale = v[0] if v[0] else 1.0
        adjs[name] = a
    for name in cfg.parents_first():
        a = adjs[name]
        if name in explicit or a.use_axis:
            continue
        p = cfg.bones[name].parent
        if not p or p not in adjs:
            continue
        pa = adjs[p]
        auto = qm(qc(pa.rot), euler_zxy(*pa.axis_vec))
        a.axis = auto
        a.axis_vec = to_euler(auto)
        a.use_axis = True
    return adjs


# ---------------------------------------------------------------------------
# 参考姿态 + IK
# ---------------------------------------------------------------------------

class RefPose(object):
    def __init__(self, offsets, parents):
        self.parents = parents
        self.offset = offsets
        self.lr = {n: ID for n in offsets}
        self.lp = dict(offsets)
        self.cache = {}

    def inv(self, n):
        self.cache.pop(n, None)
        for c in list(self.cache):
            pass

    def setr(self, n, q):
        self.lr[n] = q
        self._dirty(n)

    def setp(self, n, p):
        self.lp[n] = p
        self._dirty(n)

    def _dirty(self, n):
        # 简单粗暴：全清（参考实现，帧数不多）
        self.cache.clear()

    def _get(self, n):
        if n in self.cache:
            return self.cache[n]
        p = self.parents.get(n, "")
        if p:
            pq, pp = self._get(p)
            q = qm(pq, self.lr[n])
            r = qrot(pq, self.lp[n])
            pos = (pp[0] + r[0], pp[1] + r[1], pp[2] + r[2])
        else:
            q = self.lr[n]
            pos = self.lp[n]
        self.cache[n] = (q, pos)
        return q, pos

    def wr(self, n):
        return self._get(n)[0]

    def wp(self, n):
        return self._get(n)[1]


def set_wr(pose, n, q):
    p = pose.parents.get(n, "")
    pose.setr(n, qn(qm(qc(pose.wr(p)), q) if p else q))


def from_to(a, b):
    na = math.sqrt(sum(c * c for c in a))
    nb = math.sqrt(sum(c * c for c in b))
    if na < 1e-12 or nb < 1e-12:
        return (0, 1, 0), 0.0
    a = tuple(c / na for c in a)
    b = tuple(c / nb for c in b)
    d = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    ang = math.acos(d)
    if ang < 1e-9:
        return (0, 1, 0), 0.0
    ax = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
          a[0] * b[1] - a[1] * b[0])
    n = math.sqrt(sum(c * c for c in ax))
    return ((ax[0] / n, ax[1] / n, ax[2] / n) if n > 1e-9
            else (0, 1, 0)), ang


def axis_angle(q):
    q = qn(q)
    if q[3] < 0:
        q = (-q[0], -q[1], -q[2], -q[3])
    ang = 2 * math.acos(max(-1.0, min(1.0, q[3])))
    s = math.sqrt(max(0.0, 1 - q[3] * q[3]))
    return ((q[0] / s, q[1] / s, q[2] / s) if s > 1e-9 else (0, 1, 0)), ang


class LegRef(object):
    def __init__(self, pose, ik, sett, chain, unit):
        self.ik = ik
        self.sett = sett
        self.tgt = chain[0]
        self.chain = chain[1:]
        self.ok = len(self.chain) == 2
        if not self.ok:
            return
        knee, thigh = self.chain
        pose.setr(knee, euler_zxy(sett.min_knee_rot, 0, 0))
        pk, pt, pf = pose.wp(knee), pose.wp(thigh), pose.wp(self.tgt)
        v1 = (pk[0] - pt[0], pk[1] - pt[1], pk[2] - pt[2])
        v2 = (pf[0] - pk[0], pf[1] - pk[1], pf[2] - pk[2])
        self.l1 = math.sqrt(sum(c * c for c in v1))
        self.l2 = math.sqrt(sum(c * c for c in v2))
        self.default_enable = bool(sett.enable)
        self.sum = math.sqrt(sum((pt[i] - pf[i]) ** 2 for i in range(3)))
        self.mul2 = self.l1 * self.l2 * 2
        self.sqr = self.l1 ** 2 + self.l2 ** 2
        ax, ang = from_to(v1, v2)
        self.base_inv = qc(axang(ax, math.degrees(ang)))
        self.minq = euler_zxy(sett.min_knee_rot, 0, 0)
        pose.setr(knee, ID)

    def solve(self, pose):
        if not self.ok:
            return
        knee, thigh = self.chain
        tgt = pose.wp(self.ik)
        pt = pose.wp(thigh)
        d = (tgt[0] - pt[0], tgt[1] - pt[1], tgt[2] - pt[2])
        dl = math.sqrt(sum(c * c for c in d))
        if dl < self.sum:
            ca = max(-1.0, min(1.0, (self.sqr - dl * dl) / max(self.mul2, 1e-12)))
            deg = 180.0 - math.degrees(math.acos(ca))
            pose.setr(knee, qm(euler_zxy(deg, 0, 0), self.base_inv))
        else:
            pose.setr(knee, self.minq)
        step = math.radians(self.sett.step_angle)
        for _ in range(self.sett.iterations):
            end = pose.wp(self.tgt)
            b = pose.wp(thigh)
            ax, ang = from_to((end[0] - b[0], end[1] - b[1], end[2] - b[2]),
                              (tgt[0] - b[0], tgt[1] - b[1], tgt[2] - b[2]))
            if ang <= 1e-9:
                break
            if ang > step:
                ang = step
            dq = axang(ax, math.degrees(ang))
            set_wr(pose, thigh, qm(dq, pose.wr(thigh)))


class CCDRef(object):
    def __init__(self, pose, ik, chain, iters, step):
        self.ik = ik
        self.tgt = chain[0]
        self.chain = chain[1:]
        self.iters = iters
        self.step = math.radians(step)
        self.default_enable = True

    def solve(self, pose):
        tgt = pose.wp(self.ik)
        for _ in range(self.iters):
            skip = True
            for b in self.chain:
                e = pose.wp(self.tgt)
                p = pose.wp(b)
                ax, ang = from_to((e[0] - p[0], e[1] - p[1], e[2] - p[2]),
                                  (tgt[0] - p[0], tgt[1] - p[1], tgt[2] - p[2]))
                if ang <= 1e-9:
                    continue
                skip = False
                if ang > self.step:
                    ang = self.step
                set_wr(pose, b, qm(axang(ax, math.degrees(ang)), pose.wr(b)))
            if skip:
                break


def simulate(cfg, vmd_data, arm, unit, fps=30.0, frames=None):
    """返回 {frame: ({name: localRot}, {name: localPos})}。"""
    import bpy  # noqa
    from hs2_rig_kit.mmdrig import dummy_name

    order = [n for n in cfg.parents_first()
             if dummy_name(n) in arm.data.bones]
    parents = {}
    offsets = {}
    for n in order:
        b = cfg.bones[n]
        p = b.parent if b.parent in order else ""
        parents[n] = p
        bd = arm.data.bones[dummy_name(n)]
        head = (bd.head_local.x, bd.head_local.z, -bd.head_local.y)  # b→u
        if p:
            ph = arm.data.bones[dummy_name(p)].head_local
            ph = (ph.x, ph.z, -ph.y)
        else:
            ph = (0.0, 0.0, 0.0)
        offsets[n] = (head[0] - ph[0], head[1] - ph[1], head[2] - ph[2])

    adjs = make_adjustments(cfg)
    preset = {}
    for n in order:
        b = cfg.bones[n]
        preset[n] = adjs[n].apply(ID) if b.has_rot else ID

    pose = RefPose(offsets, parents)
    for n in order:
        pose.setr(n, preset[n])

    # IK
    iks = {}
    for s in cfg.ik_sets:
        if not s.enable:
            continue
        for ik in s.bones:
            b = cfg.bones.get(ik)
            if not b or not b.ik:
                continue
            chain = [c for c in b.ik if c in order]
            if len(chain) < 2:
                continue
            if s.solver == "MMDIKSolverLeg":
                iks[ik] = LegRef(pose, ik, s, chain, unit)
            else:
                iks[ik] = CCDRef(pose, ik, chain, s.iterations, s.step_angle)

    if frames is None:
        frames = int(round(vmd_data["frames"] / 30.0 * fps)) + 1
    out = {}
    for f in range(frames):
        src = f * 30.0 / fps
        for n in order:
            b = cfg.bones[n]
            tr = vmd_data["bones"].get(b.vmd)
            q = preset[n]
            if tr:
                _p, rawq = sample(tr, src)
                q = adjs[n].apply(rawq)
                if b.limit:
                    ax, ang = axis_angle(q)
                    ref = b.limit[0]
                    if math.sqrt(sum((ax[i] + ref[i]) ** 2
                                     for i in range(3))) < 1.0:
                        ang = -ang
                    q = axang(b.limit[1], math.degrees(ang))
            pose.setr(n, q)

            lp = offsets[n]
            if b.has_pos and tr:
                p, _q = sample(tr, src)
                # GetAdjustedPosition: sign * positionScale
                lp = (offsets[n][0] - p[0] * unit,
                      offsets[n][1] + p[1] * unit,
                      offsets[n][2] - p[2] * unit)
            pose.setp(n, lp)

            if n in iks:
                ik = iks[n]
                ikey = getattr(ik, "default_enable", True)
                if _ik_enabled(vmd_data, b.vmd, src, ikey):
                    ik.solve(pose)

            if b.track:
                sc, srcn, modes = b.track
                if "r" in modes:
                    pose.setr(n, qn(qm(slerp_unclamped(ID, pose.lr[srcn], sc),
                                       pose.lr[n])))
                if "p" in modes:
                    sp = pose.lp[srcn]
                    op = pose.lp[n]
                    pose.setp(n, (op[0] + sp[0] * sc, op[1] + sp[1] * sc,
                                  op[2] + sp[2] * sc))

        for _k, tv in cfg.twist.items():
            tw, dis, rate = tv[1].upper(), tv[2].upper(), tv[3]
            if tw not in pose.lr or dis not in pose.lr:
                continue
            org = pose.lr[tw]
            if axis_angle(org)[1] < 1e-7:
                continue
            remain = slerp(ID, org, 1.0 - rate)
            delta = qm(org, qc(remain))
            pose.setr(tw, remain)
            pose.setr(dis, qm(pose.lr[dis], delta))
            pose.cache.clear()

        pose.cache.clear()
        wr = {}
        wp = {}
        for n in order:
            wr[n] = pose.wr(n)
            wp[n] = pose.wp(n)
        out[f] = (dict(pose.lr), dict(pose.lp), wr, wp)
    return out, frames, offsets, parents


def _ik_enabled(vmd_data, vmd_bone, frame, default=True):
    keys = vmd_data.get("ik") or []
    if not keys:
        return default
    if frame < keys[0][0]:
        return True
    val = True
    for fr, _vis, entries in keys:
        if fr > frame:
            break
        if vmd_bone in entries:
            val = entries[vmd_bone]
    return val


def _dot_axis(axis, ref):
    # axis 与 ref 反向 → (axis + ref).magnitude < 1（MMDD 的判据）
    return axis[0] * ref[0] + axis[1] * ref[1] + axis[2] * ref[2]
