"""从 oo_base.unity3d 提取「默认骨架」。

p_cf_anim 是游戏里角色动画的根节点（玩家口中的 pf_anim），
它下面挂着完完整整的一副骨架：602 个节点，包含身体骨、手指、胸、
裙摆链、发骨、碰撞骨等。这些节点的局部 TRS 就是「没有任何捏人改动」
的默认状态 —— 正好是我们要的基准。
"""

from . import unityfs

ANIM_ROOT = "p_cf_anim"
BODY_ROOT = "p_cf_body_00"


class Bone(object):
    __slots__ = ("name", "path", "parent", "pos", "rot", "scale", "children",
                 "depth")

    def __init__(self, name, path, parent, pos, rot, scale, depth):
        self.name = name
        self.path = path
        self.parent = parent
        self.pos = pos
        self.rot = rot
        self.scale = scale
        self.children = []
        self.depth = depth

    def to_dict(self):
        return {
            "name": self.name,
            "path": self.path,
            "parent": self.parent,
            "pos": [round(v, 6) for v in self.pos],
            "rot": [round(v, 6) for v in self.rot],
            "scale": [round(v, 6) for v in self.scale],
        }


class Skeleton(object):
    def __init__(self, root_name, bones):
        self.root_name = root_name
        self.bones = bones
        self.by_path = {b.path: i for i, b in enumerate(bones)}
        self.by_name = {}
        for i, b in enumerate(bones):
            self.by_name.setdefault(b.name, []).append(i)

    def __len__(self):
        return len(self.bones)

    def index_of(self, path):
        if not path:
            return None
        p = str(path).replace("\\", "/")
        # 衣服 prefab 有时会把骨架挂在自己的模型根下（c_00/cf_J_Root/...），
        # 默认骨架是从 cf_J_Root 开始的，先剥掉模型根前缀再查。
        i = p.find("cf_J_Root/")
        if i >= 0:
            p = p[i:]
        elif p.startswith("c_00/"):
            p = p[5:]
        idx = self.by_path.get(p)
        if idx is not None:
            return idx
        # 少数旧裙摆层级（cf_J_LegUp01_R/...）只能按唯一叶子名对。
        leaf = p.rsplit("/", 1)[-1]
        matches = self.by_name.get(leaf) or []
        if len(matches) == 1:
            return matches[0]
        return None

    def duplicates(self):
        return {n: v for n, v in self.by_name.items() if len(v) > 1}

    def to_dict(self):
        return {
            "format": "hs2-skeleton",
            "version": 1,
            "root": self.root_name,
            "bones": [b.to_dict() for b in self.bones],
        }

    @classmethod
    def from_dict(cls, data):
        """从缓存重建（配合 to_dict）。"""
        bones = []
        for i, d in enumerate(data["bones"]):
            b = Bone(d["name"], d["path"], d["parent"],
                     tuple(d["pos"]), tuple(d["rot"]), tuple(d["scale"]), 0)
            if b.parent >= 0:
                bones[b.parent].children.append(i)
            bones.append(b)
        return cls(data.get("root", ""), bones)


def extract(bundle_path, root_name=ANIM_ROOT):
    b = unityfs.Bundle(bundle_path)
    root = b.find_root(root_name)
    tids = b.subtree(root)
    bones = []
    index_of = {}
    for tid in tids:
        node = b.nodes[tid]
        depth = 0
        cur = tid
        while b.nodes[cur].parent:
            depth += 1
            cur = b.nodes[cur].parent
        parent = index_of.get(node.parent, -1)
        bone = Bone(node.name, b.path_of(tid), parent, node.pos, node.rot,
                    node.scale, depth)
        index_of[tid] = len(bones)
        if parent >= 0:
            bones[parent].children.append(len(bones))
        bones.append(bone)
    return Skeleton(root_name, bones), b


def extract_mesh_bones(bundle, root_name, mesh_name):
    """返回 (SMR path_id, [骨骼路径...])，路径相对 root_name。"""
    smr = bundle.smr_by_mesh_name(mesh_name)
    if smr is None:
        raise KeyError("找不到网格 %r" % mesh_name)
    tt = bundle.typetree(smr)
    paths = []
    for ptr in tt["m_Bones"]:
        tid = ptr["m_PathID"]
        paths.append(bundle.path_of(tid) if tid in bundle.nodes else "")
    return smr, paths


def world_matrix(skel, index, _cache=None):
    """Unity 空间的骨骼世界矩阵（局部 TRS 逐级累乘）。"""
    cache = {} if _cache is None else _cache
    if index in cache:
        return cache[index]
    b = skel.bones[index]
    from . import mathutil as mu
    local = mu.trs_to_mat(b.pos, b.rot, b.scale)
    if b.parent >= 0:
        m = mu.mat_mul(world_matrix(skel, b.parent, cache), local)
    else:
        m = local
    cache[index] = m
    return m
