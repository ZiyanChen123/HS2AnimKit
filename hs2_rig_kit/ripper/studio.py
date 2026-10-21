"""工作室动画的官方目录：分组 + 唯一定位。

为什么要有这个模块
------------------
插件原来按 **clip 名** 给工作室动作建索引（``catalog.scan_studio``），
但游戏里 clip 名根本不唯一：

* ``studio/anime/08.unity3d`` 一个包里就有 313 个 clip、只有 86 个不同的名字 ——
  34 个武器控制器（剑/盾/枪/弓…）**各自**带一份 ``attack_00`` / ``attack_01`` /
  ``attack_02`` / ``defense_00`` / ``down_00``，共 34 × 5 个动作，
  按名合并后只剩 5 个。
* ``f_studio_34`` 里更离谱：``Anger`` 这个 clip 名重复 10 次
  （对应 ``Anger_00`` … ``Anger_09``）。

官方的做法在 ``abdata/studio/info/*.unity3d``：

* ``Anime_包_大項目_小項目`` —— 动作清单表，每行一个动作，
  给出 **表示名 / バンドルパス / ファイル名（控制器名）/ クリップ名**，
  以及这个动作要带的道具（``バンドル`` + ``ファイル`` + ``親`` 挂点）。
* ``AnimeGroup_包`` —— 大項目编号 → 名称（キャラ / スタジオ / バトル …）。
* ``AnimeCategory_包`` —— 小項目编号 → 名称。

表名本身就是分组：``Anime_08_15_19`` = 包 08、大項目 15（バトル）、
小項目 19（女 片手剣）。官方表里的 ``クリップ名`` 和包里的 clip 对象名
**不一定相同**（表写 ``Anger_00``，包里叫 ``Anger``），所以还要借
AnimatorController 的状态机把两边接上：

    state.m_NameID ──(m_TOS)──▶ state 名（= 官方表的 クリップ名）
    state → m_ClipID ──(m_AnimationClips)──▶ clip 的 PathID

这样每个动作都有唯一身份：``(包, 控制器, state 名)``。

对外的接口
----------
``build_studio_index()`` 返回
``{唯一键: {name, clip, ctrl, bundle, pid, group, top, group_cn, top_cn,
item, ...}}``；``唯一键`` 形如 ``attack_00 @ f_03_Sword``（无重名时就是
clip 名本身，保证老命令 ``load attack_00`` 照样能用）。
"""

import os
import re

from . import unityfs

# --------------------------------------------------------------------------
# 官方表：abdata/studio/info/*.unity3d
# --------------------------------------------------------------------------

INFO_DIR = os.path.join("abdata", "studio", "info")

# 表结构（Anime_XX_YY_ZZ）：
#   rows[0] = [大項目名, 小項目注释, ...]     ← 标题行
#   rows[1] = [列名, ...]                    ← 列名行
#   rows[2:] = 数据行
_COL_ORDER = "表示順番"
_COL_BIG = "大きい項目"
_COL_SMALL = "中間項目"
_COL_DISPLAY = "表示名"
_COL_BUNDLE_PATH = "バンドルパス"
_COL_FILE = "ファイル名"
_COL_CLIP = "クリップ名"
_COL_PARENT = "親"
_COL_SCALE = "親のスケール影響受けるか"

# 「バンドル」列出现三次：道具 / ベースアニメーター / アニメーター
_COL_ITEM_BUNDLE = 0

# 官方表里这几个大項目番号在本版本没有 AnimeGroup 表（大概是组名被删了，
# 但动作还在）。按内容给个说得过去的名字，别让分组显示成裸编号。
_FALLBACK_TOP = {
    "0": "角色",
    "13": "狐巫女",
    "17": "魅魔",
    "31": "修女",
    "33": "SM",
    "35": "角色3",
}


def _flat(v):
    """Unity 的嵌套 list[list[str]] → 平的 list[list[str]]"""
    out = []
    for x in (v or []):
        if isinstance(x, dict) and "list" in x:
            out.extend(_flat(x["list"]))
        else:
            out.append(x)
    return out


