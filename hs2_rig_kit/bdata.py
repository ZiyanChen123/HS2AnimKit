"""把解包出来的数据装进 Blender：骨架 + 蒙皮网格。

关键点：骨骼的「静止矩阵」直接取自游戏里的默认世界矩阵，
和网格的 bindpose 完全一致，所以模型和骨骼天然对齐。
"""

import bpy
from mathutils import Matrix, Vector

from .ripper import mathutil as mu


def _unique_names(skel):
    """骨骼名去重（游戏里有重名节点，例如 GameObject、f_t_hips）。"""
    used = {}
    out = []
    for b in skel.bones:
        n = b.name or "bone"
        if n in used:
            used[n] += 1
            n = "%s_%03d" % (n, used[n])
        else:
            used[n] = 0
        out.append(n)
    return out


def world_matrices(skel, scale=1.0):
    """按 Unity 局部 TRS 累乘出每个骨骼的世界矩阵，最后整体转换到 Blender 空间。"""
    raw = []
    for i, b in enumerate(skel.bones):
        pos = (b.pos[0] * scale, b.pos[1] * scale, b.pos[2] * scale)
        local = mu.trs_to_mat(pos, b.rot, b.scale)
        if b.parent >= 0:
            local = mu.mat_mul(raw[b.parent], local)   # 注意：先在 Unity 空间累乘
        raw.append(local)
    return [mu.mat_to_blender(m) for m in raw]


def _length_for(skel, i, world, heads):
    """骨头长度：取最近子骨的距离，没有子骨就按父骨距离估。"""
    b = skel.bones[i]
    head = heads[i]
    best = None
    for c in b.children:
        d = (heads[c] - head).length
        if d > 1e-5 and (best is None or d < best):
            best = d
    if best is None and b.parent >= 0:
        best = (head - heads[b.parent]).length * 0.6
    if best is None:
        best = 0.05
    return max(best, 0.012)


def build_armature(skel, name="HS2_Default", scale=0.1, collection=None,
                   extra=None):
    """建骨架。extra 是额外骨骼（头部/面部），world 已经是骨架空间（Unity）。

    extra: [{"name","parent","world"(4x4 Unity, 未缩放),"deform"}, ...]
    """
    names = _unique_names(skel)
    world = world_matrices(skel, scale)
    heads = [Vector((m[0][3], m[1][3], m[2][3])) for m in world]

    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    (collection or bpy.context.collection).objects.link(obj)

    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    ebs = []
    for i, b in enumerate(skel.bones):
        eb = arm.edit_bones.new(names[i])
        eb.head = heads[i]
        # 方向严格跟随游戏里骨骼的 +Y，长度取子骨距离 —— 静止矩阵和 Unity 一致。
        # 注意：不能用 eb.matrix 直接赋值，零长度的根骨（p_cf_anim / cf_J_Root /
        # cf_N_height）会得到 90°X 的错误朝向，整副骨架跟着转 90°。
        yv = Vector((world[i][0][1], world[i][1][1], world[i][2][1]))
        if yv.length < 1e-6:
            yv = Vector((0.0, 1.0, 0.0))
        yv.normalize()
        eb.tail = heads[i] + yv * _length_for(skel, i, world, heads)
        ebs.append(eb)

    for i, b in enumerate(skel.bones):
        if b.parent >= 0:
            ebs[i].parent = ebs[b.parent]
            ebs[i].use_connect = False

    # 用游戏里的世界矩阵定 roll
    for i in range(len(ebs)):
        z_axis = Vector((world[i][0][2], world[i][1][2], world[i][2][2]))
        if z_axis.length > 1e-6:
            try:
                ebs[i].align_roll(z_axis)
            except Exception:
                pass

    # ---------- 额外骨骼（头部） ----------
    if extra:
        _add_extra_edit(arm, extra, scale)

    bpy.ops.object.mode_set(mode="OBJECT")
    if prev_active:
        bpy.context.view_layer.objects.active = prev_active
    obj.select_set(False)

    obj["hs2_scale"] = scale
    obj["hs2_bone_count"] = len(skel) + (len(extra) if extra else 0)
    return obj, names


def _set_bone_matrix(eb, world, length):
    """严格按世界矩阵摆骨骼（head + 朝向），长度单独给。"""
    rot = Matrix((
        (world[0][0], world[0][1], world[0][2]),
        (world[1][0], world[1][1], world[1][2]),
        (world[2][0], world[2][1], world[2][2]),
    ))
    try:
        q = rot.to_quaternion()
        q.normalize()
        m = q.to_matrix().to_4x4()
    except Exception:
        m = Matrix.Identity(4)
    m.translation = Vector((world[0][3], world[1][3], world[2][3]))
    try:
        eb.matrix = m
        eb.length = max(length, 0.008)
    except Exception:
        yv = Vector((world[0][1], world[1][1], world[2][1]))
        if yv.length < 1e-6:
            yv = Vector((0.0, 0.0, 1.0))
        eb.tail = eb.head + yv.normalized() * max(length, 0.008)


