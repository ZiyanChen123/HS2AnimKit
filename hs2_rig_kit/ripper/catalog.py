"""统一动作目录：把 Studio 动作和 H 动作合并成一份可浏览/可搜索的清单。

Studio 动作的名字是 `chair_17_M` 这种，光看名字看不出「这是椅子上的坐姿」。
好在每个动作包的 AnimatorController 里有一张字符串表（``m_TOS``），存着
**源 FBX 的原始路径**：

    Assets/Studio/CreateData/motion/00/female/map_action/chair/chair_17_M.fbx

于是「分类」不用猜 —— 直接用游戏自己的目录结构：

    motion/00/female/map_action/chair   → 分类 female / map_action/chair
    motion/41/bondage                   → 分类 bondage
    motion/h/female/00/tokushu          → 分类 h / tokushu

H 动作走 :mod:`hdb`（日文名 + 阶段）。
"""

import os
import re

from . import clips as _clips
from . import hdb
from . import hashes as _hashes
from . import studio as _studio
from . import unityfs

# v17：官方表的大/小项目名是日文，界面统一翻成中文，避免下拉框看起来乱码。
#      非官方条目仍先按官方原始名匹配，最后才换显示名。
CACHE_VERSION = 18

_FBX_RE = re.compile(r"(?P<path>[A-Za-z0-9_\-./ ]+?\.fbx)", re.IGNORECASE)

# 分类名（游戏里的英文目录名）→ 中文，纯粹为了好翻
GROUP_CN = {
    "action": "动作", "adv": "剧情", "adv_action": "剧情动作", "chair": "椅子",
    "suwari": "座姿", "sleep": "睡眠", "eat": "饮食", "swim": "游泳",
    "map_action": "地图动作", "pose": "姿势", "dance": "舞蹈", "walk": "行走",
    "run": "奔跑", "idle": "待机", "bondage": "拘束", "tachi": "站姿",
    "sit": "坐姿", "kiss": "接吻", "hug": "拥抱", "lick": "舔",
    "mc": "男角", "m": "男角", "f": "女角", "female": "女角", "male": "男角",
    "h": "H", "tokushu": "特殊", "houshi": "奉仕", "sonyu": "插入",
    "aibu": "爱抚", "les": "百合", "3p": "3P", "2p": "2P", "fishing": "钓鱼",
    "appear": "登场", "start": "开场", "cook": "料理", "read": "阅读",
    "shower": "淋浴", "toilet": "厕所", "desk": "桌子", "bed": "床",
    "bath": "浴室", "sao": "SAO 联动", "youshiki": "仪式", "move": "移动",
    "deskchair": "桌椅", "other": "其它", "event": "事件", "pool": "泳池",
    "joy": "喜", "anger": "怒", "sorrow": "悲", "shame": "羞耻",
    "broken": "崩溃", "dependence": "依赖", "listen": "倾听", "jump": "跳跃",
    "dive": "跳水", "rain": "雨", "neko": "猫", "poledance": "钢管舞",
    "sexydance": "性感舞", "attack": "攻击", "down": "倒下", "defense": "防御",
    "sword": "剑", "gun": "枪", "bow": "弓", "rod": "杖", "rifle": "枪",
    "dagger": "短剑", "shieldsword": "剑盾", "throw": "投掷", "aura": "气功",
    "koshikudake": "腰塌", "washiki": "和式", "open": "开", "default": "默认",
    "entry": "入场", "tpose": "T-Pose", "pf": "PF", "isu": "椅子",
}


# 官方 studio/info 表里的日文大/小项目名 → 中文显示名。
# 只在最后输出时替换，匹配非官方条目时仍用原始日文名。
DISPLAY_CN = {
    "キャラ2": "角色2",
    "スタジオ": "工作室",
    "バトル": "战斗",
    "アクション": "动作",
    "ポーズ": "姿势",
    "基本": "基本",
    "感情": "感情",
    "グラビア": "写真",
    "ダンス": "舞蹈",
    "女 格闘B": "女 格斗B",
    "男 カットイン": "男 特写",
    "寝": "睡",
    "床": "床",
    "採取": "采集",
    "椅子・机": "椅子・桌",
    "歩く・走る": "走・跑",
    "遊戯": "游戏",
    "飲食": "饮食",
}

