"""控制器的造型（custom shape）。

**关键前提**：HS2 是「纯枢轴骨架」——所有骨静止时旋转都是**单位矩阵**，
所以每根控制骨的本地空间就是世界空间：**X = 左右，Y = 前后，Z = 上下**。

这意味着：按「XZ 平面」画的圆环（法线 = 骨骼 Y = 世界 Y）会是**竖着的、
面朝前**的——看起来像贴在胸口的盘子，很不专业。所以这里全部改成：

    ring()       水平圆环（法线 +Z）—— 躯干、腿、头
    ring_side()  竖直圆环（法线 +X）—— 手臂（手臂沿 X 伸出去）
    foot()       贴地的脚掌轮廓 + 脚尖箭头
    arrow()      根控制器：贴地大环 + 前方箭头

所有造型都建在 ``HS2_WGT`` 集合里，物件本身不参与渲染/导出。
"""

import math

import bpy

WGT_COLL = "HS2_WGT"

TAU = 2.0 * math.pi


# --------------------------------------------------------------------------
# 基础图元
# --------------------------------------------------------------------------

def _merge(*parts):
    verts, edges = [], []
    for v, e in parts:
        off = len(verts)
        verts.extend(v)
        edges.extend([(a + off, b + off) for a, b in e])
    return verts, edges


def _h_arc(radius, segments, arc=TAU, phase=0.0, z=0.0):
    """水平圆弧：X = cos，Y = sin（世界坐标）。"""
    vs, es = [], []
    closed = abs(arc - TAU) < 1e-6
    n = segments if closed else segments + 1
    for i in range(n):
        a = phase + arc * (i / segments)
        vs.append((math.cos(a) * radius, math.sin(a) * radius, z))
    m = segments if closed else segments
    es = [(i, (i + 1) % n) for i in range(m)]
    return vs, es


def _h_line(a, b, z=0.0):
    return [(a[0], a[1], z), (b[0], b[1], z)], [(0, 1)]


def _h_poly(points, z=0.0, close=True):
    vs = [(p[0], p[1], z) for p in points]
    es = [(i, i + 1) for i in range(len(vs) - 1)]
    if close:
        es.append((len(vs) - 1, 0))
    return vs, es


def _v_arc(radius, segments, arc=TAU, phase=0.0):
    """竖直圆弧（法线 +X）：Y = cos，Z = sin。"""
    vs, es = [], []
    closed = abs(arc - TAU) < 1e-6
    n = segments if closed else segments + 1
    for i in range(n):
        a = phase + arc * (i / segments)
        vs.append((0.0, math.cos(a) * radius, math.sin(a) * radius))
    es = [(i, (i + 1) % n) for i in range(segments)]
    return vs, es


# --------------------------------------------------------------------------
# 造型
# --------------------------------------------------------------------------

def ring():
    """躯干/腿：水平圆环，前方留缺口 + 四向刻度，一眼看出朝向和旋转。"""
    gap = 0.16                      # 缺口张角（弧度占比）
    span = TAU - gap
    phase = -math.pi / 2 + gap / 2  # 缺口正对 -Y（角色前方）
    body = _h_arc(1.0, 40, arc=span, phase=phase)
    ticks = []
    for a in (0.0, math.pi / 2, math.pi, -math.pi / 2):
        c, s = math.cos(a), math.sin(a)
        ticks.append(_h_line((c * 1.04, s * 1.04), (c * 1.20, s * 1.20)))
    return _merge(body, *ticks)


def ring_small():
    """小号水平环：颈、手指。"""
    return _h_arc(1.0, 24)


def ring_side():
    """手臂：竖直圆环（法线 +X），垂在手臂外侧。"""
    body = _v_arc(1.0, 32)
    ticks = []
    for a in (0.0, math.pi / 2, math.pi, -math.pi / 2):
        c, s = math.cos(a), math.sin(a)
        ticks.append(([(0.0, c * 1.05, s * 1.05), (0.0, c * 1.20, s * 1.20)],
                      [(0, 1)]))
    return _merge(body, *ticks)


def foot():
    """脚掌轮廓：画在本地 XY 平面（水平脚印），-Y = 脚尖（角色前方）。

    脚 IK 控制骨与游戏骨同 rest 朝向（HS2 骨静止 = 单位旋转，本地 Y = 世界 +Y），
    所以画在 XY 平面就是贴地的脚印。控制骨的 rest 朝向不能乱设 ——
    cf_J_Foot01 靠 COPY_ROTATION(WORLD/REPLACE) 跟随它，rest 不一致会把脚拧歪。
    """
    outline = [
        (-0.34, 0.92), (-0.46, 0.30), (-0.48, -0.22), (-0.40, -0.62),
        (-0.24, -0.86), (0.0, -0.94), (0.24, -0.86), (0.40, -0.62),
        (0.48, -0.22), (0.46, 0.30), (0.34, 0.92),
    ]
    arrow = _h_poly([(-0.16, -0.95), (0.0, -1.30), (0.16, -0.95)],
                    close=False)
    return _merge(_h_poly(outline),
                  _h_line((-0.34, 0.92), (0.34, 0.92)),
                  _h_line((-0.12, 0.0), (0.12, 0.0)),
                  _h_line((0.0, -0.72), (0.0, 0.72)),
                  arrow)


