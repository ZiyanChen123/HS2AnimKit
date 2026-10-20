"""VMD 旋转的「骨骼调整」—— 忠实移植 MMD4URuntime 的 ``BoneAdjustment``。

为什么需要它
------------
VMD 里的旋转是 MMD（Unity 系）**骨骼本地**的欧拉角组合。HS2 骨架的静止
姿态和 MMD 不一样（手臂更贴身体、肩更高），直接把这些四元数套到 HS2 骨上，
胳膊会**整体拧着**：肩窝凹、肘部朝错误方向翻、手背朝外。

官方 VMDPlayPlugin 的解法（``MMD4URuntime.BoneAdjustment``）不是简单乘一个
固定角度，而是**把旋转轴本身掰过去**：

    q = AngleAxis(e.y, axisY) · AngleAxis(e.x, axisX) · AngleAxis(e.z, axisZ)
        · rotAdjustment

其中 ``axisX/Y/Z`` 是「被 axisAdjustment 旋转过的坐标轴」。对胳膊来说，
肩、大臂、小臂、手分别用**不同倾角的轴**：

    shoulder : 轴不变，固定旋转 ±Shoulder(8°)
    armUp    : 轴倾 ∓Shoulder(8°)      ，固定旋转 ±ArmUp(30°)
    armLow   : 轴倾 ∓(Shoulder+ArmUp)  ，固定旋转 ±ArmLow(0°)
    hand     : 轴倾 ∓(Shoulder+ArmUp)

这正是 ini 里被注释掉的 ``LOWARM_R=a,0,0,42`` 那行的作用 —— 42 ≈ 10+32，
即「肩 10° + 大臂 32°」的轴倾角。

坐标约定
--------
本模块**全部在 Unity/MMD 空间**计算（左手系、Y 上、面朝 +Z），
四元数用 ``(x, y, z, w)``。换算到 Blender 由调用方用
``vmd.mmd_quat_to_blender`` 做，避免半路混空间。

Unity 的 ``Quaternion.Euler(x, y, z)`` 是 **ZXY** 顺序：
``q = Ry(y) · Rx(x) · Rz(z)``。
"""

import math

EPS = 1e-9

# Unity 里 2*180/pi：RotationOnAxis 用的系数
_RAD2DEG = 180.0 / math.pi


# --------------------------------------------------------------------------
# 四元数基础（Unity 系，(x,y,z,w)，纯数学，不涉及坐标系）
# --------------------------------------------------------------------------

def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q):
    n = math.sqrt(sum(c * c for c in q))
    if n < EPS:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(c / n for c in q)


def angle_axis(axis, deg):
    """绕单位轴转 deg 度（右手定则）。"""
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z)
    if n < EPS or abs(deg) < EPS:
        return (0.0, 0.0, 0.0, 1.0)
    h = math.radians(deg) * 0.5
    s = math.sin(h) / n
    return (x * s, y * s, z * s, math.cos(h))


def qrot(q, v):
    """用四元数旋转向量。"""
    p = qmul(qmul(q, (v[0], v[1], v[2], 0.0)), qconj(q))
    return (p[0], p[1], p[2])


def euler_zxy(x, y, z):
    """Unity ``Quaternion.Euler(x, y, z)``：q = Ry(y)·Rx(x)·Rz(z)。"""
    return qmul(qmul(angle_axis((0.0, 1.0, 0.0), y),
                     angle_axis((1.0, 0.0, 0.0), x)),
                angle_axis((0.0, 0.0, 1.0), z))


def to_euler_zxy(q):
    """四元数 → Unity 式 ZXY 欧拉角（度，[0,360)）。

    即求 (x,y,z) 使 ``Ry(y)·Rx(x)·Rz(z) == q``。
    """
    x, y, z, w = qnorm(q)
    # 旋转矩阵（行主序）
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
        # 万向锁：x = ±90°，z 归零
        az = 0.0
        ay = math.atan2(m01, m00) if sa > 0 else math.atan2(-m01, m00)

    def norm_deg(a):
        d = math.degrees(a) % 360.0
        return 0.0 if abs(d - 360.0) < 1e-9 else d

    return (norm_deg(ax), norm_deg(ay), norm_deg(az))