BATTLE_CN = {
    "ライフル": "步枪",
    "両手剣": "双手剑",
    "小太刀1": "小太刀1",
    "小太刀2": "小太刀2",
    "弓矢": "弓箭",
    "投擲": "投掷",
    "拳銃1": "手枪1",
    "拳銃2": "手枪2",
    "格闘A": "格斗A",
    "格闘B": "格斗B",
    "棒・槍": "棒・枪",
    "槌・鎌": "锤・镰",
    "気弾": "气弹",
    "片手剣": "单手剑",
    "盾と剣": "盾与剑",
    "魔法本": "魔法书",
    "魔法杖": "魔法杖",
}


def display_cn(name):
    if not name:
        return name
    if name in DISPLAY_CN:
        out = DISPLAY_CN[name]
    else:
        out = name
        for side in ("女", "男"):
            prefix = side + " "
            if name.startswith(prefix):
                out = prefix + BATTLE_CN.get(name[len(prefix):],
                                             name[len(prefix):])
                break
    # 日文中点统一成中文间隔号，避免菜单里看起来像乱码。
    return out.replace("\u30fb", "\u00b7")


def group_cn(name):
    n = (name or "").lower()
    if n in GROUP_CN:
        return GROUP_CN[n]
    for k in sorted(GROUP_CN, key=len, reverse=True):
        if n.startswith(k):
            return GROUP_CN[k]
    return ""


# 非官方表条目只有英文目录/中文小组名，没有官方「大項目」。
# 这里把能明确对上的小组并回官方的大项目/小项目，避免下拉里出现
# 「ction」「play」「female」这种按路径首段截出来的碎片分类。
NONOFFICIAL_GROUP_MAP = {
    "椅子": ("角色", "椅子"),
    "桌子": ("角色", "椅子・机"),
    "座姿": ("角色", "椅子"),
    "饮食": ("角色", "飲食"),
    "睡眠": ("角色", "寝"),
    "站姿": ("角色", "基本"),
    "移动": ("角色", "歩く・走る"),
    "动作": ("角色", "アクション"),
    "剧情": ("角色", "主人"),
    "剧情动作": ("角色", "主人"),
    "开场": ("角色", "基本"),
    "入场": ("角色", "基本"),
    "女角": ("角色", "基本"),
    "男角": ("角色", "基本"),
    "待机": ("角色", "基本"),
    "腰塌": ("角色", "アクション"),
    "钢管舞": ("スタジオ", "ダンス"),
    "事件": ("スタジオ", "アクション"),
    "SAO 联动": ("スタジオ", "アクション"),
    "开": ("スタジオ", "アクション"),
    "默认": ("スタジオ", "アクション"),
    "H": ("H", "H"),
}


def _fallback_category(name):
    """没有源路径时，用名字猜分类：swim_Idle_00_L → swim / Idle。

    ``S_Idle`` / ``L_WLoop1`` 这种是 Studio 里内嵌的 H 动画，单独归到「H」。
    """
    raw = name or ""
    speed, base, _cn = hdb.stage_of(raw)
    if base in hdb.STAGE_CN or "Breast" in base or "Orgasm" in base:
        return {"category": "h", "top": "h", "group": "H"}
    n = re.sub(r"^[SMLD]_", "", raw)
    toks = [t for t in re.split(r"_", n) if t]
    keep = []
    for t in toks:
        if t.isdigit() or t.lower() in ("loop", "in", "out", "d", "a", "s", "m", "l"):
            break
        keep.append(t)
    if not keep:
        keep = toks[:1] or ["其它"]
    group = keep[0]
    return {"category": "/".join(keep), "top": group.lower(), "group": group}


def _category_of(path):
    """Assets/Studio/CreateData/motion/00/female/map_action/chair/x.fbx"""
    p = path.replace("\\", "/")
    low = p.lower()
    i = low.find("/motion/")
    if i >= 0:
        p = p[i + len("/motion/"):]
    elif low.startswith("assets/"):
        parts = p.split("/")
        p = "/".join(parts[-3:])
    parts = [x for x in p.split("/") if x]
    if parts:
        parts = parts[:-1] or parts          # 去掉文件名
    # 第一段是包号时跳过它
    top = ""
    for seg in parts:
        if not seg.isdigit():
            top = seg.lower()
            break
    group = parts[-1] if parts else ""
    return {
        "category": "/".join(parts),
        "top": top,
        "group": group,
    }


