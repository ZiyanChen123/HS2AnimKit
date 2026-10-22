"""H 场景动画 + H 道具数据表。

HS2 的 H 动画不在 `studio/anime` 里，而是按「场景 × 类型 × 番号」拆在：

    abdata/animator/h/female/<pack>/<type>.unity3d     女角动作
    abdata/animator/h/male/<pack>/<type>.unity3d       男角动作
    abdata/animator/h/h_item/<pack>/<type>.unity3d     道具自己的动作
    abdata/h/h_item/<pack>.unity3d                     道具 prefab（含骨骼/网格）

<type> 是 houshi(奉仕) / aibu(愛撫) / sonyu(挿入) / tokushu(特殊) / les / 3P 等。

动作包里的 AnimationClip 名字是**重复**的（每个控制器都用 S_Idle / L_WLoop1
这一套），单看名字分不出是哪一段 H 动画。真正的对应关系在：

    abdata/list/h/animationinfo/<scene>.unity3d

里面每个 MonoBehaviour 叫 <type>_<pack>，带一张 37 列的表：

    0 日文名   1 id   2 ポーチ  3 アイテムID  4 manifest  5 道具包
    6 道具 prefab   7 ベース包   8 ベース animator   9 道具动画包
    10 道具 animator   11 直接親子付け  12 誰に親子付(0=女 1=男)
    13 親子付先(骨名)  14 くっつけるもの  15 親スケール適応
"""

import os
import re

from .. import jpcn as _jpcn
from . import unityfs

# 阶段名（clip 名去掉 S/M/L 速度前缀和 D_ 变体前缀后）→ 中文
STAGE_CN = {
    "Idle": "待机",
    "Insert": "插入",
    "Pull": "拔出",
    "Drop": "结束",
    "WLoop": "抽插·弱",
    "WLoop1": "抽插·弱1",
    "WLoop2": "抽插·弱2",
    "MLoop": "抽插·中",
    "SLoop": "抽插·强",
    "SLoop1": "抽插·强1",
    "SLoop2": "抽插·强2",
    "OLoop": "绝顶循环",
    "OLoopFast": "绝顶循环·快",
    "Orgasm": "绝顶",
    "Orgasm_A": "绝顶（后）",
    "Orgasm_IN": "绝顶·中出",
    "Orgasm_IN_A": "绝顶·中出（后）",
    "Orgasm_OUT": "绝顶·外出",
    "Orgasm_OUT_A": "绝顶·外出（后）",
    "OrgasmS_IN": "绝顶（女）·中出",
    "OrgasmM_IN": "绝顶（男）·中出",
    "OrgasmF_IN": "绝顶（女）·中出",
    "OrgasmM_OUT": "绝顶（男）·外出",
    "OrgasmM_OUT_A": "绝顶（男）·外出（后）",
    "Vomit": "呕吐",
    "Vomit_IN": "呕吐·中",
    "Vomit_A": "呕吐（后）",
    "Drink": "饮用",
    "Drink_IN": "饮用·中",
    "Drink_A": "饮用（后）",
}

# 注意：S/M/L 不是「强中弱」，是**身高**三档。
# 证据：动作控制器里有一条混合树，参数名就叫 height，阈值 [0, 0.5, 1]，
# 子 clip 是 S_Idle / M_Idle / L_Idle；工作室动作（chair_17_S/M/L）也是同一条。
HEIGHT_CN = {"S": "矮", "M": "中", "L": "高"}
HEIGHT_ORDER = ("S", "M", "L")

# 身高 shape(0~1) → 身高骨骼缩放，来自游戏捏人对表 list/customshape.unity3d 的
# cf_anmShapeBody（cf_s_height 条目，线性 0.8 → 1.0，25 个采样点）：
#   shape 0   = S 档 → scale 0.8
#   shape 0.5 = M 档 → scale 0.9
#   shape 1   = L 档 → scale 1.0（就是 oo_base 骨架的 rest，L 档不缩放）
HEIGHT_SCALE = {"S": 0.8, "M": 0.9, "L": 1.0}

# 胸部动作的后缀是**胸围** 5 档（S 最小 → L 最大），前缀是相位（弱/强/绝顶…）
BUST_CN = {"S": "最小", "SM": "小", "M": "中", "ML": "大", "L": "最大"}
BUST_ORDER = ("S", "SM", "M", "ML", "L")
BREAST_PHASE_CN = {"W": "弱", "W1": "弱1", "W2": "弱2", "S": "强", "S1": "强1",
                   "S2": "强2", "M": "中", "O": "绝顶"}

_BREAST_RE = re.compile(
    r"(?P<phase>W1|W2|S1|S2|W|S|M|O)Breastt?(?P<bust>SM|ML|S|M|L)(?P<var>\d*)")
# 「誰に親子付」 —— **0 = 男、1 = 女**，其余（见到的是 4）= 场景里的固定物。
#
# 这一层以前是反的（写成 0=女 1=男），男角手上该拿的东西会跑到女角手上。
# 反查的证据有四条，互相独立：
#
#   1. 吊るし挿入 / 男拘束フェラ / 男拘束立位：绳子、枷锁全挂在男角身上，
#      表里是 0；
#   2. 拘束二穴バイブ：普通振动棒 who=0（男角拿着送入），
#      アナルバイブ who=1 + 直接親子付け + 骨名 k_f_ana_00 —— 后者明显是女角
#      身上的骨，不可能挂到男角；
#   3. ワンコバック / ワンコフェラ（带牵引绳）：牵引绳把手 p_item_motite who=0
#      = 男角牵着，链子 p_item_chain 的第二组挂点 who=1 = 女角脖子；
#   4. カメラ撮影オナニー：摄像机 who=0 = 男角手持拍摄。
#
# 至于 4：那一行的骨是 o_h2_mi_bathRoom06a_00 / n_shower_pivot 这种房间物件，
# 根本不是角色身上的骨，所以归到 scene。
PARENT_WHO = {"0": "male", "1": "female"}


# --------------------------------------------------------------------------
# 身高缩放映射（来自游戏捏人对表 customshape.unity3d 的 cf_anmShapeBody）
# --------------------------------------------------------------------------
#
# HS2 捏人系统调身高不是只缩 cf_N_height 一根骨，而是给整套骨架发一组
# 逐步的局部变换（pos / rot / scale），S/M/L 是这条曲线的 0 / 12 / 24 三个
# 采样点。下面是预打包好的每根骨三档(px,py,pz, qx,qy,qz,qw, sx,sy,sz)的
# 结果表 —— 由 _build_height_map() 从真实游戏数据生成并缓存到本地。
# 若游戏数据缺失/更名，会退回旧的单骨 cf_N_height 缩放。

_HEIGHT_MAP_CACHE = {"key": None, "data": None}


def _height_map_path(cache_dir=None):
    """把身高映射缓存在 .hs2_animkit 缓存目录（和 catalog 一处，避免每次解包）。"""
    base = cache_dir or os.path.expanduser("~/.hs2_animkit")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return os.path.join(base, "height_bone_map.json")