def slerp(a, b, t):
    d = sum(p * q for p, q in zip(a, b))
    if d < 0.0:
        b = tuple(-c for c in b)
        d = -d
    if d > 0.9995:
        return qnorm(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))
    th = math.acos(max(-1.0, min(1.0, d)))
    s = math.sin(th)
    return tuple((a[i] * math.sin((1 - t) * th) + b[i] * math.sin(t * th)) / s
                 for i in range(4))


# --------------------------------------------------------------------------
# BoneAdjustment —— 与官方一一对应
# --------------------------------------------------------------------------

class BoneAdjustment:
    """对应 ``MMD4URuntime.BoneAdjustment``。

    用法::

        adj = BoneAdjustment("x,y,z")
        adj.set_rot(0, 0, -32)         # ini 的 r 模式 / 插件倾角
        adj.set_axis(0, 0, 42)         # ini 的 a 模式 / 派生轴倾
        q = adj.apply(vmd_quat)        # 输入输出都是 Unity 系 (x,y,z,w)
    """

    __slots__ = ("spec", "_xyz", "_sign", "rot_vec", "rot_q",
                 "axis_vec", "axis_x", "axis_y", "axis_z", "use_axis",
                 "axis_limit", "limit_on", "rotation_scale")

    def __init__(self, spec="x,y,z"):
        self.spec = spec
        self._xyz, self._sign = self._parse_spec(spec)
        self.rot_vec = (0.0, 0.0, 0.0)
        self.rot_q = (0.0, 0.0, 0.0, 1.0)
        self.axis_vec = (0.0, 0.0, 0.0)
        self.axis_x = (1.0, 0.0, 0.0)
        self.axis_y = (0.0, 1.0, 0.0)
        self.axis_z = (0.0, 0.0, 1.0)
        self.use_axis = False
        self.axis_limit = None
        self.limit_on = False
        self.rotation_scale = 1.0

    # ---- 配置 ----

    @staticmethod
    def _parse_spec(spec):
        parts = [p.strip() for p in (spec or "x,y,z").split(",")]
        while len(parts) < 3:
            parts.append("xyz"[len(parts)])
        xyz, sign = [], []
        for p in parts[:3]:
            s = 1.0
            if p.startswith("-"):
                p = p[1:].strip()
                s = -1.0
            elif p == "0":
                s = 0.0
            xyz.append(p)
            sign.append(s)
        return xyz, sign

    def set_rot(self, x, y, z):
        self.rot_vec = (float(x), float(y), float(z))
        self.rot_q = euler_zxy(*self.rot_vec)

    def set_axis(self, x, y, z):
        self.axis_vec = (float(x), float(y), float(z))
        q = euler_zxy(*self.axis_vec)
        self.axis_x = qrot(q, (1.0, 0.0, 0.0))
        self.axis_y = qrot(q, (0.0, 1.0, 0.0))
        self.axis_z = qrot(q, (0.0, 0.0, 1.0))
        self.use_axis = self.axis_vec != (0.0, 0.0, 0.0)

    def set_axis_limit(self, x, y, z):
        self.axis_limit = (float(x), float(y), float(z))
        self.limit_on = self.axis_limit != (1.0, 1.0, 1.0)

    def set_rotation_scale(self, s):
        self.rotation_scale = float(s)

    def is_identity(self):
        return (not self.use_axis
                and self.rot_vec == (0.0, 0.0, 0.0)
                and not self.limit_on
                and abs(self.rotation_scale - 1.0) < EPS)

    # ---- 核心 ----

    def _remap(self, e):
        """按 spec 重排/取反欧拉分量（如 "-x,y,-z"）。"""
        if self._xyz == ["x", "y", "z"] and self._sign == [1.0, 1.0, 1.0]:
            return e
        src = {"x": e[0], "y": e[1], "z": e[2], "0": 0.0}
        return tuple(src[self._xyz[i]] * self._sign[i] for i in range(3))

    def apply(self, q):
        """``GetAdjustedRotation``：VMD 原始旋转 → 调整后的旋转。"""
        e = self._remap(to_euler_zxy(q))
        if self.use_axis:
            val = qmul(qmul(qmul(angle_axis(self.axis_y, e[1]),
                                 angle_axis(self.axis_x, e[0])),
                           angle_axis(self.axis_z, e[2])),
                       self.rot_q)
        else:
            val = qmul(euler_zxy(*e), self.rot_q)

        if self.limit_on:
            val = euler_zxy(self._limit_components(val))

        if abs(self.rotation_scale - 1.0) > EPS:
            val = slerp((0.0, 0.0, 0.0, 1.0), val, self.rotation_scale)
        return qnorm(val)

    def _limit_components(self, q):
        """``SetAxisLimit`` + ``RotationOnAxis``：把每轴角度按 limit 缩放。"""
        lim = self.axis_limit
        out = []
        for i in range(3):
            out.append(self._rotation_on_axis(i, q) * lim[i])
        return tuple(out)

    @staticmethod
    def _rotation_on_axis(i, q):
        """单轴上的等效角度（度）。对应官方 ``114.59156*atan(comp)``。

        w 接近 0（180° 旋转）时官方会除零，这里退化为用轴分量本身。
        """
        x, y, z, w = q
        comp = (x, y, z)[i]
        if abs(w) < 1e-6:
            n = math.sqrt(x * x + y * y + z * z)
            if n < EPS:
                return 0.0
            # 180°：角度就是该轴分量方向的 ±180
            return 180.0 if comp >= 0 else -180.0
        return 2.0 * _RAD2DEG * math.atan(comp / w)