def _rows_of(tt):
    """MonoBehaviour 的 list → (标题行, 列名行, 数据行)"""
    raw = tt.get("list")
    if not isinstance(raw, list):
        return None
    rows = []
    for r in raw:
        if isinstance(r, dict) and "list" in r:
            rows.append([("" if x is None else str(x)) for x in r["list"]])
        else:
            rows.append(r)
    if len(rows) < 3:
        return None
    return rows[0], rows[1], rows[2:]


def _cell(cols, row, name, default=""):
    try:
        i = cols.index(name)
    except ValueError:
        return default
    if i >= len(row):
        return default
    return (row[i] or "").strip()


def _bundle_cols(cols):
    """三个「バンドル」列的下标：道具 / ベース / アニメーター。"""
    out = []
    for i, c in enumerate(cols):
        if c == "バンドル":
            out.append(i)
    return out


def read_info_tables(game_dir):
    """读 abdata/studio/info/*.unity3d。

    返回 ``{"anime": {...}, "group": {...}, "category": {...}}``：
      * anime    : {表名: {"title": [...], "cols": [...], "rows": [...]}}
      * group    : {编号: 名称}
      * category : {编号: 名称}
    """
    base = os.path.join(game_dir, INFO_DIR)
    anime = {}
    group = {}
    category = {}
    if not os.path.isdir(base):
        return {"anime": anime, "group": group, "category": category}

    for fn in sorted(os.listdir(base)):
        if not fn.lower().endswith(".unity3d"):
            continue
        path = os.path.join(base, fn)
        try:
            b = unityfs.Bundle(path)
        except Exception:
            continue
        for pid in list(b.objects):
            if unityfs._type_id(b.objects[pid]) != 114:
                continue
            try:
                tt = b.objects[pid].read_typetree()
            except Exception:
                continue
            nm = tt.get("m_Name") or ""
            got = _rows_of(tt)
            if not got:
                continue
            title, cols, body = got
            if nm.startswith("AnimeGroup_"):
                # AnimeGroup_XX：このパッケージ用の大項目番号 → 名称
                pkg = nm.rsplit("_", 1)[-1]
                for r in body:
                    if len(r) >= 3 and (r[1] or "").strip():
                        group[(pkg, (r[1] or "").strip())] = (r[2] or "").strip()
            elif nm.startswith("AnimeCategory_"):
                # AnimeCategory_XX_YY：YY = 大項目番号、この番号 → 小項目名
                # 同じ小項目番号が大項目ごとに違う意味を持つので (大, 小) で引ける
                parts = nm.split("_")
                big = parts[2] if len(parts) > 2 else ""
                for r in body:
                    if len(r) >= 3 and (r[1] or "").strip():
                        category[(big, (r[1] or "").strip())] = (r[2] or "").strip()
            elif nm.startswith("Anime_"):
                anime[nm] = {"title": title, "cols": cols, "rows": body,
                             "file": fn}
        b.env = None
    return {"anime": anime, "group": group, "category": category}


# --------------------------------------------------------------------------
# 控制器内部：state 名 → clip 的 PathID
# --------------------------------------------------------------------------