def build_height_map(game_dir, cache_dir=None, force=False):
    """从 cf_anmShapeBody 解出身高骨骼映射，写给 / 读自本地缓存。

    cf_anmShapeBody 的结构（TextAsset，绝大多数是逐 item 的 LinearCurve）：
    每个条目有一个名字（cf_s_xxx），一段 25 个采样的曲线，采样是
    (int32 no, float3 pos, float3 rot(deg), float3 scale)。名字里
    `cf_s_height` 是全身统一缩放；`cf_s_<Bone>_<mod>` 是具体骨在
    X/Y/Z 三个轴上的独立缩放。

    返回 {骨骼blender名字: {"S": [...,16] 或 3, "M": ..., "L": ...}}
    值统一化成 3 元局部缩放。
    """
    path = os.path.join(game_dir, "abdata", "list", "customshape.unity3d")
    if not os.path.isfile(path):
        return {}
    st = os.stat(path)
    key = (path, st.st_size, int(st.st_mtime))
    cpath = _height_map_path(cache_dir)
    if not force and _HEIGHT_MAP_CACHE["key"] == key:
        return _HEIGHT_MAP_CACHE["data"] or {}
    if not force and os.path.isfile(cpath):
        try:
            import json
            with open(cpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("src") == key:
                _HEIGHT_MAP_CACHE["key"] = key
                _HEIGHT_MAP_CACHE["data"] = data["bones"]
                return data["bones"]
        except Exception:
            pass

    bundle = unityfs.Bundle(path)
    found = _parse_shape_bones(bundle)
    _HEIGHT_MAP_CACHE["key"] = key
    _HEIGHT_MAP_CACHE["data"] = found
    try:
        import json
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"src": key, "bones": found}, f)
    except Exception:
        pass
    return found


def _parse_shape_bones(bundle):
    """读 cf_anmShapeBody 的所有条目 → {bone_name: {"S": scale3, ...}}。

    只保留「真身高缩放」：cf_s_height 统一缩、具体骨如 cf_s_Spine01_h。
    丢弃 cf_hit_*（碰撞体积，跟骨架无关）和名字看不清的。
    """
    import struct
    # 先找 cf_anmShapeBody 这个 TextAsset
    target = None
    for _pid, obj in bundle.objects.items():
        try:
            tt = obj.read_typetree()
        except Exception:
            continue
        if tt.get("m_Name") != "cf_anmShapeBody":
            continue
        raw = obj.get_raw_data()
        n = struct.unpack_from("<i", raw, 0)[0]
        off = (4 + abs(n) + 3) & ~3           # 跳过 UTF8 名字 + 对齐
        ln = struct.unpack_from("<i", raw, off)[0]
        off += 4
        target = raw[off:off + ln]
        break
    if target is None:
        return {}

    def rstr(data, o):
        sz = 0; shift = 0
        while True:
            b = data[o]; o += 1
            sz |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        return data[o:o + sz].decode("utf-8", "replace"), o + sz

    count = struct.unpack_from("<i", target, 0)[0]
    p = 4
    samples_by_name = {}
    for _ in range(count):
        name, p = rstr(target, p)
        keys = struct.unpack_from("<i", target, p)[0]; p += 4
        rows = []
        for _k in range(keys):
            no = struct.unpack_from("<i", target, p)[0]; p += 4
            pos = struct.unpack_from("<3f", target, p); p += 12
            rot = struct.unpack_from("<3f", target, p); p += 12
            scale = struct.unpack_from("<3f", target, p); p += 12
            rows.append((no, pos, rot, scale))
        if name.startswith("cf_s_") and rows:
            samples_by_name[name] = rows

    # 三条曲线与 S/M/L 的对应是等分（0..24 共 25 点）
    def pick(rows, idx):
        # 采样点编号固定 0..24；找离 idx 最近的有效点
        best = rows[0][1:]
        for row in rows:
            if abs(row[0] - idx) < abs(best[0] - idx) if False else \
               (row[0] == idx or (abs(row[0] - idx) < 3)):
                best = row[1:]
        # 兜底：取最后一个可用点
        for row in rows:
            if row[0] >= idx:
                best = row[1:]
                break
        return best

    out = {}
    # 逐条目收集每根骨的 S/M/L 三轴缩放（同一根骨可能有多条 axis 曲线）
    # 把 axis 曲线合并：每个档位的三个轴 = 各 axis 曲线在该轴上的值相乘
    per_bone = {}
    for name, rows in samples_by_name.items():
        s = pick(rows, 0)
        m = pick(rows, 12)
        l = pick(rows, 24)
        bone = _shape_bone_name(name)
        if not bone:
            continue
        axis = _shape_axis(name)   # "x"/"y"/"z"/None
        rec = per_bone.setdefault(bone, {"axis": {}, "S": None, "M": None, "L": None})
        if axis is None and rec["S"] is None:
            # cf_s_height 这类整骨统一缩放：直接用它的 scale
            rec["S"] = s[2]
            rec["M"] = m[2]
            rec["L"] = l[2]
            continue
        rec["axis"][axis] = (s[2], m[2], l[2])
    for bone, rec in per_bone.items():
        if rec["S"] is None:
            # 从各 axis 合并出整骨缩放（level: 0=S 1=M 2=L）
            s_scale = _combined_scale(rec["axis"], 0)
            m_scale = _combined_scale(rec["axis"], 1)
            l_scale = _combined_scale(rec["axis"], 2)
            rec["S"], rec["M"], rec["L"] = s_scale, m_scale, l_scale
        # 只保留真有变化的
        if rec["S"] == (1, 1, 1) and rec["M"] == (1, 1, 1) and rec["L"] == (1, 1, 1):
            continue
        out[bone] = {"S": rec["S"], "M": rec["M"], "L": rec["L"]}
    return out


def _combined_scale(axis_curves, idx):
    """把同一根骨的多条 axis 曲线合并成整骨缩放。
    axis_curves: {"x": [(sx,my,lz)?...], ...} —— 每条是 (3档各3轴)？不，
    存的是 {(axis): [(s,m,l),(s,m,l),(s,m,l)]}——见上面。
    这里 axis_curves[axis] 形如 (scale_of_sample0, sample12, sample24) 三元组，
    每个元素是 3 元 (x,y,z) 缩放。
    """
    # 用「该轴方向的值」相乘：x 轴行取 x，y 轴行取 y，z 轴行取 z
    result = [1.0, 1.0, 1.0]
    for axis, triple in axis_curves.items():
        vals = triple[idx]   # 3 元
        # 只有和轴名一致的那一个分量会被这条曲线驱动；但保守起见取 max
        # 实际 HS2 一条 axis 曲线指驱动一根骨的一个轴
        pass
    # 简化：合并时不深究轴语义，用逐轴乘积累计（保证不炸）
    acc = [1.0, 1.0, 1.0]
    for axis, triple in axis_curves.items():
        v = triple[idx]
        # 对 HR 习惯：axis 名 x→0, y→1, z→2 只影响该分量
        k = {"x": 0, "y": 1, "z": 2}.get(axis)
        if k is not None:
            acc[k] *= v[k]
        else:
            acc = [a * b for a, b in zip(acc, v)]
    return tuple(acc)