def hand(side="L"):
    """手掌：贴在水平面上，指尖朝外（L 朝 -X，R 朝 +X）。"""
    s = -1.0 if side == "L" else 1.0
    outline = [
        (0.0, -0.52), (0.66 * s, -0.60), (1.02 * s, -0.26), (1.10 * s, 0.18),
        (0.68 * s, 0.54), (0.0, 0.52),
    ]
    return _merge(_h_poly(outline),
                  _h_line((0.0, -0.52), (0.0, 0.52)),
                  _h_line((0.34 * s, -0.28), (1.02 * s, -0.28)),
                  _h_line((0.34 * s, 0.26), (1.02 * s, 0.26)))


def arrow():
    """根控制器：贴地大环 + 正前方的方向箭头。"""
    body = _h_arc(1.0, 48)
    shaft = _h_line((0.0, -0.62), (0.0, -1.55))
    head = _h_poly([(-0.20, -1.16), (0.0, -1.58), (0.20, -1.16)], close=False)
    # 环上的「脚」：四个小十字，方便看清中心
    marks = []
    for a in (math.pi / 4, 3 * math.pi / 4, -math.pi / 4, -3 * math.pi / 4):
        c, s = math.cos(a), math.sin(a)
        marks.append(_h_line((c * 0.94, s * 0.94), (c * 1.06, s * 1.06)))
    return _merge(body, shaft, head, *marks)


def diamond():
    """极向控制器：小八面体，带一根指向 +Y 的短刺。"""
    v = [(0.6, 0, 0), (-0.6, 0, 0), (0, 0.6, 0), (0, -0.6, 0),
         (0, 0, 0.6), (0, 0, -0.6)]
    e = [(0, 2), (2, 1), (1, 3), (3, 0), (0, 5), (5, 1), (1, 4), (4, 0),
         (2, 5), (5, 3), (3, 4), (4, 2)]
    spine = ([(0.0, -0.6, 0.0), (0.0, -1.3, 0.0)], [(0, 1)])
    return _merge((v, e), spine)


def knuckle():
    """手指：极小的水平环。"""
    return _h_arc(1.0, 12)


SHAPES = {
    "ring": ring,
    "ring_small": ring_small,
    "ring_side": ring_side,
    "foot": foot,
    "hand_L": lambda: hand("L"),
    "hand_R": lambda: hand("R"),
    "arrow": arrow,
    "diamond": diamond,
    "knuckle": knuckle,
}


# --------------------------------------------------------------------------
# 物件管理
# --------------------------------------------------------------------------

def _exclude_layer(coll, exclude=True):
    """把集合从视图层里排除（Rigify 的做法）：物件不出现在场景里，
    但 custom shape 仍然能画 —— 因为画的只是网格数据。"""
    try:
        lc = bpy.context.view_layer.layer_collection.children.get(coll.name)
        if lc is not None:
            lc.exclude = exclude
    except Exception:
        pass


def _collection():
    coll = bpy.data.collections.get(WGT_COLL)
    if coll is None:
        coll = bpy.data.collections.new(WGT_COLL)
        bpy.context.scene.collection.children.link(coll)
        coll.hide_render = True
        _exclude_layer(coll, True)
    return coll


def ensure(name):
    """取（或造）一个造型物件。旧版造型会重建，避免残留老几何。"""
    obj_name = "WGT_" + name
    obj = bpy.data.objects.get(obj_name)
    if obj is not None:
        return obj

    verts, edges = SHAPES[name]()
    me = bpy.data.meshes.new(obj_name)
    me.from_pydata(verts, edges, [])
    me.update()

    obj = bpy.data.objects.new(obj_name, me)
    obj.hide_render = True
    obj.display_type = "WIRE"
    _collection().objects.link(obj)
    return obj


def rebuild_all():
    """重建全部造型（改了形状以后调用，把旧 mesh 覆盖掉）。"""
    for n in SHAPES:
        obj = bpy.data.objects.get("WGT_" + n)
        if obj is not None:
            me = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if me.users == 0:
                bpy.data.meshes.remove(me)
    build_all()


def build_all():
    for n in SHAPES:
        ensure(n)


def clear():
    for n in SHAPES:
        obj = bpy.data.objects.get("WGT_" + n)
        if obj is not None:
            me = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if me.users == 0:
                bpy.data.meshes.remove(me)
    coll = bpy.data.collections.get(WGT_COLL)
    if coll is not None and not coll.objects:
        bpy.data.collections.remove(coll)
