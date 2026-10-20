"""从 oo_base.unity3d 提取默认身体网格（含骨骼权重）。

o_body_cf = 女性默认身体，9594 顶点 / 101 根骨骼 / 单材质。
它的 m_BindPose 与 p_cf_anim 里骨骼的世界矩阵逐点一致（实测误差 < 1e-3），
所以只要用同一份骨架建 armature，网格和骨骼天然对齐 —— 不需要任何手动匹配。
"""

from . import unityfs

BODY_MESH = "o_body_cf"
BODY_MALE_MESH = "o_body_cm"


def _tri_list(handler):
    """UnityPy 的 get_triangles() 可能返回 [tri...] 或按子网格嵌套的 [[tri...],...]。"""
    tris = handler.get_triangles()
    out = []

    def flush(seq):
        for t in seq:
            if isinstance(t[0], (tuple, list)):
                flush(t)
            else:
                out.extend((int(t[0]), int(t[1]), int(t[2])))

    flush(tris)
    return out


def extract(bundle, mesh_name=BODY_MESH, mesh_bones=None):
    """提取网格。mesh_bones 是 SMR 的骨骼路径表（用于权重索引）。"""
    pid = None
    for m in bundle.meshes():
        try:
            if bundle.typetree(m).get("m_Name") == mesh_name:
                pid = m
                break
        except Exception:
            continue
    if pid is None:
        raise KeyError("找不到网格 %r" % mesh_name)
    return extract_by_pid(bundle, pid, mesh_name, mesh_bones)


def extract_by_pid(bundle, pid, mesh_name=BODY_MESH, mesh_bones=None):
    """按 path_id 提取网格（同一个包里可能有多个同名网格，比如四种头型）。"""
    obj = bundle.objects[pid]
    mesh = obj.read()
    from UnityPy.helpers.MeshHelper import MeshHandler

    h = MeshHandler(mesh)
    h.process()

    verts = [(float(v[0]), float(v[1]), float(v[2])) for v in h.m_Vertices]
    normals = []
    if h.m_Normals:
        normals = [(float(v[0]), float(v[1]), float(v[2])) for v in h.m_Normals]
    uvs = []
    if h.m_UV0:
        uvs = [(float(v[0]), float(v[1])) for v in h.m_UV0]
    tris = _tri_list(h)

    bidx = h.m_BoneIndices or []
    bwts = h.m_BoneWeights or []
    weights = []
    for i in range(len(verts)):
        bi = bidx[i] if i < len(bidx) else (0, 0, 0, 0)
        bw = bwts[i] if i < len(bwts) else (0.0, 0.0, 0.0, 0.0)
        weights.append(tuple(int(x) for x in bi) + tuple(float(x) for x in bw))

    tt = bundle.typetree(pid)
    bindpose = []
    for m in tt.get("m_BindPose", []):
        bindpose.append([float(m.get("e%d%d" % (r, c), 0.0)) for r in range(4)
                         for c in range(4)])

    return {
        "name": mesh_name,
        "vertices": verts,
        "normals": normals,
        "uvs": uvs,
        "triangles": tris,
        "weights": weights,
        "bones": list(mesh_bones or []),
        "bindpose": bindpose,
        "submeshes": [(int(s.get("firstByte", 0)), int(s.get("indexCount", 0)))
                      for s in tt.get("m_SubMeshes", [])],
    }


def needs_flip(data):
    """判断三角形绕序是否与顶点法线相反（Unity 左手系 -> Blender 需翻转）。"""
    if not data.get("normals"):
        return True
    verts = data["vertices"]
    normals = data["normals"]
    tris = data["triangles"]
    score = 0.0
    for i in range(0, min(len(tris), 3000), 3):
        a, b, c = tris[i], tris[i + 1], tris[i + 2]
        pa, pb, pc = verts[a], verts[b], verts[c]
        u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        n = (u[1] * v[2] - u[2] * v[1],
             u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0])
        avg = ((normals[a][0] + normals[b][0] + normals[c][0]) / 3.0,
               (normals[a][1] + normals[b][1] + normals[c][1]) / 3.0,
               (normals[a][2] + normals[b][2] + normals[c][2]) / 3.0)
        score += n[0] * avg[0] + n[1] * avg[1] + n[2] * avg[2]
    return score < 0.0