def _add_extra_edit(arm, extra, scale):
    """在 EDIT 模式下插入额外骨骼（world 为骨架空间 Unity 矩阵）。"""
    ex_world = {}
    for e in extra:
        ex_world[e["name"]] = mu.mat_to_blender(_scale_translation(e["world"], scale))

    for e in extra:
        nm = e["name"]
        if nm in arm.edit_bones:
            continue
        w = ex_world[nm]
        eb = arm.edit_bones.new(nm)
        eb.head = Vector((w[0][3], w[1][3], w[2][3]))
        yv = Vector((w[0][1], w[1][1], w[2][1]))
        if yv.length < 1e-6:
            yv = Vector((0.0, 1.0, 0.0))
        yv.normalize()
        best = None
        for c in extra:
            if c["parent"] == nm:
                cw = ex_world[c["name"]]
                d = (Vector((cw[0][3], cw[1][3], cw[2][3])) - eb.head).length
                if d > 1e-5 and (best is None or d < best):
                    best = d
        eb.tail = eb.head + yv * max(best or 0.02, 0.008)
        eb.use_deform = bool(e.get("deform", True))
        z = Vector((w[0][2], w[1][2], w[2][2]))
        if z.length > 1e-6:
            try:
                eb.align_roll(z)
            except Exception:
                pass

    for e in extra:
        eb = arm.edit_bones.get(e["name"])
        if eb is None:
            continue
        pb = e.get("parent")
        if pb:
            tgt = arm.edit_bones.get(pb)
            if tgt is not None:
                eb.parent = tgt
                eb.use_connect = False


def add_extra_bones(arm_obj, extra, scale=0.1):
    """给已存在的骨架补骨骼（头部）。"""
    prev_active = bpy.context.view_layer.objects.active
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    _add_extra_edit(arm_obj.data, extra, scale)
    bpy.ops.object.mode_set(mode="OBJECT")
    if prev_active:
        bpy.context.view_layer.objects.active = prev_active
    return len(extra)


def _scale_translation(m, scale):
    if abs(scale - 1.0) < 1e-9:
        return m
    return tuple(
        tuple(m[r][c] * (scale if (r < 3 and c == 3) else 1.0) for c in range(4))
        for r in range(4)
    )


def build_mesh(data, skel, names, arm_obj, name="HS2_Body", scale=0.1,
               collection=None, smooth=True, xform=None):
    """建蒙皮网格。

    xform：Unity 空间的额外变换（头部网格的顶点在头 prefab 局部空间里，
    要先乘 cf_J_Head 的世界矩阵）。
    group_names：可选的「权重槽位 -> 顶点组名」表，不走骨架下标。
    """
    if xform is not None:
        verts = [mu.to_blender_pos(mu.mat_apply(xform, v), scale)
                 for v in data["vertices"]]
        normals = [mu.mat_apply(_rot_only(xform), n) for n in data.get("normals") or []]
    else:
        verts = [mu.to_blender_pos(v, scale) for v in data["vertices"]]
        normals = [mu.to_blender_pos(n) for n in data.get("normals") or []]

    tris = list(data["triangles"])
    if data.get("flip"):
        tris = [tris[i + 2] if i % 3 == 0 else (tris[i - 1] if i % 3 == 1 else tris[i - 2])
                for i in range(len(tris))]

    faces = [(tris[i], tris[i + 1], tris[i + 2]) for i in range(0, len(tris) - 2, 3)]

    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()

    if data.get("uvs"):
        uv = me.uv_layers.new(name="UVMap")
        uvs = data["uvs"]
        for loop in me.loops:
            u = uvs[loop.vertex_index]
            uv.data[loop.index].uv = (u[0], u[1])

    obj = bpy.data.objects.new(name, me)
    (collection or bpy.context.collection).objects.link(obj)

    if smooth:
        for p in me.polygons:
            p.use_smooth = True

    if normals:
        try:
            me.normals_split_custom_set_from_vertices(normals)
        except Exception:
            pass

    # 蒙皮权重
    if data.get("group_names"):
        groups = {i: n for i, n in enumerate(data["group_names"])}
    else:
        groups = {}
        for slot, bi in enumerate(data.get("bone_index") or []):
            if bi is None or bi < 0 or bi >= len(names):
                continue
            groups[slot] = names[bi]

    vgs = {}
    for slot, bname in groups.items():
        if bname not in vgs:
            vgs[bname] = obj.vertex_groups.new(name=bname)

    for vi, w in enumerate(data["weights"]):
        for k in range(4):
            weight = w[4 + k]
            if weight <= 1e-5:
                continue
            slot = w[k]
            bname = groups.get(slot)
            if bname:
                vgs[bname].add([vi], weight, "REPLACE")

    obj.parent = arm_obj
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj
    obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
    return obj


def _rot_only(m):
    return (
        (m[0][0], m[0][1], m[0][2], 0.0),
        (m[1][0], m[1][1], m[1][2], 0.0),
        (m[2][0], m[2][1], m[2][2], 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def rest_matrices(arm_obj, names):
    """Blender 侧每个骨骼的静止矩阵（armature 空间）。"""
    out = []
    for n in names:
        b = arm_obj.data.bones.get(n)
        out.append(b.matrix_local.copy() if b else Matrix())
    return out