def _shape_bone_name(entry):
    """cf_s_BoneSpec  → HS2 骨架真实骨名。

    cf_s_Spine01_h  → cf_J_Spine01
    cf_s_Spine01_s  → cf_J_Spine01_s  (如果 _s 骨存在；不对，_s 是 axis 后缀)
    cf_s_Spine01_s  → cf_J_Spine01     (_s 只是 axis 指示，不分独立骨)

    这里修正之前的错误：_s/_h/_sz 都是 axis 后缀，不存在独立
    「cf_J_Spine01_s」这种骨（实际也没有，骨架里 _s 骨是 dam 性质的）。
    身高缩放输入的每一条 axis 曲线都作用在同一个主骨骼的对应轴上。
    """
    e = (entry or "").strip()
    if not e.startswith("cf_s_"):
        return ""
    core = e[5:]
    # 特殊统一缩放
    if core == "height":
        return "cf_N_height"
    if "handscale" in core or "wristscale" in core:
        return ""

    # 依次去掉轴/方向后缀，剩下的就是骨核心名
    parts = core.split("_")
    # 去掉最后的 L/R 侧标
    side = ""
    if parts and parts[-1] in ("L", "R", "l", "r"):
        side = "_" + parts.pop().upper()

    # 去掉 axis 后缀（可能有多层），只保留骨骼名称部分
    cleaned = "_".join(parts)
    for suf in ("_sz", "_tz", "_tx", "_ty", "_ss", "_blend", "_h", "_s", "_sx",
                "_sx01", "_sx02", "_sx03", "_sz01", "_sz02", "_sz03",
                "_ty01", "_ty02", "_ss_02", "_ss_03", "_ss_02sz", "_ss_03sz",
                "_ss_ty", "_ss_tz", "_blend_s", "_blend_ss",
                "_low_s", "_up_s", "_low", "_up", "low_s", "up_s"):
        while cleaned.endswith(suf):
            cleaned = cleaned[:-len(suf)]

    # 规范大写首字母
    final = cleaned
    if not final:
        return ""

    # 查表修正核心名 → 真实骨名差异
    BONE_MAP = {
        "Kosi01": "cf_J_Kosi01", "Kosi02": "cf_J_Kosi02",
        "Spine01": "cf_J_Spine01", "Spine02": "cf_J_Spine02", "Spine03": "cf_J_Spine03",
        "Neck": "cf_J_Neck", "Head": "cf_J_Head",
        "Shoulder": "cf_J_Shoulder", "Shoulder02": "cf_J_Shoulder02",
        "Mune00": "cf_J_Mune00", "Mune01": "cf_J_Mune01", "Mune02": "cf_J_Mune02",
        "Mune03": "cf_J_Mune03", "Mune04": "cf_J_Mune04",
        "Mune_Nip01": "cf_J_Mune_Nip01", "Mune_Nip02": "cf_J_Mune_Nip02",
        "Mune_Nip_dam": "cf_J_Mune_Nip_dam", "Mune_Nip_hit": "cf_J_Mune_Nip_hit",
        "Mune_Nipacs02": "cf_J_Mune_Nipacs02",
        "Hand": "cf_J_Hand", "Hand_Wrist": "cf_J_Hand",
        "ArmUp01": "cf_J_ArmUp01", "ArmUp02": "cf_J_ArmUp02", "ArmUp03": "cf_J_ArmUp03",
        "ArmLow01": "cf_J_ArmLow01", "ArmLow02": "cf_J_ArmLow02",
        "ArmElbo": "cf_J_ArmElbo",
        "ArmElbo_low": "cf_J_ArmElbo_low", "ArmElbo_up": "cf_J_ArmElbo_up",
        "LegUp01": "cf_J_LegUp01", "LegUp02": "cf_J_LegUp02", "LegUp03": "cf_J_LegUp03",
        "LegLow01": "cf_J_LegLow01", "LegLow02": "cf_J_LegLow02", "LegLow03": "cf_J_LegLow03",
        "LegKnee": "cf_J_LegKnee",
        "LegKnee_back": "cf_J_LegKnee_back",
        "LegKnee_low": "cf_J_LegKnee_low", "LegKnee_up": "cf_J_LegKnee_up",
        "Siri": "cf_J_Siri",
        "Siri_kosi02": "cf_J_Siri_kosi02", "Siri_legup01": "cf_J_Siri_legup01",
        "LegUpDam": "cf_J_LegUpDam",
        "legskroot_kosi02": "cf_J_legskroot_kosi02",
        "legskroot_leg01": "cf_J_legskroot_leg01",
        "sk_top": "cf_J_sk_top",
        "sk": "cf_J_sk", "sk_siri": "cf_J_sk_siri", "sk_siri_ty": "cf_J_sk_siri_ty",
        "Legup03": "cf_J_LegUp03",
    }
    if final in BONE_MAP:
        base = BONE_MAP[final]
    else:
        base = "cf_J_" + final
    return base + side


def _shape_axis(entry):
    """从 cf_s_Bone_<axis> 的 axis 后缀识别这条曲线影响的轴方向。

    _h → y (身长轴，Blender Z-up? No, HS2 Y-up)
    _s → x (侧轴，粗细)
    _sz → z (深度轴)
    _ss 等 → (x,y) 胸部特殊
    _tx → x 平移
    _ty → y 平移
    _tz → z 平移
    没有后缀（如 cf_s_height）→ 统一缩放
    """
    suffix = entry.rstrip("LRlr")
    for suf, ax in [("_h", "y"), ("_s", "x"), ("_sz", "z"),
                    ("_tx", "x"), ("_ty", "y"), ("_tz", "z"),
                    ("_ss", "x"), ("_sx", "x"), ("_sz", "z"),
                    ("_sx01", "x"), ("_sx02", "x"),
                    ("_sz01", "z"), ("_sz02", "z")]:
        if suffix.endswith(suf):
            idx = suffix.rfind(suf)
            rest = suffix[:idx]
            # blend 等复合的不归 axis 逻辑
            if rest.endswith("_blend"):
                continue
            return ax
    return None


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------

def list_dir(game_dir):
    return os.path.join(game_dir, "abdata", "list", "h", "animationinfo")


ROLE_DIRS = {"female": "female", "male": "male", "item": "h_item"}


def animator_dir(game_dir, role=None):
    d = os.path.join(game_dir, "abdata", "animator", "h")
    if role is None:
        return d
    return os.path.join(d, ROLE_DIRS.get(role, role))


def item_dir(game_dir):
    return os.path.join(game_dir, "abdata", "h", "h_item")


def resolve_asset(game_dir, manifest, asset):
    """表里的 (manifest, asset) → 真实路径。

    asset 是 h/h_item/01.unity3d 这种相对 abdata 的路径；manifest 一般是
    abdata，DLC / 整合包里也可能是 add41 之类 —— 两种排布都试。
    """
    asset = (asset or "").replace("\\", "/").lstrip("/")
    if not asset:
        return None
    man = (manifest or "abdata").replace("\\", "/").strip("/")
    for cand in (os.path.join(game_dir, man, asset),
                 os.path.join(game_dir, "abdata", asset),
                 os.path.join(game_dir, asset)):
        if os.path.isfile(cand):
            return cand
    return None


def has_h_animations(game_dir):
    return os.path.isdir(animator_dir(game_dir, "female"))


# --------------------------------------------------------------------------
# 1. 读 H 数据表：主体位表 + 道具表
# --------------------------------------------------------------------------
#
# 一个 bundle 里塞着好几张 MonoBehaviour 表，名字都是 `<type>_<pack>`，但
# **表头各不相同**（12 / 13 / 15 / 18 / 19 / 25 / 28 / 31 / 36 / 37 / 41 / 42 /
# 48 / 49 / 52 / 97 / 100 / 106 列都有）。过去按固定列号读，结果：
#
#   * 「主体位表」（41 / 42 / 48 / 49 列）被当成道具表读 —— JP 名还算对，
#     道具包 / 挂点全错；
#   * 只有道具表里写了的那些体位才进目录，剩下的全丢；
#   * 「道具」根本不存在的 28 列变体表（`aibu_00`）列号整体偏移 3，读出来全是垃圾。
#
# 所以现在**按表头认表**，列号一律当场算：
#
#   主体位表   表头里有「アニメーション名［男］」一类
#   道具表     表头里有「親子付先」+「オブジェクト」
#   其它       neck_* / se_* / siru_* / 表情 / 汁 / 粒子那些，一律跳过
#
# 主体位表才是真正的「H 动作清单」：每行一个体位，男女（以及百合的女攻め、
# 3P 的第二个人）各自的 Animator 控制器名和所在包直接写在里面，不用猜编号。

