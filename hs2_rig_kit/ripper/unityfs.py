"""AssetBundle 读取层：把 UnityPy 的对象整理成好用的树。

只依赖 UnityPy，不依赖 Blender，方便单独跑命令行。
"""

import os

C_GO = 1
C_TRANSFORM = 4
C_MESH = 43
C_CLIP = 74
C_ANIMATOR = 95
C_SMR = 137


def _v3(v):
    return (float(v.x), float(v.y), float(v.z))


def _v4(v):
    return (float(v.x), float(v.y), float(v.z), float(v.w))


class Node(object):
    """一个 Transform 节点。"""

    __slots__ = ("tid", "name", "parent", "children", "pos", "rot", "scale",
                 "game_object", "_path")

    def __init__(self, tid, name, parent, children, pos, rot, scale, go):
        self.tid = tid
        self.name = name
        self.parent = parent
        self.children = children
        self.pos = pos
        self.rot = rot
        self.scale = scale
        self.game_object = go
        self._path = None


class Bundle(object):
    """一个 .unity3d 文件。"""

    def __init__(self, path, env=None):
        import UnityPy
        self.path = path
        self.env = env if env is not None else UnityPy.load(path)
        self._objs = {}
        self._nodes = {}
        self._names = {}
        self._build()

    # ---------------- 基础 ----------------
    def _build(self):
        self.objects = {}
        for obj in self.env.objects:
            self.objects[obj.path_id] = obj

        self.go2name = {}
        for pid, obj in self.objects.items():
            if _type_id(obj) == C_GO:
                try:
                    self.go2name[pid] = obj.read().m_Name
                except Exception:
                    self.go2name[pid] = ""

        nodes = {}
        for pid, obj in self.objects.items():
            if _type_id(obj) != C_TRANSFORM:
                continue
            try:
                d = obj.read()
            except Exception:
                continue
            go = d.m_GameObject.path_id
            children = [c.path_id for c in d.m_Children]
            nodes[pid] = Node(
                pid,
                self.go2name.get(go, ""),
                d.m_Father.path_id if d.m_Father.path_id else 0,
                children,
                _v3(d.m_LocalPosition),
                _v4(d.m_LocalRotation),
                _v3(d.m_LocalScale),
                go,
            )
        self.nodes = nodes
        self.go2node = {n.game_object: n.tid for n in nodes.values()}

    def name_of(self, tid):
        n = self.nodes.get(tid)
        return n.name if n else ""

    def roots(self):
        return [tid for tid, n in self.nodes.items() if not n.parent]

    def find_root(self, name):
        for tid in self.roots():
            if self.nodes[tid].name == name:
                return tid
        raise KeyError("找不到根节点 %r" % name)

    def path_of(self, tid):
        """相对子树根的路径，例如 cf_J_Root/cf_N_height/cf_J_Hips。"""
        node = self.nodes[tid]
        if node._path is not None:
            return node._path
        parts = []
        cur = tid
        while cur and self.nodes[cur].parent:
            parts.append(self.nodes[cur].name)
            cur = self.nodes[cur].parent
        path = "/".join(reversed(parts))
        node._path = path
        return path

    def subtree(self, root_tid):
        """深度优先返回子树里的 tid（父在前）。"""
        out = []
        stack = [root_tid]
        while stack:
            tid = stack.pop()
            out.append(tid)
            stack.extend(reversed(self.nodes[tid].children))
        return out

    # ---------------- 类型查询 ----------------
    def typetree(self, pid):
        return self.objects[pid].read_typetree()

    def smrs(self):
        return [pid for pid, o in self.objects.items() if _type_id(o) == C_SMR]

    def meshes(self):
        return [pid for pid, o in self.objects.items() if _type_id(o) == C_MESH]

    def clips(self):
        return [pid for pid, o in self.objects.items() if _type_id(o) == C_CLIP]

    def smr_by_mesh_name(self, mesh_name):
        """按 Mesh 名找 SkinnedMeshRenderer 的 path_id。"""
        for pid in self.smrs():
            try:
                tt = self.typetree(pid)
            except Exception:
                continue
            mesh = self.objects.get(tt["m_Mesh"]["m_PathID"])
            if mesh is None:
                continue
            try:
                if self.typetree(mesh.path_id).get("m_Name") == mesh_name:
                    return pid
            except Exception:
                continue
        return None


def _type_id(obj):
    t = obj.type
    try:
        return int(t)
    except (TypeError, ValueError):
        return int(getattr(t, "value", -1))


# --------------------------------------------------------------------------
# 游戏目录
# --------------------------------------------------------------------------

ANIME_DIR = os.path.join("abdata", "studio", "anime")
BASE_BUNDLE = os.path.join("abdata", "chara", "oo_base.unity3d")


def natural_key(s):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def anime_bundles(game_dir):
    """列出所有 studio/anime/*.unity3d（按文件名排序，oo_base 最后）。"""
    d = os.path.join(game_dir, ANIME_DIR)
    if not os.path.isdir(d):
        return []
    names = [f for f in os.listdir(d) if f.lower().endswith(".unity3d")]
    names.sort(key=natural_key)
    names.sort(key=lambda f: f.startswith("oo_base"))
    return [os.path.join(d, f) for f in names]