# --------------------------------------------------------------------------
# 按官方规则造表
# --------------------------------------------------------------------------

# 官方插件默认倾角（Settings: ShoulderTiltDefault / UpperArmTiltDefault /
# LowerArmTiltDefault）。ini 的 r 模式给了等价数值（肩 10 / 大臂 32），
# 优先用 ini；ini 没给才用这里的默认。
TILT_SHOULDER = 8.0
TILT_ARM_UP = 30.0
TILT_ARM_LOW = 0.0

# 手臂链（左右同构）。顺序：肩 → 大臂 → 小臂 → 手。
ARM_CHAIN = ("cf_J_Shoulder_{S}", "cf_J_ArmUp00_{S}",
             "cf_J_ArmLow01_{S}", "cf_J_Hand_{S}")


def build_arm_chain(shoulder, arm_up, arm_low, hand_extra=None):
    """按官方 Set() 生成手臂四条骨的调整（{骨名: BoneAdjustment}）。

    官方原文（QuickAdjust.Set）：

        shoulder_L : rot(0,0,+sh)      shoulder_R : rot(0,0,-sh)
        armUp_L    : rot(0,0,+au), axis(0,0,-sh)
        armUp_R    : rot(0,0,-au), axis(0,0,+sh)
        armLow_L   : rot(0,0,+al), axis(0,0,-(sh+au))
        armLow_R   : rot(0,0,-al), axis(0,0,+(sh+au))
        hand_L     :               axis(0,0,-(sh+au))
        hand_R     :               axis(0,0,+(sh+au))
    """
    out = {}
    for side, sgn in (("L", 1.0), ("R", -1.0)):
        sh = ARM_CHAIN[0].format(S=side)
        au = ARM_CHAIN[1].format(S=side)
        al = ARM_CHAIN[2].format(S=side)
        hd = ARM_CHAIN[3].format(S=side)

        a = BoneAdjustment("x,y,z")
        a.set_rot(0.0, 0.0, sgn * shoulder)
        out[sh] = a

        a = BoneAdjustment("x,y,z")
        a.set_rot(0.0, 0.0, sgn * arm_up)
        a.set_axis(0.0, 0.0, -sgn * shoulder)
        out[au] = a

        a = BoneAdjustment("x,y,z")
        a.set_rot(0.0, 0.0, sgn * arm_low)
        a.set_axis(0.0, 0.0, -sgn * (shoulder + arm_up))
        out[al] = a

        a = BoneAdjustment("x,y,z")
        if hand_extra is not None:
            a.set_rot(0.0, 0.0, sgn * hand_extra)
        a.set_axis(0.0, 0.0, -sgn * (shoulder + arm_up))
        out[hd] = a
    return out


def apply_modes(adj, modes):
    """把 ini 的 ``[(mode, (x,y,z)), ...]`` 叠到 adj 上。

    模式：``r`` 固定旋转 / ``a`` 轴倾 / ``s`` 旋转缩放（官方 ini 里
    ``EYES=s,1.5`` 就是缩放）。后出现的覆盖前面的。
    """
    for mode, vals in modes:
        x, y, z = (list(vals) + [0.0, 0.0, 0.0])[:3]
        if mode == "r":
            adj.set_rot(x, y, z)
        elif mode == "a":
            adj.set_axis(x, y, z)
        elif mode == "s":
            adj.set_rotation_scale(x if x else 1.0)
    return adj
