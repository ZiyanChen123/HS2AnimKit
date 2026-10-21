"""把 H 道具装进 Blender，并挂到角色骨头上。

道具在游戏里的组织方式是（见 ``ripper.prop``）：

    p_item_dildo            ← prefab 根，被挂到角色某根骨上（局部 TRS = 单位）
      └ J_dildo_00 …        ← 自带的骨骼链，动画直接打在这些节点上

所以这里做三件事：

1. 把道具的整棵节点树建成一副独立骨架（每个 Transform 一根骨）；
2. 网格顶点已经在 prefab 根空间，按权重绑到骨上 —— 静态网格压 100% 到
   所属节点，蒙皮网格用游戏里的骨骼槽位；
3. 按数据表（``hdb`` 的 prop 字段）把道具骨架挂到角色骨骼上，并写道具动画。
"""

import bpy
from mathutils import Matrix, Vector

from . import anim
from .ripper import mathutil as mu
from .ripper import prop as _prop

COLL_PROP = "HS2 道具"


class _Bone(object):
    __slots__ = ("path", "parent", "pos", "rot", "scale", "name")


class _Skel(object):
    """给 anim.apply_clip 用的假骨架（只要有 bones[] 就够）。"""

    def __init__(self, bones):
        self.bones = bones

    def __len__(self):
        return len(self.bones)


def _world_matrices(bones):
    """按局部 TRS 累乘出世界矩阵（Unity 空间，未缩放）。"""
    out = []
    for b in bones:
        local = mu.trs_to_mat(b.pos, b.rot, b.scale)
        out.append(mu.mat_mul(out[b.parent], local) if b.parent >= 0 else local)
    return out