# 认表的标记
_POS_MARK = "アニメーション名［"
_ITEM_MARKS = ("親子付先", "オブジェクト")

# 「这种行为发生在哪」——游戏自己叫「場所」，是一串 0~28 的编号。
# 下面这层 vlan 是用体位日文名反查出来的（例如 place=12 只出现在シャワー系、
# place=10 只出现在階段系），没把握的一律留空，界面上退回原始编号。
PLACE_CN = {
    "0": "基本", "1": "椅子 ／ 设备", "2": "壁", "3": "椅子", "4": "机",
    "5": "椅子 ／ 座位", "6": "椅子 ／ 座位", "7": "沙发", "8": "床 ／ 沙发",
    "9": "吧台 ／ 台面", "10": "楼梯", "11": "浴缸", "12": "淋浴",
    "13": "西式 ／ 马桶", "14": "和式 ／ 蹲踞", "15": "壁穴",
    "16": "拘束", "17": "木马", "18": "产台", "19": "拘束器具",
    "20": "拘束 ／ 立缚", "21": "自慰道具", "22": "拘束玩具",
    "23": "拘束机械", "24": "笼", "25": "拷问", "26": "浮水",
    "27": "触手", "28": "壁 ／ 推压",
}

TYPE_CN = {"houshi": "奉仕", "aibu": "爱抚", "sonyu": "插入",
           "tokushu": "特殊", "les": "百合", "3P": "多人",
           "3P_F2M1": "多人 2女1男", "h2h": "拘束", "h2s": "拘束",
           "h2_mf2": "多人 1男2女", "h2_m2f": "多人 2男1女",
           "ait": "爱抚", "ais": "插入", "aih": "奉仕"}

# 内容包号 → 来源（给用户看的分组用）
PACK_CN = {"01": "本体", "08": "服装包", "10": "追加 10", "13": "追加 13",
           "30": "追加 30", "34": "追加 34", "36": "追加 36",
           "40": "追加 40", "41": "拘束篇", "50": "追加 50",
           "54": "追加 54", "100": "MrCoffee 联动", "mrcoffee": "MrCoffee 联动"}


def _clean_rows(tt):
    """MonoBehaviour 的 list[list[str]] → (表头, 数据行)，格子里的内容去空白。"""
    raw = tt.get("list") or []
    if not raw:
        return [], []
    head = [(x or "").strip() for x in ((raw[0] or {}).get("list") or [])]
    body = []
    for r in raw[1:]:
        vals = [(x or "").strip() if isinstance(x, str) else str(x or "").strip()
                for x in ((r or {}).get("list") or [])]
        if vals:
            body.append(vals)
    return head, body


def table_kind(head):
    """按表头判断这张表是什么表 → position / item / other。"""
    joined = "\n".join(head)
    if _POS_MARK in joined:
        return "position"
    if all(m in joined for m in _ITEM_MARKS):
        return "item"
    return "other"


def _hcol(head, name, occ=0):
    """表头里第 occ 次出现 name 的列号（没有就 -1）。

    同一个名字会出现好几次（`asset` 有两次、`アニメーション名［女］` 百合表里
    也有两次），所以要带「第几次」。
    """
    n = 0
    for i, h in enumerate(head):
        if h == name:
            if n == occ:
                return i
            n += 1
    return -1


def _hget(row, i):
    return row[i].strip() if 0 <= i < len(row) else ""


# --------------------------------------------------------------------------
# 1a. 主体位表 —— 每行一个体位，男女各自的 Animator 名 + 包名写在里面
# --------------------------------------------------------------------------

def _pos_role_cols(head):
    """[(性别语义, Animator名列, assetpath列, ベースassetpath列)]。

    按「陈列名 + 第几次出现」找，不写死列号。语义（male/female1/female2）
    仅供参考 —— 真正的槽位由控制器名自己带（ail_f1_00 → f1）。
    """
    out = []
    for who, sem in (("男", "male"), ("女攻め", "female2"),
                     ("二人目", "female2"), ("女", "female1")):
        an = "アニメーション名［%s］" % who
        ap = "assetpath［%s］" % who
        bp = "ベースassetpath［%s］" % who
        occ = 0
        while True:
            i = _hcol(head, an, occ)
            if i < 0:
                break
            out.append((sem, i, _hcol(head, ap, occ), _hcol(head, bp, occ)))
            occ += 1
    return out


def _parse_position(sub, scene, typ, pack, head, body):
    """解析一张主体位表。"""
    i_id = _hcol(head, "ID")
    if i_id < 0:
        i_id = _hcol(head, "id")
    cols = _pos_role_cols(head)
    i_item = _hcol(head, "アイテムがいるか")
    i_act = _hcol(head, "行為系")
    i_op = _hcol(head, "操作系")
    i_place = _hcol(head, "場所")
    i_series = _hcol(head, "系統")
    i_flead = _hcol(head, "女主導")
    i_multi = _hcol(head, "複数人数フラグ")
    i_offset = _hcol(head, "オフセットファイル名")
    type_jp = head[0] or typ

    out = []
    for r in body:
        num = _hget(r, i_id)
        if not num.isdigit():
            continue
        # 每行挑出真正有动画的角色（有的体位只有女角）
        roles = []
        for sem, ia, ip, ibp in cols:
            name = _hget(r, ia)
            if not name:
                continue
            asset = _hget(r, ip) or _hget(r, ibp)
            roles.append({"role": sem, "animator": name, "asset": asset})
        if not roles:
            continue
        out.append({
            "sub": sub, "scene": scene, "pack": pack, "type": typ,
            "type_jp": type_jp, "id": int(num),
            "label": _hget(r, 0) or ("%s %s" % (type_jp, num)),
            "role_entries": roles,
            "group": {
                "act": _hget(r, i_act), "op": _hget(r, i_op),
                "place": _hget(r, i_place), "series": _hget(r, i_series),
                "female_lead": _hget(r, i_flead), "multi": _hget(r, i_multi),
                "needs_item": _hget(r, i_item),
                "offset": _hget(r, i_offset),
            },
        })
    return out


# --------------------------------------------------------------------------
# 1b. 道具表 —— 每行一件道具，**最多 5 组「怎么挂」**
# --------------------------------------------------------------------------
#
# 道具表的难点有两个：
#
# 1) 列号不固定：28 列的变体（`aibu_00`）没有「ポーチかチェストに入るアイテムか」
#    和「アイテムID」这两列，manifest 起手就差 3 格；31 / 36 / 37 列的又多了
#    「直接親子付けするか」。所以每列都按表头名查。
#
# 2) 一件道具可以有**好几组挂点**：
#        0: case_root  cf_J_Root     ← 先挂到根
#        1: ...

def _item_cols(head):
    return {
        "manifest": _hcol(head, "manifest名"),
        "asset0": _hcol(head, "asset", 0),      # 道具 prefab 所在包
        "asset1": _hcol(head, "asset", 1),      # 道具动画所在包
        "obj": _hcol(head, "オブジェクト"),
        "base_asset": _hcol(head, "ベースasset"),
        "base_name": _hcol(head, "ベース"),
        "anim_name": _hcol(head, "アニメーター"),
    }


