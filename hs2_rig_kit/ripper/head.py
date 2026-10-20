"""头部：骨骼树 + 皮肤网格。

HS2 的头部是单独的资源包（``abdata/chara/00/fo_head_00.unity3d``），
里面是 ``p_cf_head_00`` / ``p_cf_head_01`` 两套头型，以及配套的眼睛/牙齿/睫毛。

身体包 ``oo_base.unity3d`` 里有一份 ``p_cf_head_bone``，就是头部骨骼树的模板
（92 个节点，其中 43 根被头部网格蒙皮引用）。游戏运行时把头 prefab 挂在
``cf_J_Head`` 下面、局部变换为单位矩阵，所以：

    面部骨的世界矩阵 = cf_J_Head 的世界矩阵 · 头部骨骼树的局部矩阵

而头部网格的 ``m_BindPose`` 是**相对头 prefab 根**的（实测逐点吻合到 1e-3），
所以顶点必须先乘上 cf_J_Head 的世界矩阵，才能和身体网格落在同一个空间。

这里只取 ``o_head``（皮肤）。眼睛、舌头、睫毛、牙齿都不要——素模够用了。
"""

from . import mathutil as mu
from . import mesh as _mesh
from . import unityfs

HEAD_BONE_ROOT = "p_cf_head_bone"
HEAD_PREFAB = "p_cf_head_00"
HEAD_MESH = "o_head"

# 需要跳过的容器节点（没有蒙皮意义）
_SKIP_PREFIX = ("p_cf_head",)


def bones(base_bundle):
    """头部骨骼树。

    返回 [{'name','parent','world','deform'}]，world 是**头部局部空间**（相对
    cf_J_Head）的矩阵，调用方再乘上 cf_J_Head 的世界矩阵。
    """
    root_tid = base_bundle.find_root(HEAD_BONE_ROOT)
    tids = base_bundle.subtree(root_tid)

    out = []
    world = {}
    seen = {}
    for tid in tids:
        node = base_bundle.nodes[tid]
        local = mu.trs_to_mat(node.pos, node.rot, node.scale)
        pw = world.get(node.parent, mu.IDENTITY)
        w = mu.mat_mul(pw, local)
        world[tid] = w

        if tid == root_tid:
            continue
        name = node.name or "face"
        base = name
        if name in seen:
            seen[name] += 1
            name = "%s_%03d" % (name, seen[name])
        else:
            seen[name] = 0

        if node.parent == root_tid:
            parent = "cf_J_Head"   # 顶层：直接挂到 cf_J_Head（游戏里也是这么挂的）
        else:
            parent = base_bundle.nodes[node.parent].name
        out.append({"name": name, "parent": parent, "world": w, "deform": True})
    return out


def _chain_names(bundle, tid):
    out = []
    cur = tid
    while cur:
        out.append(bundle.nodes[cur].name)
        cur = bundle.nodes[cur].parent
    return out


def mesh(head_bundle, prefab=HEAD_PREFAB, mesh_name=HEAD_MESH):
    """取出某个头 prefab 下的皮肤网格（含权重和骨骼路径）。"""
    smr = None
    for pid in head_bundle.smrs():
        try:
            tt = head_bundle.typetree(pid)
            mid = tt["m_Mesh"]["m_PathID"]
            if head_bundle.typetree(mid).get("m_Name") != mesh_name:
                continue
            go = tt["m_GameObject"]["m_PathID"]
            node = head_bundle.go2node.get(go)
            if node is None:
                continue
            if prefab in _chain_names(head_bundle, node):
                smr = pid
                break
        except Exception:
            continue
    if smr is None:
        raise KeyError("头部包里找不到 %s/%s" % (prefab, mesh_name))

    tt = head_bundle.typetree(smr)
    mid = tt["m_Mesh"]["m_PathID"]
    paths = [head_bundle.path_of(p["m_PathID"]) for p in tt["m_Bones"]]
    data = _mesh.extract_by_pid(head_bundle, mid, mesh_name, paths)

    # SMR 的骨骼路径 -> 骨骼名（路径最后一段）
    names = [p.split("/")[-1] for p in paths]
    data["bone_names"] = names
    data["bone_index"] = list(range(len(names)))
    data["flip"] = _mesh.needs_flip(data)
    return data


def load(game_dir, attach, base_bundle=None, bundle_name="fo_head_00",
         prefab=HEAD_PREFAB):
    """一次性拿到头部骨骼 + 网格 + 挂载矩阵。

    ``attach`` 是 cf_J_Head 在骨架空间的世界矩阵（Unity 空间，已含缩放），
    这里把所有骨骼世界矩阵都乘上它，返回的就是骨架空间坐标。
    """
    import os
    if base_bundle is None:
        base_bundle = unityfs.Bundle(os.path.join(
            game_dir, "abdata", "chara", "oo_base.unity3d"))
    path = os.path.join(game_dir, "abdata", "chara", "00",
                        bundle_name + ".unity3d")
    hb = unityfs.Bundle(path)

    bs = bones(base_bundle)
    for b in bs:
        b["world"] = mu.mat_mul(attach, b["world"])
    return {
        "bones": bs,
        "mesh": mesh(hb, prefab=prefab),
        "attach": attach,
        "source": path,
    }
