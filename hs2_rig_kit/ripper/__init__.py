"""HS2 资源解包器（纯 Python，可脱离 Blender 单独使用）。

对外只需要 :class:`GameAssets`。
"""

import json
import os
import pickle
import time

from . import catalog as _catalog
from . import clips as _clips
from . import hashes as _hashes
from . import head as _head
from . import hdb as _hdb
from . import mesh as _mesh
from . import skeleton as _skel
from . import unityfs as _unityfs

__all__ = ["GameAssets", "ClipData", "open_bundle"]

open_bundle = _unityfs.Bundle

CACHE_VERSION = 4


class GameAssets(object):
    """绑定一个 HS2 游戏目录。"""

    def __init__(self, game_dir, cache_dir=None):
        self.game_dir = os.path.abspath(game_dir)
        self.cache_dir = cache_dir or os.path.join(
            os.path.expanduser("~"), ".hs2_animkit")
        self._base = None
        self._skel = None
        self._index = None
        self._head = None
        self._catalog = None
        self._memo = {}

    # ---------------- 磁盘缓存 ----------------
    # 解包 oo_base / 头部包要 7 秒，其中绝大部分是 UnityPy 读对象树。
    # 解析结果本身是纯 Python 数据，直接 pickle 下来，第二次起 <0.3 秒。

    def _cache_key(self, tag, paths):
        stamps = []
        for p in paths:
            try:
                st = os.stat(p)
                stamps.append("%s:%d:%.0f" % (os.path.basename(p), st.st_size,
                                              st.st_mtime))
            except OSError:
                stamps.append("%s:?" % p)
        raw = "|".join(stamps) + "|v%d" % CACHE_VERSION
        import hashlib
        return "%s_%s.pkl" % (tag, hashlib.md5(raw.encode("utf-8")).hexdigest()[:12])

    def _cache_get(self, tag, paths, builder):
        key = self._cache_key(tag, paths)
        if key in self._memo:
            return self._memo[key]
        path = os.path.join(self.cache_dir, key)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
                self._memo[key] = data
                return data
            except Exception:
                pass
        t0 = time.time()
        data = builder()
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump(data, f, protocol=4)
            os.replace(tmp, path)
        except Exception:
            pass
        self._memo[key] = data
        return data

    # ---------------- 基础数据 ----------------
    @property
    def base_path(self):
        return os.path.join(self.game_dir, _unityfs.BASE_BUNDLE)

    def check(self):
        if not os.path.isfile(self.base_path):
            raise RuntimeError(
                "这里不像是 HS2 根目录：找不到 abdata/chara/oo_base.unity3d\n"
                "当前：%s" % self.game_dir)
        return True

    @property
    def base(self):
        if self._base is None:
            self.check()
            self._base = _unityfs.Bundle(self.base_path)
        return self._base

    def skeleton(self):
        if self._skel is None:
            self.check()
            raw = self._cache_get("skel", [self.base_path],
                                  lambda: _skel.extract(self.base_path)[0].to_dict())
            self._skel = _skel.Skeleton.from_dict(raw)
        return self._skel

    def body_mesh(self, mesh_name=None):
        mesh_name = mesh_name or _mesh.BODY_MESH

        def build():
            skel = self.skeleton()
            smr, bone_paths = _skel.extract_mesh_bones(
                self.base, _skel.BODY_ROOT, mesh_name)
            data = _mesh.extract(self.base, mesh_name, bone_paths)
            # SMR 的骨骼顺序 -> 骨架下标
            bone_index = []
            for p in bone_paths:
                idx = skel.index_of(p)
                bone_index.append(idx if idx is not None else -1)
            data["bone_index"] = bone_index
            data["flip"] = _mesh.needs_flip(data)
            return data
        return self._cache_get("body_" + mesh_name, [self.base_path], build)

    # ---------------- 头部 ----------------
    def head(self, bundle_name="fo_head_00", prefab="p_cf_head_00"):
        """头部骨骼 + 皮肤网格。骨骼世界矩阵已乘上 cf_J_Head（骨架空间）。"""
        key = (bundle_name, prefab)
        if self._head is None:
            self._head = {}
        if key in self._head:
            return self._head[key]

        skel = self.skeleton()
        idxs = skel.by_name.get("cf_J_Head") or []
        if not idxs:
            raise KeyError("骨架里没有 cf_J_Head")
        attach = _skel.world_matrix(skel, idxs[0])
        head_path = os.path.join(self.game_dir, "abdata", "chara", "00",
                                 bundle_name + ".unity3d")

        def build():
            return _head.load(self.game_dir, attach, base_bundle=self.base,
                              bundle_name=bundle_name, prefab=prefab)

        data = self._cache_get("head_%s_%s" % (bundle_name, prefab),
                               [self.base_path, head_path], build)
        self._head[key] = data
        return data

    # ---------------- 动作 ----------------
    def index_path(self):
        return os.path.join(self.cache_dir, "clip_index.json")

    def cached_index(self):
        """只读缓存，不触发扫描（UI 用）。"""
        if self._index is not None:
            return self._index
        p = self.index_path()
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                idx = json.load(f)
            if idx.get("game_dir") == self.game_dir:
                self._index = idx["clips"]
                return self._index
        except Exception:
            return None
        return None

    def clip_index(self, rebuild=False, progress=None):
        """{唯一键: {"files": [...], "fit": 0~1, "category": ...}}。

        ``fit`` 表示这个动作的骨骼绑定有多少能对上当前骨架 —— 用来滤掉
        「不是给这个骨架的」动作（男角/道具/椅子/双人动作）。
        ``category`` 来自官方的分组表（见 ripper/studio.py）。

        **键不是 clip 名** —— 游戏里 clip 名会重名（34 个武器控制器各有一份
        ``attack_00``），所以键是「clip 名」或「clip 名 @ 控制器」。
        """
        if self._index is not None and not rebuild:
            return self._index
        cat = self.catalog(rebuild=rebuild, progress=progress)
        idx = {}
        for n, e in (cat.get("studio") or {}).items():
            idx[n] = {
                "files": e.get("files") or [],
                "fit": e.get("fit", 1.0),
                "category": e.get("category", ""),
                "top": e.get("top", ""),
                "top_cn": e.get("top_cn", ""),
                "group": e.get("group", ""),
                "group_cn": e.get("group_cn", ""),
                "bundle": e.get("bundle"),
                "pid": e.get("pid"),
                "name": e.get("name") or n,
                "clip": e.get("clip") or n,
                "ctrl": e.get("ctrl") or "",
                "official": bool(e.get("official")),
                "item": e.get("item") or {},
            }
        self._index = idx
        return idx

    def _bundle_for(self, clip_name):
        idx = self.clip_index()
        e = idx.get(clip_name)
        files = e.get("files") if isinstance(e, dict) else e
        if not files:
            raise KeyError("游戏里没有叫 %r 的动作" % clip_name)
        path = os.path.join(self.game_dir, _unityfs.ANIME_DIR, files[0])
        return _unityfs.Bundle(path)

    def load_clip(self, clip_name, fps=None):
        """按唯一键（或 clip 名）装一条工作室动作。

        唯一键带 pid 时直接按 path_id 取 —— 名字重名时这是唯一靠得住的办法。
        """
        idx = self.clip_index()
        e = idx.get(clip_name) or {}
        if e.get("pid") is not None and e.get("bundle"):
            return self.load_ref(e["bundle"], e["pid"], fps=fps,
                                 name=e.get("clip") or clip_name)
        bundle = self._bundle_for(clip_name)
        tt = _clips.find_clip(bundle, clip_name)
        if tt is None:
            raise KeyError("包里找不到动作 %r" % clip_name)
        return self.load_typetree(tt, fps=fps, name=clip_name)

    # ---------------- 统一目录（Studio + H） ----------------

    def catalog_path(self):
        return os.path.join(self.cache_dir, "catalog_v%d.json" % _catalog.CACHE_VERSION)

    def catalog(self, rebuild=False, progress=None, with_h=True):
        """Studio 动作 + H 动作 + 分类，第一次扫描后写缓存。"""
        if self._catalog is not None and not rebuild:
            return self._catalog
        p = self.catalog_path()
        if os.path.isfile(p) and not rebuild:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    cat = json.load(f)
                if (cat.get("game_dir") == self.game_dir
                        and cat.get("version") == _catalog.CACHE_VERSION
                        and (cat.get("h") or not with_h)):
                    self._catalog = cat
                    return cat
            except Exception:
                pass

        skel_paths = None
        try:
            skel_paths = self.skeleton().by_path.keys()
        except Exception:
            pass
        cat = _catalog.build(self.game_dir, progress=progress,
                             with_h=with_h, skeleton_paths=skel_paths)
        cat["game_dir"] = self.game_dir
        cat["version"] = _catalog.CACHE_VERSION
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cat, f, ensure_ascii=False)
            os.replace(tmp, p)
            # 清掉旧版本的目录缓存（改格式会换文件名，不清就攒一堆）
            import glob
            keep = os.path.basename(p)
            for old in glob.glob(os.path.join(self.cache_dir, "catalog_v*.json")):
                if os.path.basename(old) != keep:
                    try:
                        os.remove(old)
                    except OSError:
                        pass
        except Exception:
            pass
        self._catalog = cat
        return cat

    def h_data(self, rebuild=False, progress=None):
        return self.catalog(rebuild=rebuild, progress=progress).get("h") or {}

    # ---------------- 按目录条目加载动作 ----------------

    def open_anime(self, fname):
        """打开 studio/anime/<fname>（带一层内存缓存，同一个包不重复解析）。"""
        return self.open_file(os.path.join(self.game_dir,
                                           _unityfs.ANIME_DIR, fname))

    def open_file(self, path):
        """打开任意包（绝对路径 / 相对游戏目录 / 光秃秃的 anime 包名）。

        目录里记的 Studio 动作只存 ``38.unity3d`` 这种包名，所以没路径分隔符
        时按 studio/anime 处理。
        """
        if not os.path.isabs(path) and os.sep not in path and "/" not in path:
            path = os.path.join(self.game_dir, _unityfs.ANIME_DIR, path)
        elif not os.path.isabs(path):
            path = os.path.join(self.game_dir, path)
        path = os.path.abspath(path)
        b = self._memo.get(("file", path))
        if b is None:
            b = _unityfs.Bundle(path)
            self._memo[("file", path)] = b
        return b

    def load_ref(self, bundle_path, pid, fps=None, name=None, paths=None):
        """按「包 + path_id」加载动作。

        H 动作包里同名 clip 一大堆（每个控制器都有 S_Idle），只靠名字根本
        找不到，必须用 path_id。``paths`` 可以换一套「路径表」，道具动画就是
        用它自己的骨骼路径来解。
        """
        b = self.open_file(bundle_path)
        tt = b.typetree(pid)
        if name is None:
            name = tt.get("m_Name") or "clip"
        return self.load_typetree(tt, fps=fps, name=name, paths=paths)

    def load_typetree(self, tt, fps=None, name=None, paths=None):
        if paths is None:
            skel = self.skeleton()
            paths = skel.by_path.keys()
        lookup = _hashes.build_lookup(paths)
        clip = _clips.decode(tt, lookup, fps=fps)
        if name:
            clip.name = name
        return clip

    def load_catalog_entry(self, entry, fps=None):
        """目录里的 studio 条目（或 {bundle, pid} 字典）→ ClipData。"""
        if isinstance(entry, str):
            entry = self.catalog()["studio"].get(entry) or {}
        bundle = entry.get("bundle")
        pid = entry.get("pid")
        if bundle is None and entry.get("refs"):
            bundle, pid = entry["refs"][0]
        if pid is None:
            return self.load_clip(entry.get("name") or "")
        return self.load_ref(bundle, pid, fps=fps, name=entry.get("name"))
