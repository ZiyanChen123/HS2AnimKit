"""数学工具：4x4 矩阵、四元数、坐标转换。

约定
----
Unity 是左手系、Y 轴向上；Blender 是右手系、Z 轴向上。
本模块统一使用「Unity 空间」做计算，最后交给 Blender 时用 :func:`to_blender`
做一次整体旋转（绕 X 轴 +90°）：(x, y, z) -> (x, -z, y)。
"""

import math

IDENTITY = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def quat_to_mat(q):
    """四元数 (x, y, z, w) -> 3x3 旋转矩阵（行主序）。"""
    x, y, z, w = q
    n = x * x + y * y + z * z + w * w
    if n > 0.0 and abs(n - 1.0) > 1e-9:
        inv = 1.0 / math.sqrt(n)
        x, y, z, w = x * inv, y * inv, z * inv, w * inv
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)),
        (2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)),
        (2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)),
    )


def trs_to_mat(pos, rot, scale):
    """Unity 局部 TRS -> 4x4 矩阵。"""
    r = quat_to_mat(rot)
    sx, sy, sz = scale
    return (
        (r[0][0] * sx, r[0][1] * sy, r[0][2] * sz, pos[0]),
        (r[1][0] * sx, r[1][1] * sy, r[1][2] * sz, pos[1]),
        (r[2][0] * sx, r[2][1] * sy, r[2][2] * sz, pos[2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def mat_mul(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4)
    )


def mat_apply(m, p):
    return (
        m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
        m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
        m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3],
    )


def mat_to_quat(m):
    """3x3 旋转部分 -> 四元数 (x, y, z, w)。"""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    n = math.sqrt(x * x + y * y + z * z + w * w)
    return (x / n, y / n, z / n, w / n)


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


def quat_normalize(q):
    n = math.sqrt(sum(c * c for c in q))
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(c / n for c in q)


# 绕 X 轴 +90°：Unity (x,y,z) -> Blender (x,-z,y)
_UP = (math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))

_C = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, -1.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)
_C_INV = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def mat_to_blender(m):
    """Unity 空间矩阵 -> Blender 空间（整体旋转，等价于换基）。"""
    return mat_mul(mat_mul(_C, m), _C_INV)


def to_blender_pos(p, scale=1.0):
    return (p[0] * scale, -p[2] * scale, p[1] * scale)


def to_blender_quat(q):
    """把 Unity 空间的旋转搬到 Blender 空间（基变换）。"""
    return quat_normalize(quat_mul(quat_mul(_UP, q), quat_conj(_UP)))