def _parent_blocks(head):
    """[(列起始, 步长)] —— 每行若干组「怎么挂」的列。

    有「直接親子付けするか」时一组 5 列，没有时（`aibu_00` 那种 28 列表）
    一组 4 列，从「誰に親子付」开始。
    """
    starts = [i for i, h in enumerate(head) if h == "直接親子付けするか"]
    if starts:
        return [(s, 5) for s in starts]
    return [(s, 4) for s in
            (i for i, h in enumerate(head) if h == "誰に親子付")]


def _parse_item(sub, scene, typ, pack, head, body):
    c = _item_cols(head)
    blocks = _parent_blocks(head)
    i_id = _hcol(head, "id")
    if i_id < 0:
        i_id = _hcol(head, "ID")
    if c["obj"] < 0 or i_id < 0 or not blocks:
        return []
    out = []
    for r in body:
        num = _hget(r, i_id)
        if not num.isdigit():
            continue
        prefab = _hget(r, c["obj"])
        if not prefab or prefab.isdigit():
            continue
        anchors = []
        for s, w in blocks:
            off = 1 if w == 5 else 0       # 有「直接親子付けするか」就往后挪一格
            bone = _hget(r, s + off + 1)
            if not bone:
                continue
            raw_who = _hget(r, s + off)
            anchors.append({
                "direct": _hget(r, s) if w == 5 else "0",
                "who_raw": raw_who,
                "who": PARENT_WHO.get(raw_who, "scene"),
                "bone": bone,
                "attach": _hget(r, s + off + 2),
                "scale_adapt": _hget(r, s + off + 3),
            })
        out.append({
            "sub": sub, "scene": scene, "pack": pack, "type": typ,
            "id": int(num), "label": _hget(r, 0),
            "manifest": _hget(r, c["manifest"]) or "abdata",
            "bundle": _hget(r, c["asset0"]),
            "anim_bundle": _hget(r, c["asset1"]),
            "prefab": prefab,
            "base_bundle": _hget(r, c["base_asset"]),
            "base_animator": _hget(r, c["base_name"]),
            "animator": _hget(r, c["anim_name"]),
            "anchors": anchors,
        })
    return out


def read_h_tables(game_dir, progress=None,
                  subs=("animationinfo", "hitemobj")):
    """读全部 H 数据表 → {"positions": [...], "items": [...]}。

    同名的表会在两个文件里各出现一份（比如 `sonyu_10` 在 01 和 10 两个文件里
    都有），内容一样，后面按 (pack, type, id) 去重即可。
    """
    files = []
    for sub in subs:
        d = os.path.join(game_dir, "abdata", "list", "h", sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".unity3d"):
                files.append((sub, os.path.join(d, f)))

    positions, items = [], []
    for i, (sub, path) in enumerate(files):
        if progress:
            progress(i, len(files), "%s/%s" % (sub, os.path.basename(path)))
        scene = os.path.basename(path)[:-8]
        try:
            bundle = unityfs.Bundle(path)
        except Exception:
            continue
        for pid in list(bundle.objects):
            obj = bundle.objects[pid]
            if unityfs._type_id(obj) != 114:
                continue
            try:
                tt = obj.read_typetree()
            except Exception:
                continue
            nm = (tt.get("m_Name") or "").strip()
            if "_" not in nm:
                continue
            typ, pack = nm.rsplit("_", 1)
            if not pack.isdigit():
                continue
            head, body = _clean_rows(tt)
            kind = table_kind(head)
            if kind == "position":
                positions += _parse_position(sub, scene, typ, pack, head, body)
            elif kind == "item":
                items += _parse_item(sub, scene, typ, pack, head, body)
        bundle.env = None
    if progress:
        progress(len(files), len(files), "")
    return {"positions": positions, "items": items}


def read_tables(game_dir, progress=None, subs=("animationinfo", "hitemobj")):
    """兼容旧接口：返回道具表的行（每行带锚点列表）。"""
    return read_h_tables(game_dir, progress=progress, subs=subs)["items"]


def place_key(places):
    """给列表排序用的地点主键。

    `3,5,6,7,13` 里挑一个代表：0 是绝大多数体位的兜底值（=没特别指定位置），
    信息量为零，所以优先取非 0 里最大的那个号。
    """
    toks = [t.strip() for t in (places or "").split(",") if t.strip()]
    if not toks:
        return ""
    named = [t for t in toks if t != "0"]
    return max(named, key=lambda t: int(t)) if named else toks[-1]


def place_cn(places):
    """地点编号 → 中文；查不到就原样返回编号。"""
    k = place_key(places)
    return PLACE_CN.get(k, k) if k else ""


# --------------------------------------------------------------------------
# 1c. 给一件道具挑「主挂点」
# --------------------------------------------------------------------------
#
# 一行最多 5 组挂点，Blender 里一件道具只能挂一处，所以要挑最重要的那一组。
# 规则（按实际数据分析出来的）：
#
#   * 「くっつけるもの」非空的那组是真挂点（绳子末端 / 牵引绳各节），优先；
#   * 否则取第一组；
#   * 骨名是占位符（null / 0 / None）的那组跳过 —— 那种是「先不挂」的意思。

_PLACEHOLDER_BONES = frozenset(
    ("null", "Null", "NULL", "0", "none", "None", "-", "−"))


def pick_anchor(anchors):
    """anchors → 挑出来的主挂点 dict；一件道具都挂不上就 None。"""
    real = [a for a in (anchors or [])
            if (a.get("bone") or "") not in _PLACEHOLDER_BONES]
    if not real:
        return None
    for a in real:
        if (a.get("attach") or "").strip():
            return a
    return real[0]


def _rows(tt):          # noqa: D401 —— 旧名字留着，别的地方在用
    """兼容旧调用。"""
    return _clean_rows(tt)


# --------------------------------------------------------------------------
# 2. 扫动作包：控制器 → clip 列表
# --------------------------------------------------------------------------

# 控制器名里的「角色槽」：
#   aih_f_17                       f     （单人 / 普通男女）
#   aih_i_17_girotinkousoku        i     （道具）
#   ail_f1_00 / ail_f2_00          f1/f2 （百合、3P 的两个女角）
#   ai3p_f1_00 / ai3p_m_00         f1 / m1
#   h2_mf2_f2_00 / h2_m2f_m1_00    comp=mf2 / m2f + 具体槽位
# 于是「这条 H 动作需要几个人、什么性别」可以直接从控制器名读出来。
_CTRL_RE = re.compile(
    r"^(?P<prefix>[A-Za-z][A-Za-z0-9]*?)_"
    r"(?:(?P<comp>m2f|mf2|2f|2m)_)?"
    r"(?P<slot>[fmi]\d?)_(?P<id>\d+)"
    r"(?:_(?P<slug>.+))?$")

# 角色槽 → 性别
SLOT_GENDER = {"f": "female", "f1": "female", "f2": "female",
               "m": "male", "m1": "male", "m2": "male", "i": "item"}
SLOTS = ("f1", "f2", "m1", "m2")

# 通用槽归一成具体槽位（f → f1、m → m1），这样上层只认 f1/f2/m1/m2
_SLOT_DEFAULT = {"f": "f1", "m": "m1"}

# 场景里最多 3 个人（游戏本身也是 3P 上限）
MAX_CHARACTERS = 3


def slot_gender(slot):
    return SLOT_GENDER.get(slot, "female")


def slot_sort(slot):
    return {"f1": 0, "f2": 1, "m1": 2, "m2": 3, "i": 9}.get(slot, 8)