def controller_states(bundle, ctrl_pid):
    """一个控制器里的动作 → {基名: [(变体名, clip 的 PathID), ...]}。

    这里有**两层**名字，官方表给的是「基名」，包里存的是「变体名」：

        官方表 クリップ名     包内 clip 名
        chair_Idle_00   →    chair_Idle_00_S / _M / _L
        chair_00_in     →    chair_00_S_in / _M_in / _L_in
        Anger_00        →    Anger        （34 包里没有身高变体，10 个同名）

    所以按「基名」归组，把变体都收在一起；同名多份时靠**顺序**区分
    （官方表按 Anger_00…Anger_09 排，包里也是这个顺序）。

    ``variants`` 里按 (变体名, pathid) 存，加载时再按身高档位挑。
    """
    try:
        tt = bundle.typetree(ctrl_pid)
    except Exception:
        return {}

    clips = tt.get("m_AnimationClips") or []
    tos = {}
    for entry in (tt.get("m_TOS") or []):
        try:
            h, s = entry[0], entry[1]
        except Exception:
            continue
        if isinstance(s, str) and s and not s.startswith("Assets/"):
            tos.setdefault(h, []).append(s)

    # state → clip：state 的 m_NameID 查 m_TOS 拿名字，m_ClipID 查 clip
    state_clip = []           # [(state 名, pathid)]，保持状态机里的顺序
    ctrl = tt.get("m_Controller") or {}
    for sm in (ctrl.get("m_StateMachineArray") or []):
        data = (sm or {}).get("data") or {}
        for st in (data.get("m_StateConstantArray") or []):
            d = (st or {}).get("data") or {}
            nid = d.get("m_NameID")
            bt = d.get("m_BlendTreeConstantArray") or []
            if not bt:
                continue
            nodes = ((bt[0] or {}).get("data") or {}).get("m_NodeArray") or []
            if not nodes:
                continue
            cid = ((nodes[0] or {}).get("data") or {}).get("m_ClipID")
            if cid is None or cid < 0 or cid >= len(clips):
                continue
            pathid = (clips[cid] or {}).get("m_PathID")
            if pathid is None:
                continue
            names = tos.get(nid) or []
            # state 名 = m_TOS 条目的最后一段；没有就用 clip 自己的名字
            label = ""
            for full in names:
                tail = full.split(".")[-1]
                if tail:
                    label = tail
                    break
            state_clip.append((label, pathid))

    # 包内 clip 名 → PathID（用于「官方名 == 包内名」的情况）
    by_name = {}
    clip_name_of = {}
    for e in clips:
        pid = (e or {}).get("m_PathID")
        if pid is None:
            continue
        try:
            cn = bundle.typetree(pid).get("m_Name") or ""
        except Exception:
            continue
        if cn:
            by_name.setdefault(cn, []).append(pid)
            clip_name_of[pid] = cn

    out = {}
    for label, pathid in state_clip:
        if not label:
            continue
        # 基名看 state 名（官方表那一列），身高档看 clip 名 ——
        # 两者常常不同：state 叫 Shower_A_01，clip 叫 M_Shower_A_01
        base, _v = _split_height(label)
        _cb, var = _split_height(clip_name_of.get(pathid, ""))
        out.setdefault(base, []).append((var, pathid))

    # 官方名直接等于包内名的情况（attack_00 这种）也补上
    for cn, pids in by_name.items():
        base, var = _split_height(cn)
        bucket = out.setdefault(base, [])
        have = {p for _v, p in bucket}
        for pid in pids:
            if pid not in have:
                bucket.append((var, pid))
    return out


_HEIGHT_RE = re.compile(r"^(?P<base>.+?)_(?P<h>S|M|L)(?P<tail>_in|_loop|_out)?$")
_HEIGHT_PREFIX_RE = re.compile(r"^(?P<h>S|M|L)_(?P<base>.+)$")


def _split_height(name):
    """拆出「基名 + 身高档」。

    游戏的命名有两套：

        后缀式   chair_00_M_in → ("chair_00_in", "M")
        前缀式   S_Shower_A_01 → ("Shower_A_01", "S")

    官方表写的都是**基名**（``chair_Idle_00`` / ``Shower_A_01``），
    包里存的是带档的变体 —— 两种都得认出来。
    """
    n = name or ""
    m = _HEIGHT_RE.match(n)
    if m:
        base = m.group("base") + (m.group("tail") or "")
        return base, m.group("h")
    m = _HEIGHT_PREFIX_RE.match(n)
    if m:
        return m.group("base"), m.group("h")
    return n, ""


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------

def _pkg_of_bundle_path(p):
    """``studio/anime/08.unity3d`` → ``08.unity3d``"""
    p = (p or "").replace("\\", "/")
    if "/" in p:
        p = p.rsplit("/", 1)[-1]
    return p