def build_prop(assets, pack, prefab, scale=0.1, collection=None,
               name=None, cache_dir=None):
    """返回道具的 armature 对象（连同它的网格子物体）。"""
    data = _prop.extract(assets.game_dir, pack, prefab,
                         cache_dir=cache_dir or assets.cache_dir)
    bones = []
    for bd in data["bones"]:
        b = _Bone()
        b.name = bd["name"]
        b.parent = bd["parent"]
        b.pos = tuple(bd["pos"])
        b.rot = tuple(bd["rot"])
        b.scale = tuple(bd["scale"])
        b.path = bd["path"]
        bones.append(b)

    world = _world_matrices(bones)
    bw = [mu.mat_to_blender(m) for m in world]

    arm = bpy.data.armatures.new(name or ("PROP_" + prefab))
    obj = bpy.data.objects.new(arm.name, arm)
    (collection or bpy.context.collection).objects.link(obj)

    prev = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    names = []
    used = {}
    for i, b in enumerate(bones):
        nm = b.name or "bone"
        if nm in used:
            used[nm] += 1
            nm = "%s_%03d" % (nm, used[nm])
        else:
            used[nm] = 0
        names.append(nm)

    ebs = []
    for i, b in enumerate(bones):
        eb = arm.edit_bones.new(names[i])
        w = bw[i]
        eb.head = Vector((w[0][3] * scale, w[1][3] * scale, w[2][3] * scale))
        yv = Vector((w[0][1], w[1][1], w[2][1]))
        if yv.length < 1e-6:
            yv = Vector((0.0, 1.0, 0.0))
        yv.normalize()
        best = None
        for j, c in enumerate(bones):
            if c.parent != i:
                continue
            d = (Vector((bw[j][0][3], bw[j][1][3], bw[j][2][3])) * scale
                 - eb.head).length
            if d > 1e-5 and (best is None or d < best):
                best = d
        eb.tail = eb.head + yv * max(best or 0.02, 0.006)
        ebs.append(eb)

    for i, b in enumerate(bones):
        if b.parent >= 0:
            ebs[i].parent = ebs[b.parent]
            ebs[i].use_connect = False

    for i in range(len(ebs)):
        z = Vector((bw[i][0][2], bw[i][1][2], bw[i][2][2]))
        if z.length > 1e-6:
            try:
                ebs[i].align_roll(z)
            except Exception:
                pass

    bpy.ops.object.mode_set(mode="OBJECT")
    if prev:
        bpy.context.view_layer.objects.active = prev
    obj.select_set(False)

    obj["hs2_scale"] = scale
    obj["hs2_prop_pack"] = pack
    obj["hs2_prop_prefab"] = prefab
    obj["hs2_prop_bones"] = len(bones)
    # 存下骨骼表（顺序 / 父级 / 路径 / 静止 TRS），写动画时不用再解包
    import json
    rows = []
    for b in bones:
        rows.append([b.name, b.parent, b.path,
                     b.pos[0], b.pos[1], b.pos[2],
                     b.rot[0], b.rot[1], b.rot[2], b.rot[3],
                     b.scale[0], b.scale[1], b.scale[2]])
    obj["hs2_prop_data"] = json.dumps({"bones": rows, "names": names})

    # ---- 网格 ----
    mats = [_make_material(m, "%s_%s" % (prefab, i))
            for i, m in enumerate(data.get("materials") or [])]
    meshes = []
    for md in data["meshes"]:
        me = bpy.data.meshes.new(md["name"] or "prop_mesh")
        verts = [(v[0] * scale, v[1] * scale, v[2] * scale) for v in md["verts"]]
        tris = list(md["tris"])
        if md.get("flip"):
            tris = [tris[i + 2] if i % 3 == 0 else
                    (tris[i - 1] if i % 3 == 1 else tris[i - 2])
                    for i in range(len(tris))]
        faces = [(tris[i], tris[i + 1], tris[i + 2])
                 for i in range(0, len(tris) - 2, 3)]
        me.from_pydata(verts, [], faces)
        me.update()
        if md.get("uvs"):
            uvl = me.uv_layers.new(name="UVMap")
            uvs = md["uvs"]
            for loop in me.loops:
                u = uvs[loop.vertex_index]
                uvl.data[loop.index].uv = (u[0], u[1])
        if md.get("normals"):
            try:
                ns = [(n[0], n[1], n[2]) for n in md["normals"]]
                me.normals_split_custom_set_from_vertices(ns)
            except Exception:
                pass
        for p in me.polygons:
            p.use_smooth = True
        for slot in md.get("materials") or []:
            if 0 <= slot < len(mats):
                me.materials.append(mats[slot])

        mob = bpy.data.objects.new("%s_%s" % (prefab, md["name"]), me)
        (collection or bpy.context.collection).objects.link(mob)

        slots = md.get("bone_slots") or []
        vgs = {}
        for si, bi in enumerate(slots):
            if bi < 0 or bi >= len(names):
                continue
            vgs[si] = mob.vertex_groups.new(name=names[bi])
        for vi, w in enumerate(md["weights"]):
            for k in range(4):
                weight = w[4 + k]
                if weight <= 1e-5:
                    continue
                vg = vgs.get(w[k])
                if vg is not None:
                    vg.add([vi], weight, "REPLACE")
        mob.parent = obj
        mod = mob.modifiers.new("Armature", "ARMATURE")
        mod.object = obj
        mob.matrix_parent_inverse = obj.matrix_world.inverted()
        mob["hs2_prop"] = prefab
        meshes.append(mob)

    obj["hs2_prop_meshes"] = len(meshes)
    return obj


def _make_material(info, name):
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name or "prop_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    c = info.get("color") or (1.0, 1.0, 1.0, 1.0)
    if bsdf is not None:
        try:
            bsdf.inputs["Base Color"].default_value = (c[0], c[1], c[2], 1.0)
            bsdf.inputs["Roughness"].default_value = 0.6
        except Exception:
            pass
    mat.diffuse_color = (c[0], c[1], c[2], 1.0)
    return mat


# --------------------------------------------------------------------------
# 挂到角色骨骼
# --------------------------------------------------------------------------