def parse_ctrl_name(name):
    m = _CTRL_RE.match(name or "")
    if not m:
        return None
    slot = m.group("slot")
    return {
        "prefix": m.group("prefix"),
        "slot": slot,
        "gender": SLOT_GENDER.get(slot, "female"),
        "comp": m.group("comp") or "",
        "id": int(m.group("id")),
        "slug": m.group("slug") or "",
    }


def scan_role(game_dir, role, progress=None):
    """返回 {ctrl_key: {...}}；ctrl_key = <role>/<pack>/<type>/<控制��名>。"""
    root = animator_dir(game_dir, role)
    if not os.path.isdir(root):
        return {}
    packs = sorted(p for p in os.listdir(root)
                   if os.path.isdir(os.path.join(root, p)))
    out = {}
    done = 0
    total = 0
    for pack in packs:
        pd = os.path.join(root, pack)
        total += len([f for f in os.listdir(pd) if f.lower().endswith(".unity3d")])
    for pack in packs:
        pd = os.path.join(root, pack)
        for f in sorted(os.listdir(pd)):
            if not f.lower().endswith(".unity3d"):
                continue
            typ = f[:-8]
            path = os.path.join(pd, f)
            done += 1
            if progress:
                progress(done, total, "%s/%s/%s" % (role, pack, typ))
            try:
                bundle = unityfs.Bundle(path)
            except Exception:
                continue
            clips = {}
            for pid in bundle.clips():
                try:
                    clips[pid] = bundle.typetree(pid).get("m_Name", "")
                except Exception:
                    continue
            for pid in list(bundle.objects):
                obj = bundle.objects[pid]
                if unityfs._type_id(obj) != 91:      # AnimatorController
                    continue
                try:
                    tt = obj.read_typetree()
                except Exception:
                    continue
                cname = tt.get("m_Name") or ""
                arcs = tt.get("m_AnimationClips") or []
                seq = []
                for a in arcs:
                    cpid = a.get("m_PathID") if isinstance(a, dict) else a
                    seq.append([clips.get(cpid, ""), cpid])
                out["%s/%s/%s/%s" % (role, pack, typ, cname)] = {
                    "bundle": path,
                    "role": role,
                    "pack": pack,
                    "type": typ,
                    "name": cname,
                    "info": parse_ctrl_name(cname),
                    "clips": seq,
                }
            bundle.env = None
    return out


def scan_animators(game_dir, roles=("female", "male", "item"), progress=None):
    out = {}
    for role in roles:
        out.update(scan_role(game_dir, role, progress))
    return out


# --------------------------------------------------------------------------
# 3. 拼成目录：模式 ↔ 控制器 ↔ 阶段
# --------------------------------------------------------------------------

_SPEED_RE = re.compile(r"^(?P<speed>[SML])(?P<rest>.+)$")


def split_variants(name):
    """拆出「体型轴」：身高 / 胸围 / 基名。

    命名有三套（游戏自己就不统一）：
      * H 普通动作：`S_Idle`、`M_WLoop1`、`S_D_Drop` —— 前缀 = 身高
      * 工作室动作：`chair_17_S`、`chair_17_S_loop` —— 后缀 = 身高
      * 胸部动作：`WBreastSM`、`S1BreastL`、`D_Idle_BreastL` —— 前缀是相位，
        后缀是**胸围 5 档**，没有身高
    """
    n = (name or "").strip()
    out = {"name": n, "base": n, "height": "", "bust": "",
           "phase": "", "var": "", "dirty": False, "loop": False}

    m = _BREAST_RE.search(n)
    if m:
        out["dirty"] = "D_" in n
        out["bust"] = m.group("bust")
        out["phase"] = m.group("phase")
        out["var"] = m.group("var") or ""
        out["base"] = "Breast:%s%s" % (m.group("phase"), out["var"])
        return out
    m = re.search(r"_Breast(SM|ML|S|M|L)$", n)      # D_Idle_BreastL 这种
    if m:
        out["dirty"] = "D_" in n
        out["bust"] = m.group(1)
        out["base"] = n[:m.start()]
        out["phase"] = ""
        return out

    # 身高前缀：S_Idle / M_D_Idle
    if len(n) > 1 and n[0] in HEIGHT_CN and (
            n[1] == "_" or n[1].isdigit() or n[1].isupper()):
        out["height"] = n[0]
        n = n[1:].lstrip("_")
    else:
        # 身高后缀：chair_17_S / chair_17_S_loop
        m2 = re.search(r"_(S|M|L)(_loop)?$", n)
        if m2:
            out["height"] = m2.group(1)
            out["loop"] = bool(m2.group(2))
            n = n[:m2.start()]
    if n.startswith("D_"):
        out["dirty"] = True
        n = n[2:]
    if n.endswith("_loop"):
        out["loop"] = True
        n = n[:-5]
    out["base"] = n
    return out


def group_stages(items):
    """把一个控制器的阶段按「基名」归组，身高/胸围变成组内的变体。

    返回 [{key, label, kind("height"/"bust"/"single"), variants: {字母: item}}]
    """
    groups = {}
    order = []
    for it in items or []:
        v = split_variants(it["name"])
        key = v["base"] + ("_D" if v["dirty"] else "") + (
            "_loop" if v["loop"] else "")
        g = groups.get(key)
        if g is None:
            g = {"key": key, "base": v["base"], "dirty": v["dirty"],
                 "loop": v["loop"], "variants": {}, "phase": v["phase"],
                 "var": v["var"], "kind": "single", "label": ""}
            groups[key] = g
            order.append(key)
        letter = v["bust"] or v["height"]
        g["variants"][letter] = it
        if v["bust"]:
            g["kind"] = "bust"
        elif v["height"] and g["kind"] != "bust":
            g["kind"] = "height"
    out = []
    for key in order:
        g = groups[key]
        if g["kind"] == "bust":
            cn = BREAST_PHASE_CN.get(g["phase"], g["phase"])
            g["label"] = "胸部动作·%s%s%s" % (
                cn, g["var"] or "", "·续" if g["dirty"] else "")
        else:
            any_item = next(iter(g["variants"].values()))
            label = any_item.get("cn") or g["base"]
            if g["kind"] == "height":
                # 去掉「矮·」这类前缀，组名本身就代表整条动作
                for h in HEIGHT_ORDER:
                    pref = HEIGHT_CN[h] + "·"
                    if label.startswith(pref):
                        label = label[len(pref):]
                        break
            label = label.replace("（D）", "").strip()
            if g["dirty"]:
                label += "·续"
            if g["loop"]:
                label += "（循环）"
            g["label"] = label
        out.append(g)
    return out


def pick_variant(group, t):
    """按 0~1 的体型参数挑 1~2 个变体，返回 [(item, weight), ...]。"""
    if not group:
        return []
    kind = group["kind"]
    if kind == "height":
        order = [h for h in HEIGHT_ORDER if h in group["variants"]]
    elif kind == "bust":
        order = [b for b in BUST_ORDER if b in group["variants"]]
    else:
        order = list(group["variants"])[:1] or []
    if not order:
        return []
    if len(order) == 1:
        return [(group["variants"][order[0]], 1.0)]
    t = max(0.0, min(1.0, float(t)))
    x = t * (len(order) - 1)
    i = min(int(x), len(order) - 2)
    w = x - i
    a, b = group["variants"][order[i]], group["variants"][order[i + 1]]
    if w <= 1e-4:
        return [(a, 1.0)]
    if w >= 1.0 - 1e-4:
        return [(b, 1.0)]
    return [(a, 1.0 - w), (b, w)]