def _any_group(table, num):
    """编号其实是全局的，只是表分散在各包里 —— 任意包里找到同名即可。"""
    for (_pkg, n), name in table.items():
        if n == num and name:
            return name
    return ""


def _any_category(table, num):
    """小項目编号同理。不同大項目下同名编号含义不同，取第一个非空名。"""
    for (_big, n), name in table.items():
        if n == num and name:
            return name
    return ""


def build_studio_index(game_dir, progress=None, skeleton_paths=None):
    """工作室动作的完整索引。

    返回 ``{唯一键: 条目}``，条目字段：
      name        显示用（官方「表示名」，没有就用 clip 名）
      clip        包内 clip 名
      ctrl        控制器名
      bundle      包文件名（在 abdata/studio/anime/ 下）
      pid         clip 的 PathID（能定位时）
      state       官方 state 名（= 表里的 クリップ名）
      group       小項目编号
      group_cn    小項目名
      top         大項目编号
      top_cn      大項目名
      category    "大項目/小項目"
      item        {bundle, prefab, parent, scale}（官方表给的道具）
    """
    info = read_info_tables(game_dir)
    anime = info["anime"]
    if not anime:
        return {}

    # ---- 第一遍：把官方表摊平成一行一个动作 ----
    rows = []          # (表名, 标题, 列名, 数据行)
    for tname, t in anime.items():
        rows.append((tname, t["title"], t["cols"], t["rows"]))

    # ---- 需要打开哪些包 ----
    want_ctrl = {}     # (bundle 文件名, 控制器名) → [(tname, 行下标)]
    for tname, title, cols, body in rows:
        for i, r in enumerate(body):
            bp = _cell(cols, r, _COL_BUNDLE_PATH)
            fn = _cell(cols, r, _COL_FILE)
            if not bp or not fn:
                continue
            want_ctrl.setdefault((_pkg_of_bundle_path(bp), fn),
                                 []).append((tname, i))

    anime_dir = os.path.join(game_dir, "abdata", "studio", "anime")
    out = {}
    used_key = {}

    for bi, (fname, ctrl_name) in enumerate(sorted(want_ctrl)):
        if progress:
            progress(bi, len(want_ctrl), fname)
        path = os.path.join(anime_dir, fname)
        if not os.path.isfile(path):
            continue
        try:
            b = unityfs.Bundle(path)
        except Exception:
            continue

        # 找控制器
        ctrl_pid = None
        for pid in list(b.objects):
            if unityfs._type_id(b.objects[pid]) != 91:
                continue
            try:
                tt = b.typetree(pid)
            except Exception:
                continue
            if (tt.get("m_Name") or "") == ctrl_name:
                ctrl_pid = pid
                break
        if ctrl_pid is None:
            b.env = None
            continue

        states = controller_states(b, ctrl_pid)
        used_base = {}          # 基名 → 已用次数（同名 clip 按出现顺序分配）

        for tname, ridx in want_ctrl[(fname, ctrl_name)]:
            t = anime[tname]
            cols, body = t["cols"], t["rows"]
            if ridx >= len(body):
                continue
            r = body[ridx]
            state = _cell(cols, r, _COL_CLIP)
            if not state:
                continue
            display = _cell(cols, r, _COL_DISPLAY) or state
            # 大項目/小項目编号：优先行里写的，行里没有就用表名
            big = _cell(cols, r, _COL_BIG)
            small = _cell(cols, r, _COL_SMALL)
            parts = tname.split("_")
            if len(parts) >= 4:
                big = big or parts[2]
                small = small or parts[3]
            pkg = parts[1] if len(parts) > 1 else ""
            # AnimeGroup 按包存（小項目编号则按 (大項目, 小項目) 存）。
            # 编号其实是全局的，只是表分散在各包里 —— 先按包找，找不到再全局找。
            top_cn = (info["group"].get((pkg, big))
                      or _any_group(info["group"], big)
                      or _FALLBACK_TOP.get(big, ""))
            grp_cn = (info["category"].get((big, small))
                      or info["category"].get(("", small))
                      or _any_category(info["category"], small) or "")
            if not grp_cn:
                # 表标题第 0 格就是小項目名
                grp_cn = (t["title"][0] or "").strip() if t["title"] else ""

            pid = _resolve_pid(states, state, used_base)
            clip_name = state
            if pid is not None:
                try:
                    ctt = b.typetree(pid)
                    clip_name = ctt.get("m_Name") or state
                except Exception:
                    pass

            # 道具：官方表给了「道具包 / prefab / 挂点骨」
            item = {}
            bcols = _bundle_cols(cols)
            if bcols:
                bi0 = bcols[0]
                if bi0 < len(r) and (r[bi0] or "").strip():
                    item["bundle"] = (r[bi0] or "").strip()
                    item["prefab"] = _cell(cols, r, "ファイル")
                    item["manifest"] = _cell(cols, r, "マニフェスト")
                    item["parent"] = _cell(cols, r, _COL_PARENT)
                    item["scale"] = _cell(cols, r, _COL_SCALE)

            key = _unique_key(state, ctrl_name, used_key)
            out[key] = {
                "name": display,
                "clip": clip_name,
                "state": state,
                "ctrl": ctrl_name,
                "bundle": fname,
                "pid": pid,
                "group": small,
                "group_cn": grp_cn,
                "top": big,
                "top_cn": top_cn,
                "category": "%s/%s" % (top_cn or big, grp_cn or small),
                "item": item,
                "official": True,
            }
        b.env = None

    if progress:
        progress(len(want_ctrl), len(want_ctrl), "")
    return out


