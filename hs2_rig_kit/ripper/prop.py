"""H 道具（prefab）解包。

道具包 ``abdata/h/h_item/<pack>.unity3d`` 里是一堆**自成一体的 prefab**：

    p_item_dildo
      ├ N_dildo_Angle              ← 角度控制节点
      │  └ J_dildo_root → J_dildo_00 … J_dildo_06   ← 骨骼链
      ├ O_dildo_00                 ← SkinnedMeshRenderer（8 骨蒙皮）
      └ O_dildo_00_d               ← 另一个 SMR

没有 Avatar，也没有跟角色共用的骨架 —— 每个道具一套自己的小骨架，
动作就直接打在这些 Transform 上（见 ``hdb`` 里连到 ``animator/h/h_item``）。

所以导入 Blender 的做法是：把子树里**所有 Transform 都建成一根骨**，
网格顶点先换算到 prefab 根空间，再按 100% 权重绑到各自的骨上。
这样静态网格（MeshFilter）和蒙皮网格（SMR）用同一套逻辑就能跟动作走。
"""

import os
import pickle

from . import mesh as _mesh
from . import mathutil as mu
from . import unityfs

C_ANIMATOR = 95
C_MATERIAL = 21
C_MESHFILTER = 33
C_MESHRENDERER = 23
C_TEXTURE = 28


def item_bundle(game_dir, pack):
    p = os.path.join(game_dir, "abdata", "h", "h_item", "%s.unity3d" % pack)
    return p if os.path.isfile(p) else None


def list_prefabs(game_dir, pack):
    """这个包里有哪些 prefab（= 顶层 Transform 的名字）。"""
    path = item_bundle(game_dir, pack)
    if not path:
        return []
    b = unityfs.Bundle(path)
    out = []
    for tid in b.roots():
        n = b.nodes[tid]
        if n.children:
            out.append(n.name)
    b.env = None
    return sorted(out)


def _local_matrix(node):
    return mu.trs_to_mat(node.pos, node.rot, node.scale)


def _material_info(bundle, pid):
    """材质 → {name, color, texture}"""
    try:
        tt = bundle.typetree(pid)
    except Exception:
        return None
    name = tt.get("m_Name", "")
    props = tt.get("m_SavedProperties") or {}
    color = (1.0, 1.0, 1.0, 1.0)
    tex_pid = None

    def _pair(item):
        if isinstance(item, dict):
            return item.get("first"), item.get("second")
        try:
            return item[0], item[1]
        except Exception:
            return None, None

    def _keyname(d):
        if isinstance(d, str):
            return d
        if not isinstance(d, dict):
            return ""
        return d.get("name") or d.get("m_Name") or ""

    for it in props.get("m_Colors") or []:
        k, v = _pair(it)
        if _keyname(k) in ("_Color", "_MainColor", "_BaseColor"):
            try:
                c = (float(v.get("r", 1)), float(v.get("g", 1)),
                     float(v.get("b", 1)), float(v.get("a", 1)))
            except Exception:
                continue
            if c[3] <= 0.0:          # 有些 shader 的 _BaseColor 只当遮罩用
                c = (c[0], c[1], c[2], 1.0)
            if color == (1.0, 1.0, 1.0, 1.0) or _keyname(k) == "_Color":
                color = c
    for it in props.get("m_TexEnvs") or []:
        k, v = _pair(it)
        if _keyname(k) in ("_MainTex", "_BaseMap", "_BaseTex"):
            try:
                tex_pid = (v.get("m_Texture") or {}).get("m_PathID")
            except Exception:
                tex_pid = None
            break
    return {"name": name, "color": color, "texture_pid": tex_pid}


def _texture_png(bundle, tex_pid):
    _ensure_texture_support()
    obj = bundle.objects.get(tex_pid)
    if obj is None:
        return None
    try:
        img = obj.read().image
    except Exception:
        return None
    if img is None:
        return None
    import io
    buf = io.BytesIO()
    try:
        img.convert("RGBA").save(buf, format="PNG")
    except Exception:
        return None
    return buf.getvalue()