def _tos_categories(tt):
    """AnimatorController 的 m_TOS → {clip 名: 分类}"""
    out = {}
    for entry in tt.get("m_TOS") or []:
        try:
            s = entry[1]
        except Exception:
            continue
        if not isinstance(s, str) or ".fbx" not in s.lower():
            continue
        for m in _FBX_RE.finditer(s):
            p = m.group("path")
            base = os.path.basename(p)[:-4]
            if base:
                out[base] = _category_of(p)
    return out


def scan_studio(game_dir, progress=None, skeleton_paths=None):
    """扫工作室动画 → {唯一键: {...}}

    **唯一键**不是 clip 名 —— 游戏里 clip 名根本不唯一：
    ``studio/anime/08.unity3d`` 里 34 个武器控制器（剑/盾/枪/弓…）**各自**
    带一份 ``attack_00``，按名合并后 34 × 5 个动作只剩 5 个。

    所以这里走两条腿：
      1. 官方表（:mod:`studio`）给出**分组**和**唯一身份**
         （包, 控制器, state 名）—— 见 ripper/studio.py；
      2. 直接扫包里的 clip，把官方表没列的（studio 包内嵌的 H 阶段片段等）
         也收进来。

    重名的官方条目用 ``"clip @ 控制器"`` 做键，没重名的保持 clip 名不变，
    这样老命令 ``load attack_00`` 照样能用。
    """
    lookup = {}
    if skeleton_paths:
        try:
            lookup = _hashes.build_lookup(skeleton_paths)
        except Exception:
            lookup = {}

    bundles = unityfs.anime_bundles(game_dir)
    out = {}

    # ---- 1) 官方表：分组 + 唯一身份 + 精确的 clip 寻址 ----
    official = {}
    try:
        official = _studio.build_studio_index(game_dir, progress=None)
    except Exception:
        official = {}

    # ---- 2) 扫包：官方表之外的 clip 也要收 ----
    for i, path in enumerate(bundles):
        fname = os.path.basename(path)
        if progress:
            progress(i, len(bundles), fname)
        try:
            b = unityfs.Bundle(path)
        except Exception:
            continue

        # 控制器里的源路径表 → 动作名到分类
        cats = {}
        for pid in list(b.objects):
            obj = b.objects[pid]
            if unityfs._type_id(obj) != 95 and unityfs._type_id(obj) != 91:
                continue
            try:
                tt = b.typetree(pid)
            except Exception:
                continue
            if unityfs._type_id(obj) == 91:
                for k, v in _tos_categories(tt).items():
                    cats.setdefault(k, v)

        # 动作本体
        for pid in b.clips():
            try:
                tt = b.typetree(pid)
            except Exception:
                continue
            name = tt.get("m_Name") or ""
            if not name:
                continue
            fit = _clips.clip_fit(tt, lookup) if lookup else 1.0
            e = out.get(name)
            if e is None:
                e = {"name": name, "files": [], "fit": 0.0,
                     "category": "", "top": "", "group": "",
                     "refs": [], "pid": pid, "bundle": fname}
                out[name] = e
            e["refs"].append([fname, pid])
            if fname not in e["files"]:
                e["files"].append(fname)
            if fit > e["fit"]:
                e["fit"] = round(fit, 3)
            c = cats.get(name)
            if c and not e["category"]:
                e["category"] = c["category"]
                e["top"] = c["top"]
                e["group"] = c["group"]

        b.env = None
    if progress:
        progress(len(bundles), len(bundles), "")

    # ---- 3) 合并：官方条目用它的唯一键，其余保持 clip 名 ----
    # 官方表给的是**基名**（chair_Idle_00），包里存的是变体
    # （chair_Idle_00_S / _M / _L）—— 变体由插件已有的 S/M/L 机制处理，
    # 这里要把重复的变体条目剔掉，别让列表里出现「一条官方 + 三条变体」。
    off_bases = set()
    for key, off in official.items():
        off_bases.add(_studio._split_height(off["state"])[0])
        off_bases.add(off["state"])

    for key, off in official.items():
        e = out.get(off["clip"]) or {}
        merged = dict(e)
        merged.update({
            "name": off["name"],
            "clip": off["clip"],
            "state": off["state"],
            "ctrl": off["ctrl"],
            "official": True,
            "official_key": key,
            "group": off["group"],
            "group_cn": off["group_cn"],
            "top": off["top"],
            "top_cn": off["top_cn"],
            "category": off["category"],
            "item": off["item"],
            "pid": off["pid"] if off["pid"] is not None else merged.get("pid"),
            "bundle": off["bundle"] or merged.get("bundle"),
        })
        if off["pid"] is not None:
            merged["files"] = [off["bundle"]]
            merged["refs"] = [[off["bundle"], off["pid"]]]
        merged["group_cn"] = off["group_cn"] or merged.get("group_cn") or ""
        out[key] = merged
        # 官方条目占了这个 clip 名 —— 把按名建的旧条目挪走，别让它顶掉新条目
        if out.get(off["clip"]) is not merged:
            out.pop(off["clip"], None)

    # 官方基名的 S/M/L 变体：收进官方条目自己的 variants 里，
    # 免得在列表里重复出现（列表按基名显示，身高用下拉切）。
    off_by_base = {}
    for k, v in official.items():
        base, _height = _studio._split_height(v["state"])
        off_by_base.setdefault(base, []).append(k)
        off_by_base.setdefault(v["state"], []).append(k)
    for name in list(out.keys()):
        e = out[name]
        if e.get("official"):
            continue
        base, height = _studio._split_height(name)
        if base not in off_bases or not height:
            continue
        owners = [k for k in dict.fromkeys(off_by_base.get(base) or [])
                  if k in out]
        if len(owners) > 1:
            # 同名攻击动作分属多个武器控制器时，优先精确 state 名
            exact = [k for k in owners if official[k]["state"] == name]
            if exact:
                owners = exact
        owner = owners[0] if owners else None
        if owner is None:
            continue
        tgt = out.get(owner)
        if tgt is None:
            continue
        tgt.setdefault("variants", {})[height] = name
        out.pop(name, None)

    # 没有源路径 / 不是官方表的，兜底按名字猜分类
    for name, e in out.items():
        if e.get("official"):
            continue
        if not e["category"]:
            c = _fallback_category(name)
            e["category"] = c["category"]
            e["top"] = c["top"]
            e["group"] = c["group"]
        if not e.get("group_cn"):
            e["group_cn"] = group_cn(e.get("group")) or group_cn(e.get("top"))
        if not e.get("refs"):
            e["refs"] = [[e["files"][0], None]] if e["files"] else []

    # 非官方条目补上官方分类：先按小组中文名精确匹配官方小组，
    # 再查别名表；两边都没有才放进「其它」。
    official_groups = {}
    for off in official.values():
        top_cn, group_cn_ = off.get("top_cn"), off.get("group_cn")
        if top_cn and group_cn_:
            official_groups.setdefault(group_cn_, (top_cn, group_cn_))
    for e in out.values():
        if e.get("official"):
            continue
        g = e.get("group_cn") or ""
        pair = official_groups.get(g) or NONOFFICIAL_GROUP_MAP.get(g)
        if pair:
            e["top_cn"], e["group_cn"] = pair
        else:
            e["top_cn"] = "其它"
            e["group_cn"] = g or "其它"

    for e in out.values():
        e["top_cn"] = display_cn(e.get("top_cn"))
        e["group_cn"] = display_cn(e.get("group_cn"))
    return out


def build(game_dir, progress=None, roles=("female", "male", "item"),
          with_studio=True, with_h=True, skeleton_paths=None):
    """完整目录。progress(i, total, label) 会被调用很多次（分两段）。"""
    def wrap(lo, hi):
        def cb(i, total, name):
            if progress and total:
                progress(lo + (hi - lo) * i // max(total, 1), 100, name)
        return cb

    studio = {}
    h = None
    if with_studio:
        studio = scan_studio(game_dir, progress=wrap(0, 70),
                             skeleton_paths=skeleton_paths)
    if with_h and hdb.has_h_animations(game_dir):
        h = hdb.build(game_dir, roles=roles, progress=wrap(70, 100))

    # 归一化：每个动作留一条「首选引用」，加载时不用再挑
    for name, e in studio.items():
        refs = e.get("refs") or []
        if refs:
            e["bundle"] = refs[0][0]
            e["pid"] = refs[0][1]
    return {
        "version": CACHE_VERSION,
        "game_dir": game_dir,
        "studio": studio,
        "h": h,
    }
