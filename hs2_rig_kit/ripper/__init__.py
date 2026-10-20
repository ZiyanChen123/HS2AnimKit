"""HS2 资源解包器（纯 Python，可脱离 Blender 单独使用）。

对外只需要 :class:`GameAssets`。
"""

import json
import os
import pickle
import time

from . import clips as _clips
from . import hashes as _hashes
from . import head as _head
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

    def body_mesh(self):
        def build():
            skel = self.skeleton()
            smr, bone_paths = _skel.extract_mesh_bones(
                self.base, _skel.BODY_ROOT, _mesh.BODY_MESH)
            data = _mesh.extract(self.base, _mesh.BODY_MESH, bone_paths)
            # SMR 的骨骼顺序 -> 骨架下标
            bone_index = []
            for p in bone_paths:
                idx = skel.index_of(p)
                bone_index.append(idx if idx is not None else -1)
            data["bone_index"] = bone_index
            data["flip"] = _mesh.needs_flip(data)
            return data
        return self._cache_get("body", [self.base_path], build)

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
        """{动作名: {"files": [...], "fit": 0~1, "bones": n}}，第一次扫描后缓存。

        ``fit`` 表示这个动作的骨骼绑定有多少能对上当前骨架 —— 用来滤掉
        「不是给这个骨架的」动作（男角/道具/椅子/双人动作）。
        """
        if self._index is not None and not rebuild:
            return self._index
        p = self.index_path()
        if os.path.isfile(p) and not rebuild:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    idx = json.load(f)
                if idx.get("game_dir") == self.game_dir and idx.get("version") == 2:
                    self._index = idx["clips"]
                    return self._index
            except Exception:
                pass

        # 骨架的路径哈希表：判断一个动作是不是给这副骨架用的
        lookup = {}
        try:
            lookup = _hashes.build_lookup(self.skeleton().by_path.keys())
        except Exception:
            pass

        idx = {}
        bundles = _unityfs.anime_bundles(self.game_dir)
        for i, path in enumerate(bundles):
            fname = os.path.basename(path)
            if progress:
                progress(i, len(bundles), fname)
            b = _unityfs.Bundle(path)
            for pid in b.clips():
                try:
                    tt = b.typetree(pid)
                except Exception:
                    continue
                n = tt.get("m_Name", "")
                if not n:
                    continue
                fit = _clips.clip_fit(tt, lookup) if lookup else 1.0
                e = idx.get(n)
                if e is None:
                    idx[n] = {"files": [fname], "fit": round(fit, 3)}
                elif fname not in e["files"]:
                    e["files"].append(fname)
                    if fit > e["fit"]:
                        e["fit"] = round(fit, 3)
            b.env = None
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"game_dir": self.game_dir, "version": 2,
                           "clips": idx}, f)
        except Exception:
            pass
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
        bundle = self._bundle_for(clip_name)
        tt = _clips.find_clip(bundle, clip_name)
        if tt is None:
            raise KeyError("包里找不到动作 %r" % clip_name)
        skel = self.skeleton()
        lookup = _hashes.build_lookup(skel.by_path.keys())
        return _clips.decode(tt, lookup, fps=fps)
