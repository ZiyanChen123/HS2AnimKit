"""解析 MMDD 的 chara_*.ini（HS2 舞蹈插件的角色骨骼配置）。

MMDD 的做法：在 HS2 骨架上**造一棵虚拟的 MMD 骨架**，VMD 驱动虚拟骨，
再按 BoneWeight 把虚拟骨的世界变换同步回 HS2 实骨。这套配置就是那棵
虚拟骨架的定义，我们照着复刻一份。

ini 里几个关键段
----------------
    [General]        AxisScale / DefWeightSyncMode / HeightBone
    [BoneDefine]     NAME=parent:vmd骨名:属性[:ik][:track][:limit][:localaxis]
                     属性 [rpitla]: r=rotation p=position i=ik t=track
                                    l=limit a=localaxis
    [BonePosition]   NAME=b:骨名 | bbs:骨A:骨B:比例 | bo:骨名:x,y,z | br:...
    [BoneWeight]     NAME=HS2实骨名[:l|local|g|global]
    [BoneIKSet_N]    IKBONE / SOLVER / ITERATIONS / STEP_ANGLE / LIMITS
    [BoneTwistDisperse]  扭转分散
    [BoneRotAxisAdjust]  应用 VMD 旋转前/后的轴修正（度，欧拉）
    [PrepareBoneBeforeCreateDummyBone]  给 HS2 骨预设的旋转（度）
"""

import configparser
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(os.path.dirname(_HERE))

# 配置搜索顺序：先找**包内**自带的（安装版 / zip 都能用），
# 再退回开发目录 _ref/mmdd/mmdd（源码副本）。
_CONFIG_DIRS = [
    os.path.join(_HERE, "mmd_configs"),
    os.path.join(_PROJ, "_ref", "mmdd", "mmdd"),
]


def _find_dir():
    for d in _CONFIG_DIRS:
        if os.path.isdir(d) and any(f.endswith(".ini") for f in os.listdir(d)):
            return d
    return _CONFIG_DIRS[0]


REF_DIR = _find_dir()

# MMDD v2.2.1 的 vmdlib_neov2.ini = 「Default charactor bone v2.0 (TDA spec)」，
# 是官方最完整的躯干/IK/D 骨/キャンセル层级 —— 作为默认配置。
DEFAULT_INI = "chara_default_2.0_TDA.ini"


class BoneDef(object):
    __slots__ = ("name", "parent", "vmd", "attr", "ik", "track", "limit",
                 "localaxis", "pos", "weight", "weight_mode")

    def __init__(self, name):
        self.name = name
        self.parent = ""
        self.vmd = ""
        self.attr = ""
        self.ik = None            # (末端, 中间..., 根)
        self.track = None         # (比例, 目标骨, 模式 "r"/"p"/"rp")
        self.limit = None         # (x, y, z) 欧拉四元数
        self.localaxis = None
        self.pos = None           # (type, value)
        self.weight = None        # HS2 实骨名
        self.weight_mode = "global"

    @property
    def has_rot(self):
        return "r" in self.attr

    @property
    def has_pos(self):
        return "p" in self.attr

    @property
    def has_ik(self):
        return "i" in self.attr


class IKSet(object):
    __slots__ = ("name", "enable", "bones", "solver", "iterations",
                 "step_angle", "min_knee_rot", "limits", "control_weight",
                 "min_delta", "last_frame_weight", "ik_rotation_weight",
                 "use_leg", "solve_method")

    def __init__(self):
        self.name = ""
        self.enable = True
        self.bones = []
        self.solver = "MMDIKSolver"
        self.iterations = 6
        self.step_angle = 114.5916
        self.min_knee_rot = 1.0
        self.limits = None
        self.control_weight = 1.0
        self.min_delta = 0.001
        self.last_frame_weight = 1.0
        self.ik_rotation_weight = 1.0
        self.use_leg = True
        self.solve_method = "Solve"