def attach_to_bone(prop_obj, char_obj, bone_name, attach="", scale_adapt=False):
    """把道具骨架挂到角色骨骼上。

    默认局部 TRS 是单位矩阵（道具根正好落在骨头上），这和游戏里一致。
    但有些道具的「挂点」不在根上 —— 数据表第 14 列（くっつけるもの）写的是
    道具内部的某个节点，比如手铐要拿 `k_himo_armL` 去对齐手腕。这时候得反推
    一个偏移：让那个节点（而不是根）落在骨头原点上。

        prop_world @ L = bone_world   →   prop_world = bone_world @ L⁻¹

    ``scale_adapt`` 打开时，额外把父级的缩放补进来（游戏里「親スケールの適応」）。
    """
    pb = char_obj.pose.bones.get(bone_name)
    if pb is None:
        return False
    prop_obj.parent = char_obj
    prop_obj.parent_type = "BONE"
    prop_obj.parent_bone = bone_name
    # Blender 的骨骼父级原点在骨尾，先算清楚再反推 parent_inverse
    m = char_obj.matrix_world @ pb.matrix @ Matrix.Translation((0.0, pb.length, 0.0))
    prop_obj.matrix_parent_inverse = m.inverted() @ (char_obj.matrix_world @ pb.matrix)

    basis = Matrix.Identity(4)
    node = (attach or "").strip()
    if node:
        pb_node = prop_obj.pose.bones.get(node)
        if pb_node is None:
            # 兼容「用节点上的名字找一下」：道具里可能重名加了后缀
            for cand in prop_obj.pose.bones:
                if cand.name.startswith(node):
                    pb_node = cand
                    break
        if pb_node is not None:
            local = pb_node.bone.matrix_local
            try:
                basis = local.inverted()
            except Exception:
                basis = Matrix.Identity(4)
            prop_obj["hs2_attach_node"] = pb_node.name
    if scale_adapt:
        s = char_obj.matrix_world.to_scale()
        if abs(s.x - 1.0) > 1e-6 or abs(s.y - 1.0) > 1e-6 or abs(s.z - 1.0) > 1e-6:
            basis = basis @ Matrix.Diagonal(s.to_4d())
    prop_obj.matrix_basis = basis
    prop_obj["hs2_attached_to"] = bone_name
    # 立刻刷新一次，不然 UI 里读到的 matrix_world 还是旧的
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    return True


def prop_skel(prop_obj):
    """从道具骨架上的自定义属性反推「假骨架」，供 anim.apply_clip 用。"""
    import json
    raw = prop_obj.get("hs2_prop_data")
    if not raw:
        return None, None
    try:
        d = json.loads(raw)
    except Exception:
        return None, None
    bones = []
    for row in d.get("bones") or []:
        b = _Bone()
        b.name = row[0]
        b.parent = int(row[1])
        b.path = row[2]
        b.pos = (row[3], row[4], row[5])
        b.rot = (row[6], row[7], row[8], row[9])
        b.scale = (row[10], row[11], row[12])
        bones.append(b)
    return _Skel(bones), d.get("names") or []


def prop_paths(prop_obj):
    skel, _names = prop_skel(prop_obj)
    return [b.path for b in (skel.bones if skel else [])]


def load_clip(assets, prop_obj, bundle_path, pid, fps=None):
    """道具动画要用**道具自己的**路径表来解，不能套角色的骨架路径表。"""
    return assets.load_ref(bundle_path, pid, fps=fps,
                           paths=prop_paths(prop_obj))


def apply_clip(prop_obj, clip, action_name=None, start_frame=0):
    """把道具动画写成 Action（复用 anim.apply_clip 的逐帧世界矩阵解算）。"""
    skel, names = prop_skel(prop_obj)
    if skel is None:
        raise RuntimeError("这个道具没有骨骼数据（不是本插件导入的？）")
    scale = float(prop_obj.get("hs2_scale", 0.1))
    return anim.apply_clip(prop_obj, skel, names, clip, scale=scale,
                           action_name=action_name or
                           ("PROP_" + (clip.name or "clip")),
                           start_frame=start_frame)
