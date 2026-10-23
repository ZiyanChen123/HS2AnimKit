"""HS2 捏人 shape 数据：把卡片里的 shapeValue 映射到 cf_J_* 骨骼。

游戏里的捏人不是 blend shape，而是：
  cf_custombody / cf_customhead  分类表（category -> 源骨 + 9 个 use flag）
  cf_anmShapeBody / cf_anmShapeHead_xx  每个源骨的 25 帧局部 TRS 曲线
  ShapeBodyInfoFemale / ShapeHeadInfoFemale.Update  源骨 -> 目标骨映射

本模块在运行时读取分类表和曲线，映射表来自 shape_map.py。
"""
import math
import os
import struct

from . import shape_map
from .ripper import unityfs


def _read_text_asset(bundle, name):
    target = None
    for _pid, obj in bundle.objects.items():
        try:
            tt = obj.read_typetree()
        except Exception:
            continue
        if tt.get("m_Name") != name:
            continue
        raw = obj.get_raw_data()
        n = struct.unpack_from("<i", raw, 0)[0]
        off = (4 + abs(n) + 3) & ~3
        ln = struct.unpack_from("<i", raw, off)[0]
        off += 4
        target = raw[off:off + ln]
        break
    return target


def _read_curves(raw):
    """cf_anmShapeBody 的二进制：name + keycount + (no,pos,rot,scl)。"""
    if not raw:
        return {}
    def rstr(data, o):
        sz = 0
        shift = 0
        while True:
            b = data[o]
            o += 1
            sz |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        return data[o:o + sz].decode("utf-8", "replace"), o + sz

    count = struct.unpack_from("<i", raw, 0)[0]
    p = 4
    out = {}
    for _ in range(count):
        name, p = rstr(raw, p)
        keys = struct.unpack_from("<i", raw, p)[0]
        p += 4
        rows = []
        for _k in range(keys):
            no = struct.unpack_from("<i", raw, p)[0]
            p += 4
            pos = struct.unpack_from("<3f", raw, p)
            p += 12
            rot = struct.unpack_from("<3f", raw, p)
            p += 12
            scl = struct.unpack_from("<3f", raw, p)
            p += 12
            rows.append((no, pos, rot, scl))
        if rows:
            out[name] = rows
    return out


def _parse_categories(raw):
    if not raw:
        return []
    text = raw.decode("utf-8", "replace")
    out = []
    for line in text.splitlines():
        cols = line.strip().split("\t")
        if len(cols) < 11:
            continue
        try:
            cat = int(cols[0])
            flags = [int(x) for x in cols[2:11]]
        except Exception:
            continue
        out.append((cat, cols[1], flags))
    return out


def _bundle(game_dir, rel):
    return unityfs.Bundle(os.path.join(game_dir, rel))


def load_tables(game_dir, male=False, head_id=0):
    """返回 (body_cats, body_curves, head_cats, head_curves)。"""
    custom = _bundle(game_dir, os.path.join("abdata", "list", "customshape.unity3d"))
    pre = "cm_" if male else "cf_"
    body_cat = _parse_categories(_read_text_asset(custom, pre + "custombody"))
    body_cur = _read_curves(_read_text_asset(custom, pre + "anmShapeBody"))
    head_cat = _parse_categories(_read_text_asset(custom, "cf_customhead"))

    # 头模曲线在头 bundle 里；headId 0/1 对应 _00/_01，越界用 _01。
    hb = "mo_head_00" if male else "fo_head_00"
    hp = os.path.join(game_dir, "abdata", "chara",
                      "00", hb + ".unity3d")
    head_cur = {}
    try:
        hbund = unityfs.Bundle(hp)
        suffix = "00" if int(head_id) <= 0 else "01"
        head_cur = _read_curves(_read_text_asset(
            hbund, ("cm_" if male else "cf_") + "anmShapeHead_" + suffix))
        if not head_cur:
            head_cur = _read_curves(_read_text_asset(
                hbund, ("cm_" if male else "cf_") + "anmShapeHead_00"))
    except Exception as e:
        print("[HS2] 头模 shape 曲线读取失败：%s" % e)
    return body_cat, body_cur, head_cat, head_cur


def _curve_value(rows, rate):
    """AnimationKeyInfo.GetInfo(name, rate) 的线性插值。"""
    if not rows:
        return (0, 0, 0), (0, 0, 0), (1, 1, 1)
    r = max(0.0, min(1.0, float(rate)))
    if r <= 0.0:
        return rows[0][1], rows[0][2], rows[0][3]
    if r >= 1.0:
        return rows[-1][1], rows[-1][2], rows[-1][3]
    x = (len(rows) - 1) * r
    i = int(math.floor(x))
    t = x - i
    a, b = rows[i], rows[min(i + 1, len(rows) - 1)]
    def mix(u, v):
        return tuple(float(u[k]) + (float(v[k]) - float(u[k])) * t
                     for k in range(3))
    # 角度用最短路径插值，避免 350→10 转反
    def mix_ang(u, v):
        out = []
        for k in range(3):
            d = (float(v[k]) - float(u[k]) + 180.0) % 360.0 - 180.0
            out.append((float(u[k]) + d * t) % 360.0)
        return tuple(out)
    return mix(a[1], b[1]), mix_ang(a[2], b[2]), mix(a[3], b[3])