def _ensure_texture_support():
    """UnityPy 的 export 包里带 AudioClipConverter，它 import 了没打包的
    ``fmod_toolkit`` —— 不 stub 掉的话连 Texture2D.image 都进不去。
    （我们只要贴图，不要音频。）
    """
    import sys
    import types
    if "fmod_toolkit" in sys.modules:
        return
    try:
        import fmod_toolkit  # noqa: F401
        return
    except Exception:
        pass
    mod = types.ModuleType("fmod_toolkit")

    def raw_to_wav(*_a, **_k):
        raise NotImplementedError("HS2 AnimKit 不解音频")

    mod.raw_to_wav = raw_to_wav
    sys.modules["fmod_toolkit"] = mod


def extract(game_dir, pack, prefab, with_textures=False, cache_dir=None):
    """提取一个道具 prefab。

    返回 dict：
        bones:  [{name, parent, pos, rot, scale, path}]
        meshes: [{name, node, verts, normals, uvs, tris, weights, bone_slots,
                  material}]
        materials: [{...}]
    """
    cache_key = None
    path = item_bundle(game_dir, pack)
    if path is None:
        raise KeyError("没有道具包 %s" % pack)
    if cache_dir:
        try:
            st = os.stat(path)
            import hashlib
            raw = "%s|%s|%d|%d|v2" % (path, prefab, st.st_size, st.st_mtime)
            cache_key = "prop_%s.pkl" % hashlib.md5(
                raw.encode("utf-8")).hexdigest()[:12]
            fp = os.path.join(cache_dir, cache_key)
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return pickle.load(f)
        except Exception:
            cache_key = None

    b = unityfs.Bundle(path)
    root_tid = None
    for tid in b.roots():
        if b.nodes[tid].name == prefab:
            root_tid = tid
            break
    if root_tid is None:
        for tid in b.nodes:
            if b.nodes[tid].name == prefab:
                root_tid = tid
                break
    if root_tid is None:
        raise KeyError("道具包里没有 %s" % prefab)

    tids = b.subtree(root_tid)
    index = {tid: i for i, tid in enumerate(tids)}

    # ---- 骨骼 + 每个节点的「到 prefab 根」矩阵 ----
    bones = []
    l2root = {}
    for tid in tids:
        node = b.nodes[tid]
        i = index[tid]
        parent = index.get(node.parent, -1) if node.parent else -1
        lo = _local_matrix(node)
        l2root[tid] = (mu.mat_mul(l2root[node.parent], lo)
                       if parent >= 0 else lo)
        bones.append({
            "name": node.name or ("node_%d" % i),
            "parent": parent,
            "pos": node.pos,
            "rot": node.rot,
            "scale": node.scale,
            "path": b.path_of(tid) if tid != root_tid else "",
        })

    # ---- 材质表 ----
    materials = []

    def mat_index(renderer_tt):
        out = []
        for p in renderer_tt.get("m_Materials") or []:
            mpid = p.get("m_PathID")
            info = _material_info(b, mpid) if mpid else None
            if info is None:
                info = {"name": "", "color": (1, 1, 1, 1), "texture_pid": None}
            info = dict(info)
            if with_textures and info.get("texture_pid"):
                info["png"] = _texture_png(b, info["texture_pid"])
            materials.append(info)
            out.append(len(materials) - 1)
        return out

    # ---- 网格 ----
    meshes = []
    smr_by_node = {}
    filter_by_node = {}
    renderer_by_node = {}
    for pid in list(b.objects):
        obj = b.objects[pid]
        t = unityfs._type_id(obj)
        if t not in (137, C_MESHFILTER, C_MESHRENDERER):
            continue
        try:
            tt = b.typetree(pid)
        except Exception:
            continue
        go = (tt.get("m_GameObject") or {}).get("m_PathID")
        node_tid = b.go2node.get(go)
        if node_tid not in index:
            continue
        if t == 137:
            smr_by_node[node_tid] = tt
        elif t == C_MESHFILTER:
            filter_by_node[node_tid] = tt
        else:
            renderer_by_node[node_tid] = pid

    for node_tid in tids:
        smr = smr_by_node.get(node_tid)
        mf = filter_by_node.get(node_tid)
        tt = smr or mf
        if tt is None:
            continue
        mesh_ptr = (tt.get("m_Mesh") or {}).get("m_PathID")
        if not mesh_ptr or mesh_ptr not in b.objects:
            continue
        mname = ""
        try:
            mname = b.typetree(mesh_ptr).get("m_Name", "")
        except Exception:
            pass
        data = _mesh.extract_by_pid(b, mesh_ptr, mname)
        # 顶点 → prefab 根空间（Blender 单位）
        m = l2root[node_tid]
        data["vertices"] = [_bl(mu.mat_apply(m, v)) for v in data["vertices"]]
        if data.get("normals"):
            import math
            rot = _rot_only(m)
            norms = []
            for n in data["normals"]:
                v = mu.mat_apply(rot, n)
                ln = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
                norms.append(_bl((v[0] / ln, v[1] / ln, v[2] / ln)))
            data["normals"] = norms
        data["flip"] = _mesh.needs_flip({"normals": data.get("normals"),
                                         "vertices": data["vertices"],
                                         "triangles": data["triangles"]})
        # 蒙皮槽位 → 骨骼下标
        slots = []
        if smr:
            for p in smr.get("m_Bones") or []:
                bt = b.nodes.get(p.get("m_PathID"))
                if bt is not None and p.get("m_PathID") in index:
                    slots.append(index[p.get("m_PathID")])
                else:
                    slots.append(-1)
        else:
            slots = [index[node_tid]]
            # 静态网格：所有权重都压到这一个骨上
            data["weights"] = [(0, 0, 0, 0, 1.0, 0.0, 0.0, 0.0)
                               for _ in data["vertices"]]
        mats = []
        if smr:
            mats = mat_index(smr)
        else:
            rpid = renderer_by_node.get(node_tid)
            if rpid is not None:
                try:
                    mats = mat_index(b.typetree(rpid))
                except Exception:
                    mats = []
        meshes.append({
            "name": mname or b.nodes[node_tid].name,
            "node": index[node_tid],
            "verts": data["vertices"],
            "normals": data.get("normals") or [],
            "uvs": data.get("uvs") or [],
            "tris": data["triangles"],
            "weights": data["weights"],
            "bone_slots": slots,
            "materials": mats,
            "flip": data["flip"],
        })

    out = {"prefab": prefab, "pack": pack, "bones": bones,
           "meshes": meshes, "materials": materials}
    b.env = None
    if cache_key:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, cache_key), "wb") as f:
                pickle.dump(out, f, protocol=4)
        except Exception:
            pass
    return out


def _bl(p):
    """Unity 坐标（未乘缩放）→ Blender 坐标。道具和角色用同一把 0.1 的尺。"""
    return mu.to_blender_pos(p, 1.0)


def _rot_only(m):
    return (
        (m[0][0], m[0][1], m[0][2], 0.0),
        (m[1][0], m[1][1], m[1][2], 0.0),
        (m[2][0], m[2][1], m[2][2], 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )


def _unused_renderer_for(bundle, node_tid):
    """MeshFilter 所在 GameObject 上的 MeshRenderer。"""
    node = bundle.nodes.get(node_tid)
    if node is None:
        return None
    go = node.game_object
    for pid, obj in bundle.objects.items():
        if unityfs._type_id(obj) != C_MESHRENDERER:
            continue
        try:
            tt = obj.read_typetree()
        except Exception:
            continue
        if (tt.get("m_GameObject") or {}).get("m_PathID") == go:
            return pid
    return None