class CharaConfig(object):
    """一份 chara 配置。"""

    def __init__(self, path=None):
        self.path = path or os.path.join(REF_DIR, DEFAULT_INI)
        self.name = os.path.basename(self.path)
        self.general = {}
        self.bones = {}              # {NAME: BoneDef}
        self.order = []              # 按定义顺序（层级已经保证父在前）
        self.ik_sets = []
        self.twist = {}              # {key: (kind, a, b, ratio)}
        self.rot_adjust = {}         # {NAME: [(mode, x, y, z)]}
        self.prepare = {}            # {HS2骨名: (x, y, z) 度}
        self.axis_scale = 0.832
        self.def_sync = "global"
        self.height_bone = "cf_N_height"
        self._load()

    # ------------------------------------------------------------------
    def _load(self):
        cp = configparser.ConfigParser(strict=False, delimiters=("=",),
                                       comment_prefixes=(";", "#"))
        cp.optionxform = str          # 骨名大小写敏感
        try:
            cp.read(self.path, encoding="utf-8-sig")
        except Exception:
            return

        if cp.has_section("General"):
            self.general = dict(cp["General"])
            self.axis_scale = float(self.general.get("AxisScale", 0.832))
            self.def_sync = (self.general.get("DefWeightSyncMode") or
                             "global").lower()
            self.height_bone = self.general.get("HeightBone", "cf_N_height")

        if cp.has_section("BoneDefine"):
            for name, raw in cp["BoneDefine"].items():
                self._parse_define(name.strip(), raw.strip())

        if cp.has_section("BonePosition"):
            for name, raw in cp["BonePosition"].items():
                b = self.bones.get(name.strip())
                if b is not None:
                    b.pos = self._parse_pos(raw.strip())

        if cp.has_section("BoneWeight"):
            for name, raw in cp["BoneWeight"].items():
                b = self.bones.get(name.strip())
                if b is None:
                    continue
                parts = [p.strip() for p in raw.split(":") if p.strip()]
                if not parts:
                    continue
                b.weight = parts[0]
                if len(parts) > 1:
                    m = parts[1].lower()
                    b.weight_mode = "local" if m in ("l", "local") else "global"

        # IK 设置
        n = 0
        try:
            n = int(cp.get("BoneIKSetOverview", "IK_SET_NUMBER", fallback=0))
        except Exception:
            n = 0
        for i in range(1, n + 1):
            sec = "BoneIKSet_%d" % i
            if not cp.has_section(sec):
                continue
            s = IKSet()
            d = cp[sec]
            s.name = d.get("NAME", sec)
            s.enable = d.get("ENABLE", "1").strip() not in ("0", "false", "False")
            s.bones = [x.strip() for x in d.get("IKBONE", "").split(",")
                       if x.strip()]
            s.solver = d.get("SOLVER", "MMDIKSolver").strip()
            try:
                s.iterations = int(d.get("ITERATIONS", 6))
            except Exception:
                pass
            try:
                s.step_angle = float(d.get("STEP_ANGLE", 114.5916))
            except Exception:
                pass
            try:
                s.min_knee_rot = float(d.get("MIN_KNEE_ROT", 1))
            except Exception:
                pass
            for attr, key, typ in (("control_weight", "CONTROL_WEIGHT", float),
                                   ("min_delta", "MIN_DELTA", float),
                                   ("last_frame_weight", "LAST_FRAME_WEIGHT", float),
                                   ("ik_rotation_weight", "IK_ROTATION_WEIGHT", float)):
                try:
                    setattr(s, attr, typ(d.get(key, getattr(s, attr))))
                except Exception:
                    pass
            s.use_leg = str(d.get("USE_LEG", "1")).strip().lower() in ("1", "true")
            s.solve_method = (d.get("SOLVE_METHOD", "Solve") or "Solve").strip()
            raw = d.get("LIMITS", "")
            s.limits = self._parse_limits(raw)
            self.ik_sets.append(s)

        # 扭转分散
        if cp.has_section("BoneTwistDisperse"):
            for k, raw in cp["BoneTwistDisperse"].items():
                parts = [p.strip() for p in raw.split(":")]
                if len(parts) >= 4:
                    try:
                        self.twist[k.strip()] = (parts[0], parts[1], parts[2],
                                                 float(parts[3]))
                    except Exception:
                        pass

        # 旋转轴修正
        if cp.has_section("BoneRotAxisAdjust"):
            for k, raw in cp["BoneRotAxisAdjust"].items():
                items = []
                for tok in raw.split(","):
                    tok = tok.strip()
                    if not tok:
                        continue
                    try:
                        items.append(tok)
                    except Exception:
                        pass
                # 形如 r,0,0,-32 或 s,1.5
                parsed = []
                mode = "r"
                buf = []
                for tok in items:
                    try:
                        buf.append(float(tok))
                    except ValueError:
                        if buf and mode:
                            parsed.append((mode, tuple(buf)))
                        mode = tok
                        buf = []
                if buf:
                    parsed.append((mode, tuple(buf)))
                self.rot_adjust[k.strip()] = parsed

        # 建虚拟骨前给 HS2 骨的预设旋转
        if cp.has_section("PrepareBoneBeforeCreateDummyBone"):
            for k, raw in cp["PrepareBoneBeforeCreateDummyBone"].items():
                k = k.strip()
                if k.startswith("SETTING_"):
                    continue
                try:
                    t = raw.strip().strip("()")
                    self.prepare[k] = tuple(float(x) for x in t.split(","))
                except Exception:
                    pass

    # ------------------------------------------------------------------
    def _parse_define(self, name, raw):
        parts = raw.split(":")
        b = BoneDef(name)
        b.parent = parts[0].strip() if len(parts) > 0 else ""
        b.vmd = parts[1].strip() if len(parts) > 1 else ""
        b.attr = parts[2].strip() if len(parts) > 2 else ""
        for extra in parts[3:]:
            extra = extra.strip()
            if not extra:
                continue
            head = extra.split(",")[0].strip().lower()
            vals = [x.strip() for x in extra.split(",")[1:]]
            if head == "i":                       # ik: 末端 -> 根
                b.ik = vals
            elif head.startswith("t"):            # track: tr / tp / trp，比例, 目标
                try:
                    modes = head[1:]
                    b.track = (float(vals[0]), vals[1] if len(vals) > 1 else "",
                               modes)
                except Exception:
                    pass
            elif head == "l":                     # limitAxis: refx,refy,refz,lmtx,lmty,lmtz
                # vmdlib: 旋转先拆成角度轴；若旋转轴与 refAx 反向则角度取反，
                # 再绕 lmtAx 重新施回（手臂扭转骨用，见 mmdrig._limit_axis）。
                try:
                    v = [float(x) for x in vals[:6]]
                    if len(v) == 6:
                        b.limit = ((v[0], v[1], v[2]), (v[3], v[4], v[5]))
                except Exception:
                    pass
            elif head == "la":                    # localaxis
                b.localaxis = vals
        self.bones[name] = b
        self.order.append(name)

    @staticmethod
    def _parse_pos(raw):
        """b:骨名 / bbs:A:B:比例 / bo:骨名:x,y,z / br:骨名:x,y,z"""
        parts = raw.split(":")
        kind = parts[0].strip()
        rest = [p.strip() for p in parts[1:]]
        if kind == "b" and rest:
            return ("b", (rest[0],))
        if kind == "bbs" and len(rest) >= 3:
            try:
                return ("bbs", (rest[0], rest[1], float(rest[2])))
            except Exception:
                return None
        if kind in ("bo", "br") and len(rest) >= 2:
            try:
                xyz = tuple(float(v) for v in rest[1].split(","))
                return (kind, (rest[0], xyz))
            except Exception:
                return None
        return None

    @staticmethod
    def _parse_limits(raw):
        """(((0.5, 180), (0, 0), (0, 0)), None)
        → [[(0.5, 180), (0, 0), (0, 0)], None]

        这串东西本身就是合法的 Python 元组字面量，直接 literal_eval 最稳 ——
        手写的括号分组会把 (0.5,180) 吃成 (0,0)。
        """
        import ast
        raw = (raw or "").strip()
        if not raw or raw.lower() == "none":
            return None
        try:
            val = ast.literal_eval(raw)
        except Exception:
            return None
        out = []
        for item in (val if isinstance(val, tuple) else (val,)):
            if item is None:
                out.append(None)
                continue
            axes = []
            for ax in item:
                if ax is None:
                    axes.append(None)
                elif isinstance(ax, (tuple, list)) and len(ax) == 2:
                    axes.append((float(ax[0]), float(ax[1])))
                else:
                    axes.append(None)
            out.append(axes or None)
        return out or None

    # ------------------------------------------------------------------
    def vmd_to_bone(self):
        """{MMD 骨名: BoneDef}"""
        return {b.vmd: b for b in self.bones.values() if b.vmd}

    def ik_bone_map(self):
        """{IK 骨名: IKSet}"""
        m = {}
        for s in self.ik_sets:
            for n in s.bones:
                m[n] = s
        return m

    def parents_first(self):
        """按「父在前」的顺序返回骨名（ini 已经是这个顺序，这里做一次校验）。"""
        out = []
        seen = set()
        for n in self.order:
            b = self.bones[n]
            if b.parent and b.parent not in seen and b.parent in self.bones:
                # 父还没排进来 —— 先补父
                stack = []
                cur = b.parent
                while cur and cur not in seen and cur in self.bones:
                    stack.append(cur)
                    cur = self.bones[cur].parent
                for x in reversed(stack):
                    out.append(x)
                    seen.add(x)
            if n not in seen:
                out.append(n)
                seen.add(n)
        return out


def list_configs():
    """可用的 chara 配置（名字 -> 路径）。"""
    if not os.path.isdir(REF_DIR):
        return []
    out = []
    for f in sorted(os.listdir(REF_DIR)):
        if f.lower().endswith(".ini"):
            out.append((f, os.path.join(REF_DIR, f)))
    return out