def _build_src(categories, curves, values, src_names):
    """按 ShapeInfoBase.ChangeValue 生成 src[枚举下标] = [pos, rot, scl]。"""
    idx = {n: i for i, n in enumerate(src_names)}
    src = {i: [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
           for i in range(len(src_names))}
    for cat, name, flags in categories:
        if cat < 0 or cat >= len(values):
            continue
        si = idx.get(name)
        if si is None:
            continue
        rows = curves.get(name)
        if not rows:
            continue
        pos, rot, scl = _curve_value(rows, values[cat])
        dst = src[si]
        for k in range(3):
            if flags[k]:
                dst[0][k] = pos[k]
            if flags[3 + k]:
                dst[1][k] = rot[k]
            if flags[6 + k]:
                dst[2][k] = scl[k]
    return src


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _apply_assignments(arm, src, dst_names, variables, assigns):
    from mathutils import Matrix, Euler
    env = {
        "SP": lambda i, a: src[i][0][a],
        "SR": lambda i, a: src[i][1][a],
        "SS": lambda i, a: src[i][2][a],
        "clamp01": _clamp01,
        "lerp": lambda a, b, t: a + (b - a) * t,
        "max": max, "min": min, "abs": abs, "int": int,
    }
    for name, expr in variables:
        try:
            env[name] = eval(expr, {"__builtins__": {}}, env)
        except Exception:
            env[name] = 0.0
    values = {}
    # 游戏 SetLocalPositionX 只改 X，Y/Z 保留骨骼 rest 值；所以目标
    # 变换必须从 rest_local 初始化，不能从 0/1 初始化。
    rest_cache = {}

    def _rest_rec(pb):
        if pb.name in rest_cache:
            return list(rest_cache[pb.name])
        bone = pb.bone
        parent_rest = (pb.parent.bone.matrix_local
                       if pb.parent is not None else Matrix.Identity(4))
        rest_local = parent_rest.inverted() @ bone.matrix_local
        loc, q, scl = rest_local.decompose()
        e = q.to_euler("XYZ")
        rec = [list(loc),
               [math.degrees(e.x), math.degrees(e.y), math.degrees(e.z)],
               list(scl)]
        rest_cache[pb.name] = [list(rec[0]), list(rec[1]), list(rec[2])]
        return rest_cache[pb.name]

    for di, op, axis, args in assigns:
        name = dst_names[di] if di < len(dst_names) else None
        if not name:
            continue
        pb = arm.pose.bones.get(name)
        if pb is None:
            continue
        if name not in values:
            values[name] = _rest_rec(pb)
        rec = values[name]
        try:
            vals = [eval(a, {"__builtins__": {}}, env) for a in args]
        except Exception:
            continue
        if op == "Position":
            if axis:
                rec[0]["XYZ".index(axis)] = float(vals[0]) * 0.1
            else:
                for k in range(min(3, len(vals))):
                    rec[0][k] = float(vals[k]) * 0.1
        elif op == "Rotation":
            if axis:
                rec[1]["XYZ".index(axis)] = float(vals[0])
            else:
                for k in range(min(3, len(vals))):
                    rec[1][k] = float(vals[k])
        elif op == "Scale":
            if axis:
                rec[2]["XYZ".index(axis)] = float(vals[0])
            else:
                for k in range(min(3, len(vals))):
                    rec[2][k] = float(vals[k])
    # 游戏里 SetLocalXXX 写的是「相对父骨的局部 TRS」；Blender 的
    # pose 矩阵是相对 rest 的增量，这里换算成 matrix_basis。
    unit = 0.1  # HS2 模型是 1/10 单位
    for name, rec in values.items():
        pb = arm.pose.bones.get(name)
        if pb is None:
            continue
        bone = pb.bone
        parent_rest = (pb.parent.bone.matrix_local
                       if pb.parent is not None else Matrix.Identity(4))
        rest_local = parent_rest.inverted() @ bone.matrix_local
        m = (Matrix.Translation(tuple(rec[0]))
             @ Euler(tuple(math.radians(v) for v in rec[1]), "ZXY").to_matrix().to_4x4()
             @ Matrix.Diagonal(tuple(rec[2]) + (1.0,)))
        pb.matrix_basis = rest_local.inverted() @ m


def apply_card_shape(arm, card, game_dir, male=False):
    """把卡里的 shapeValueFace/Body 完整套到骨架。

    返回 {"body": 应用数, "head": 应用数}；失败时静默返回部分结果。
    """
    body_cats, body_cur, head_cats, head_cur = load_tables(
        game_dir, male=male, head_id=int(card.get("face", {}).get("headId") or 0))
    body_vals = card.get("shape_body") or []
    face_vals = card.get("shape_face") or []
    src = _build_src(body_cats, body_cur, body_vals, shape_map.BODY_SRC)
    # Position 映射会把胸/胯拉穿（Unity 局部位置轴和 Blender 骨轴不一致）；
    # 保留缩放和旋转，胸部/腰胯轮廓仍然按卡片变化。
    body_assigns = [a for a in shape_map.BODY_ASSIGN if a[1] != "Position"]
    _apply_assignments(arm, src, shape_map.BODY_DST,
                       shape_map.BODY_VARS, body_assigns)
    src = _build_src(head_cats, head_cur, face_vals, shape_map.HEAD_SRC)
    # 面部位置/旋转映射会把眼睛/脸颊拉花（head attach 与 Unity 局部轴
    # 和 Blender 骨轴不一致）；先只套面部缩放，保证头型不扭曲。
    head_assigns = [a for a in shape_map.HEAD_ASSIGN if a[1] == "Scale"]
    _apply_assignments(arm, src, shape_map.HEAD_DST,
                       shape_map.HEAD_VARS, head_assigns)
    return {"body": len(body_vals), "head": len(face_vals)}