def _resolve_pid(states, name, used_base):
    """在控制器的动作里找到官方表那一行对应的 clip。

    官方表写的是**基名**，包里存的是**变体名**：

        官方 Anger_00       →  34 包里 10 个同名的「Anger」，按出现顺序第 1 个
        官方 chair_Idle_00  →  包里 chair_Idle_00_S / _M / _L，挑默认档
        官方 koshikudake_A  →  包里 Koshikudake_A（大小写不一样）

    ``used_base`` 记录每个基名已经用到第几个，保证 34 个同名 Anger 各归各位。
    """
    base, height = _split_height(name)
    bucket = states.get(base)
    if not bucket:
        # 官方名本身可能就是变体名（attack_00 这种）
        bucket = states.get(name)
        if not bucket:
            # 大小写不一致（Koshikudake_A vs koshikudake_A）
            bucket = _find_ci(states, name)
            if not bucket:
                return None
        base = name if states.get(name) else base

    idx = used_base.get(base, 0)
    used_base[base] = idx + 1

    if len(bucket) == 1:
        return bucket[0][1]

    # 有身高档 → 挑对应的
    if height:
        for var, pid in bucket:
            if var == height:
                return pid
    # 没有指定档 → 挑「中间」的那个（S<M<L），有变体名带序号的按序号取
    order = {"S": 0, "M": 1, "L": 2}
    with_h = [(order.get(v, 9), i, p) for i, (v, p) in enumerate(bucket)]
    with_h.sort()
    return with_h[min(idx, len(with_h) - 1)][2]


def _find_ci(states, name):
    """忽略大小写找一个基名（游戏里大小写不统一）。"""
    low = name.lower()
    for k, v in states.items():
        if k.lower() == low:
            return v
    return None


def _unique_key(state, ctrl, used):
    """唯一键：没有重名就用 state 本身（保持老命令可用），有重名就加控制器后缀。"""
    base = state
    if base in used and used[base] != ctrl:
        key = "%s @ %s" % (state, ctrl)
        n = 2
        while key in used:
            key = "%s @ %s (%d)" % (state, ctrl, n)
            n += 1
        used[key] = ctrl
        return key
    used[base] = ctrl
    return base
