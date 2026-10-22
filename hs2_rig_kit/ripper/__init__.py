"""HS2 资源解包器（纯 Python，可脱离 Blender 单独使用）。

对外只需要 :class:`GameAssets`。
"""

import json
import os
import pickle
import time

from . import catalog as _catalog
from . import clothes as _clothes
from . import clips as _clips
from . import hashes as _hashes
from . import head as _head
from . import hdb as _hdb
from . import mesh as _mesh
from . import skeleton as _skel
from . import unityfs as _unityfs
from .. import jpcn as _jpcn

__all__ = ["GameAssets", "ClipData", "open_bundle"]

open_bundle = _unityfs.Bundle

CACHE_VERSION = 4
CLOTHES_CACHE_VERSION = 1


def _bind_translation(data, skel):
    """网格 bindpose 与骨架静止姿态之间的纯平移差。

    ``cm_o_dan00`` 的 bindpose 比 ``p_cf_anim`` 的静止骨架高 0.2724（Unity
    单位），直接按骨架建网格会让阴茎整体偏高、和睾丸对不上。这里只处理
    纯平移；有旋转/缩放的网格不猜。
    """
    from . import mathutil as mu
    bp = data.get("bindpose") or []
    bones = data.get("bones") or []
    if not bp or not bones:
        return None
    offset = None
    for i, path in enumerate(bones):
        if i >= len(bp):
            return None
        idx = skel.index_of(path)
        if idx is None:
            return None
        flat = bp[i]
        mat = [[flat[r * 4 + c] for c in range(4)] for r in range(4)]
        cur = mu.mat_mul(_skel.world_matrix(skel, idx), mat)
        for r in range(3):
            for c in range(3):
                want = 1.0 if r == c else 0.0
                if abs(cur[r][c] - want) > 1e-3:
                    return None
        trans = (cur[0][3], cur[1][3], cur[2][3])
        if offset is None:
            offset = trans
        elif max(abs(trans[k] - offset[k]) for k in range(3)) > 1e-4:
            return None
    return offset


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
        self._clothes = None
        self._genital = None
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
                        and (cat.get("h") or not with_h
                             or cat.get("h_scanned"))):
                    # 旧缓存没有中文名字段，载入时就地补齐，不用重扫
                    _jpcn.localize_modes((cat.get("h") or {}).get("modes"))
                    self._catalog = cat
                    return cat
            except Exception as e:
                # 别把异常吞掉：这里出错会导致每次启动都重扫几十秒
                print("[HS2] 目录缓存读取失败，将重建：%s" % e)

        skel_paths = None
        try:
            skel_paths = self.skeleton().by_path.keys()
        except Exception:
            pass
        cat = _catalog.build(self.game_dir, progress=progress,
                             with_h=with_h, skeleton_paths=skel_paths)
        cat["game_dir"] = self.game_dir
        cat["version"] = _catalog.CACHE_VERSION
        cat["h_scanned"] = bool(with_h)
        # 空目录结果不写缓存：扫描失败（比如 python 版本不对）时不能拿空表
        # 把上一次的好缓存盖掉，否则用户会莫名少掉整个动作库。
        if not cat.get("studio"):
            print("[HS2] 目录扫描没读到 Studio 动作，这次不写缓存")
        else:
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
            except Exception as e:
                print("[HS2] 目录缓存写入失败：%s" % e)
        self._catalog = cat
        return cat

    def h_data(self, rebuild=False, progress=None):
        return self.catalog(rebuild=rebuild, progress=progress).get("h") or {}

    # ---------------- 男性生殖器（H 场景用） ----------------

    def male_genital(self):
        """oo_base 里的 cm_o_dan00 / cm_o_dan_f（带骨骼下标）。

        同名网格在 prefab 里有 female silhouette / male silhouette / 身体本体
        几份拷贝，骨骼路径虽然一样，但 SMR root/bind 不同。优先取
        ``p_cm_body_00`` 身体本体下、不在 silhouette 里的那一份。
        """
        if self._genital is not None:
            return self._genital
        self.check()
        b = self.base
        skel = self.skeleton()
        out = []
        for mesh_name in ("cm_o_dan00", "cm_o_dan_f"):
            best = None
            best_score = -999
            for smr in b.smrs():
                tt = b.typetree(smr)
                mid = tt.get("m_Mesh", {}).get("m_PathID")
                if mid not in b.objects:
                    continue
                try:
                    if b.typetree(mid).get("m_Name") != mesh_name:
                        continue
                except Exception:
                    continue
                go = tt.get("m_GameObject", {}).get("m_PathID")
                nt = b.go2node.get(go)
                path = b.path_of(nt) if nt else ""
                score = 0
                if path.startswith("n_o_root/n_body_base/N_mnpbset/N_dan/"):
                    score += 100
                if "o_silhouette_" in path:
                    score -= 50
                if "/N_dan/" in path and "silhouette" not in path:
                    score += 10
                if nt is not None:
                    n = b.nodes[nt]
                    # 身体本体那份一般没有额外偏移
                    if abs(n.pos[0]) + abs(n.pos[1]) + abs(n.pos[2]) < 0.01:
                        score += 5
                if score > best_score:
                    best_score = score
                    best = smr
            if best is None:
                print("[HS2] 找不到男性生殖器网格 %s" % mesh_name)
                continue
            try:
                tt = b.typetree(best)
                paths = []
                for ptr in tt.get("m_Bones") or []:
                    tid = ptr.get("m_PathID")
                    paths.append(b.path_of(tid) if tid in b.nodes else "")
                mid = tt["m_Mesh"]["m_PathID"]
                data = _mesh.extract_by_pid(b, mid, mesh_name, paths)
                off = _bind_translation(data, skel)
                if off:
                    data["vertices"] = [
                        (v[0] + off[0], v[1] + off[1], v[2] + off[2])
                        for v in data["vertices"]
                    ]
                data["bone_index"] = [
                    skel.index_of(p) if skel.index_of(p) is not None else -1
                    for p in paths
                ]
                mats = []
                for mp in tt.get("m_Materials") or []:
                    pid = mp.get("m_PathID")
                    if pid in b.objects:
                        mats.append(b.typetree(pid).get("m_Name", ""))
                out.append({"name": mesh_name, "mesh": data,
                            "materials": mats})
            except Exception as e:
                print("[HS2] 男性生殖器网格 %s 读取失败：%s" % (mesh_name, e))
        self._genital = out
        return out

    # ---------------- 衣服目录 ----------------

    def clothes_catalog_path(self):
        return os.path.join(
            self.cache_dir, "clothes_v%d.json" % CLOTHES_CACHE_VERSION)

    def _clothes_stamp(self):
        """characustom 目录的文件数 + 最新修改时间，用来发现新增/更新的 Mod。"""
        root = os.path.join(self.game_dir, "abdata", "list", "characustom")
        latest, count = 0.0, 0
        try:
            for name in os.listdir(root):
                if not name.lower().endswith(".unity3d"):
                    continue
                count += 1
                try:
                    latest = max(latest, os.stat(
                        os.path.join(root, name)).st_mtime)
                except OSError:
                    pass
        except OSError:
            pass
        return "%d:%.0f" % (count, latest)

    def clothes_catalog(self, rebuild=False, progress=None):
        """扫描 abdata/list/characustom，返回衣服记录（带磁盘缓存）。"""
        if self._clothes is not None and not rebuild:
            return self._clothes
        p = self.clothes_catalog_path()
        stamp = self._clothes_stamp()
        if os.path.isfile(p) and not rebuild:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if (data.get("game_dir") == self.game_dir
                        and data.get("version") == CLOTHES_CACHE_VERSION
                        and data.get("stamp") == stamp):
                    self._clothes = data.get("records") or []
                    return self._clothes
            except Exception as e:
                print("[HS2] 衣服缓存读取失败，将重扫：%s" % e)

        records = _clothes.load_catalog(self.game_dir, progress=progress)
        if records:
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"game_dir": self.game_dir,
                               "version": CLOTHES_CACHE_VERSION,
                               "stamp": stamp, "records": records},
                              f, ensure_ascii=False)
                os.replace(tmp, p)
            except Exception as e:
                print("[HS2] 衣服缓存写入失败：%s" % e)
        self._clothes = records
        return records

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