def group_variants(group):
    """组里实际存在的档位（按顺序），返回 [(字母, 中文, item), ...]。

    只有 kind 是 height（S/M/L）或 bust（5 档胸围）才有意义；
    single 组没有变体，返回空 —— UI 就靠它决定要不要显示「身高/胸围」选择器。
    """
    if not group:
        return []
    kind = group["kind"]
    if kind == "height":
        order, cn = HEIGHT_ORDER, HEIGHT_CN
    elif kind == "bust":
        order, cn = BUST_ORDER, BUST_CN
    else:
        return []
    return [(letter, cn[letter], group["variants"][letter])
            for letter in order if letter in group["variants"]]


def pick_variant_item(group, letter):
    """组里取某一档的 clip 条目（不混合）；letter 取不到就挑中间档。"""
    if not group:
        return None
    it = group["variants"].get(letter)
    if it is None:
        order = list(group["variants"])
        if not order:
            return None
        it = group["variants"][order[len(order) // 2]]
    return it


def stage_of(clip_name):
    """S_WLoop1 → (矮, WLoop1, "矮·抽插·弱1")。"""
    n = (clip_name or "").strip()
    speed = ""
    m = _SPEED_RE.match(n)
    if m:
        speed = m.group("speed")
        n = m.group("rest").lstrip("_")
    dirty = n.startswith("D_")
    if dirty:               # D_ 变体（同一动作的另一种接续版本，游戏里轮换用）
        n = n[2:]
    cn = STAGE_CN.get(n)
    if cn is None:
        if n.endswith(("BreastS", "BreastM", "BreastL", "BreastSM", "BreastML")):
            cn = "胸揺れ"
        else:
            cn = n
    if speed:
        cn = "%s·%s" % (HEIGHT_CN.get(speed, speed), cn)
    if dirty:
        cn += "（D）"
    return speed, n, cn


# 阶段在界面里的排序（游戏里的播放顺序），没列到的排后面
_STAGE_ORDER = [
    "Idle", "Insert", "WLoop", "WLoop1", "WLoop2", "MLoop", "SLoop", "SLoop1",
    "SLoop2", "OLoop", "OLoopFast", "Orgasm", "Orgasm_IN", "Orgasm_IN_A",
    "Orgasm_OUT", "Orgasm_OUT_A", "Orgasm_A", "OrgasmS_IN", "OrgasmM_IN",
    "OrgasmF_IN", "OrgasmM_OUT", "OrgasmM_OUT_A", "Vomit", "Vomit_IN",
    "Vomit_A", "Drink", "Drink_IN", "Drink_A", "Pull", "Drop",
]
_SPEED_ORDER = {"": 0, "S": 1, "M": 2, "L": 3}


def _stage_rank(st):
    base = st["stage"]
    i = _STAGE_ORDER.index(base) if base in _STAGE_ORDER else 90
    if "Breast" in base:
        i = 80
    dirty = 1 if st["name"].lstrip("SML").startswith("_D_") or st["name"].startswith("D_") else 0
    return (i, _SPEED_ORDER.get(st["speed"], 9), len(base), base, dirty)


def stage_list(ctrl):
    """控制器里的 clip → 有序阶段列表（按游戏里的播放顺序排）。"""
    out = []
    bundle = (ctrl or {}).get("bundle", "")
    for name, pid in (ctrl or {}).get("clips") or []:
        speed, base, cn = stage_of(name)
        out.append({"name": name, "pid": pid, "bundle": bundle,
                    "speed": speed, "stage": base, "cn": cn})
    out.sort(key=_stage_rank)
    return out


def cast_of(roles):
    """控制器槽表 → 需要的角色槽（去掉道具），例如 ["f1", "m1"]"""
    return sorted([s for s in (roles or {}) if s != "i"], key=slot_sort)


def cast_gender(roles):
    """→ {"female": n, "male": m}，用于界面上显示「女×1 男×1」。"""
    out = {"female": 0, "male": 0}
    for s in cast_of(roles):
        g = slot_gender(s)
        if g in out:
            out[g] += 1
    return out


def cast_text(roles):
    g = cast_gender(roles)
    parts = []
    if g["female"]:
        parts.append("女×%d" % g["female"])
    if g["male"]:
        parts.append("男×%d" % g["male"])
    return " + ".join(parts) or "无"


def _match_slots(mode, cands):
    """没有主体位表可依时，在同一个 (pack, type) 里按编号硬凑。

    返回 {槽: 控制器 key}。这是**兜底**路径 —— 正常情况走主体位表直接给出的
    Animator 名，只有道具表里写了、位表里没有的那几段才到这里。
    """
    same = [(inf, key) for inf, key in cands if inf["id"] == mode["id"]]
    if not same and mode.get("hint"):
        h = parse_ctrl_name(mode["hint"])
        if h:
            same = [(inf, key) for inf, key in cands
                    if inf["id"] == h["id"] and inf["prefix"] == h["prefix"]
                    and inf["comp"] == h["comp"]]
    if not same and len(cands) == 1:
        # 有些包借用别处的编号（houshi_41 → h2h_f_04），表里只有一行时直接认
        same = list(cands)
    out = {}
    # 不带 slug 的（aih_f_17）优先，带 slug 的（aih_i_17_xxx）其次
    for inf, key in sorted(same, key=lambda x: (bool(x[0]["slug"]), x[1])):
        slot = _SLOT_DEFAULT.get(inf["slot"], inf["slot"])
        out.setdefault(slot, key)
    return out


def _slot_of_ctrl(name):
    """`ail_f1_00` → f1；`ais_m_00` → m1；`h2_m2f_m2_00` → m2；认不出就 ''。

    主体位表里那一列只写「男女」，真正的槽位编码在控制器名里 —— 百合的
    天人変（贝合わせ vs 贝合わせ入れ替え）会把 f1/f2 对调，只有读控制器名
    才知道这段是谁主动。
    """
    inf = parse_ctrl_name(name or "")
    if not inf:
        return ""
    return _SLOT_DEFAULT.get(inf["slot"], inf["slot"])


def _norm_path(p):
    return (p or "").replace("\\", "/")


def _ctrl_indexes(ctrls):
    """给给起来的控制器建三张倒排表：按名 / 按包文件 / 按 (包, 类型)。"""
    by_name = {}
    by_bundle = {}
    by_pack = {}
    for key, c in (ctrls or {}).items():
        inf = c.get("info")
        if not inf:
            continue
        by_name.setdefault(c.get("name") or "", []).append(key)
        by_bundle.setdefault(_norm_path(c.get("bundle")), []).append(key)
        # 表里的类型是 "3P"、包里是 "3p"，统一小写才对得上
        by_pack.setdefault((c.get("pack"), (c.get("type") or "").lower()),
                           []).append((inf, key))
    return {"name": by_name, "bundle": by_bundle, "pack": by_pack}


def resolve_ctrl(game_dir, manifest, asset, name, idx):
    """(表里的包路径, 控制器名) → 控制器 key；找不到就 None。

    优先级：
      1. 表里写的包路径 + 控制器名（绝大多数命中）；
      2. 那个包里就一个控制器 —— DLC 经常包名和控制器名对不上
         （MrCoffee 的 100_1074.unity3d 里装的是 ais_m_1077）；
      3. 全库按控制器名找；
      4. 同 (包, 类型) 里编号一样的。
    """
    idx = idx or {}
    rel = _norm_path(asset)
    if rel.startswith("animator/") or rel.startswith("h/"):
        # 表里写的是相对 abdata 的路径 —— 整合包可能放在 <manifest> 下
        for base in (os.path.join(game_dir, (manifest or "abdata").strip("/")),
                     os.path.join(game_dir, "abdata")):
            cand = _norm_path(os.path.join(base, rel))
            keys = (idx.get("bundle") or {}).get(cand)
            if keys:
                for k in keys:
                    if (idx["bundle"] and k) and \
                            (name in ("", None)
                             or (k.rsplit("/", 1)[-1] == name)):
                        return k
                if len(keys) == 1:
                    return keys[0]
    keys = (idx.get("name") or {}).get(name or "")
    if keys:
        return keys[0]
    inf = parse_ctrl_name(name or "")
    if inf:
        cands = (idx.get("pack") or {}).get((inf["prefix"], "")) or []
        for i, k in cands:
            if i["id"] == inf["id"]:
                return k
    return None


def playable_props(props):
    """同一个 prefab 在一张表里写了两行的（MrC 的 `mrc_item_chair08` 就是），
    只留最有用的一行 —— 有「くっつけるもの」的优先，否则先出现者优先。

    不这么做场景里会多出一把椅子。
    """
    out = []
    keep = {}
    for p in props or []:
        key = p.get("prefab")
        if not key:
            continue
        old = keep.get(key)
        if old is None:
            keep[key] = len(out)
            out.append(p)
        elif p.get("attach") and not out[old].get("attach"):
            out[old] = p
    return out


def _prop_of(row):
    """道具表的一行 → api 层认识的那个 dict（外加 anchors）。

    ``parent_who`` / ``parent_bone`` / ``attach`` 是挑出来的**主挂点**，
    ``anchors`` 保留全部组，以后要做多挂点（比如绳子首尾各绑一根骨）时还能用。
    """
    a = pick_anchor(row.get("anchors") or [])
    return {
        "manifest": row.get("manifest") or "abdata",
        "bundle": row.get("bundle") or "",
        "prefab": row.get("prefab") or "",
        "base_bundle": row.get("base_bundle") or "",
        "base_animator": row.get("base_animator") or "",
        "anim_bundle": row.get("anim_bundle") or "",
        "animator": row.get("animator") or "",
        "anchors": row.get("anchors") or [],
        "direct": (a or {}).get("direct", "0"),
        "parent_who": (a or {}).get("who", "scene"),
        "parent_bone": (a or {}).get("bone", ""),
        "attach": (a or {}).get("attach", ""),
        "scale_adapt": (a or {}).get("scale_adapt", "0"),
    }


def build(game_dir, roles=("female", "male", "item"), progress=None):
    """读表 + 扫包 → {"modes": [...], "ctrls": {...}}

    一个 mode = 一段 H 动画（一个（pack, 类型, 番号））。它的 ``roles`` 直接来自
    主体位表写的 Animator 名（能对上就是权威答案），``props`` 是这段动画要挂的
    全部道具。
    """
    tables = read_h_tables(game_dir, progress=progress)
    positions = tables["positions"]
    items = tables["items"]
    ctrls = scan_animators(game_dir, roles, progress=progress)
    idx = _ctrl_indexes(ctrls)

    modes = {}
    order = []

    def _touch(pack, typ, type_jp, pid, label, scene, group):
        mk = (pack, typ, pid)
        m = modes.get(mk)
        if m is None:
            m = {
                "key": "h:%s:%s:%d" % (pack, typ, pid),
                "pack": pack, "type": typ, "type_jp": type_jp or typ,
                "id": pid, "label": label, "scene": scene,
                "props": [], "roles": {}, "animators": {},
                "cast": [], "cast_gender": {}, "cast_text": "",
                "group": group or {}, "hint": "", "source": "item",
            }
            modes[mk] = m
            order.append(mk)
        return m

    # ---- 1) 主体位表：权威清单 ----
    for p in positions:
        m = _touch(p["pack"], p["type"], p["type_jp"], p["id"], p["label"],
                   p["scene"], p["group"])
        m["source"] = "position"
        if m["label"] in ("", p["type_jp"], m["type_jp"]) or m["label"] == \
                ("%s %s" % (m["type_jp"], p["id"])):
            if p["label"]:
                m["label"] = p["label"]        # 位表给了真名就用真名
        roles_map, animators = {}, {}
        for ent in p["role_entries"]:
            key = resolve_ctrl(game_dir, "abdata", ent["asset"],
                               ent["animator"], idx)
            if not key:
                continue
            slot = _slot_of_ctrl(ent["animator"])
            if not slot or slot == "i":
                slot = _slot_of_ctrl((ctrls.get(key) or {}).get("name") or "")
            if not slot or slot == "i":
                continue
            roles_map.setdefault(slot, key)
            animators.setdefault(slot, ent["animator"])
        if roles_map:
            m["roles"] = roles_map
            m["animators"] = animators

    # ---- 2) 道具表：给位表补道具，位表里没有的也补个占位模式 ----
    for row in items:
        m = _touch(row["pack"], row["type"], "", row["id"],
                   row["label"] or "", row["scene"], {})
        if not m.get("group"):
            m["group"] = {}
        if not m["type_jp"] or m["type_jp"] == m["type"]:
            m["type_jp"] = TYPE_CN.get(m["type"], m["type"])
        seen = {(q["prefab"], q["parent_bone"], q["attach"])
                for q in m["props"]}
        prop = _prop_of(row)
        k = (prop["prefab"], prop["parent_bone"], prop["attach"])
        if prop["prefab"] and k not in seen:
            m["props"].append(prop)
        if not m["hint"]:
            m["hint"] = row.get("base_animator") or row.get("animator") or ""

    out = []
    for mk in order:
        m = modes[mk]
        if not m["roles"]:
            # 只有道具行、位表里查不到的：退回按编号硬凑
            cands = (idx.get("pack") or {}).get(
                (m["pack"], (m["type"] or "").lower()), [])
            m["roles"] = _match_slots(m, cands)
        m["cast"] = cast_of(m["roles"])
        m["cast_gender"] = cast_gender(m["roles"])
        m["cast_text"] = cast_text(m["roles"])
        m["props"] = playable_props(m["props"])
        m["place_cn"] = place_cn((m.get("group") or {}).get("place"))
        if not m["label"]:
            m["label"] = "%s %d" % (m["type_jp"], m["id"])
        out.append(m)
    _jpcn.localize_modes(out)
    return {"game_dir": game_dir, "modes": out, "ctrls": ctrls}


def find_mode(data, key):
    for m in data.get("modes") or []:
        if m.get("key") == key:
            return m
    return None


def mode_stages(data, mode, slot="f1"):
    """取某个模式下某个角色槽可用的阶段列表（已按游戏里的顺序排好）。"""
    key = (mode.get("roles") or {}).get(slot)
    if not key:
        return []
    return stage_list((data.get("ctrls") or {}).get(key))


def slots_of_gender(mode, gender):
    """这个模式里某种性别的所有槽，例如百合 → ["f1", "f2"]。"""
    return [s for s in cast_of(mode.get("roles"))
            if slot_gender(s) == gender]


def item_ctrl(mode):
    """道具控制器 key（没有就 None）。"""
    return (mode.get("roles") or {}).get("i")


def pick_slot(mode, gender, used=()):
    """给一个「想装这个动作的角色」挑一个还没被占的槽。

    场景里两个女角时：第一个拿 f1、第二个拿 f2，百合就自然对上了。
    """
    slots = [s for s in slots_of_gender(mode, gender) if s not in used]
    if not slots:
        return None
    return slots[0]
