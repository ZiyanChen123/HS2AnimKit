"""VMD（MikuMikuDance 动作）解析 + MMD → HS2 的骨骼映射。

VMD 布局（little endian）
------------------------
    30  signature "Vocaloid Motion Data 0002\\0"
    20  model name
     4  bone frame count
       per frame: 15 name + 4 frame + 12 pos + 16 quat + 64 interp  = 111
     4  morph frame count      per frame: 15 name + 4 frame + 4 weight = 23
     4  camera count           61 bytes each（不用）
     4  light count            28 bytes each
     4  self shadow count      9 bytes each
     4  IK frame count         274 bytes each

MMD 和 HS2 都是「纯枢轴骨架」：骨骼没有独立的静止朝向，动作里的四元数就是
相对父级的旋转。所以 VMD 的骨骼旋转可以直接当 HS2 的局部 basis 用，
只需要转换坐标系（MMD Y-up 左手 → Blender Z-up 右手）。

**插值**：VMD 每个关键帧带 4 条贝塞尔曲线（位置 X/Y/Z、旋转）。MMDD 读
VMD 后是按这些曲线逐帧采样的，不是线性插值。之前这里直接 slerp/lerp，
关键帧稀疏时（舞蹈常见 10~20 帧一个 key）和游戏里的落点差几厘米、转几度，
这也是「精度达不到游戏插件」的一个主要来源。
"""

import math
import struct

from mathutils import Quaternion

from .ripper import mathutil as mu

# MMD 骨骼基名 -> HS2 骨骼（左右由 前缀 右/左 或后缀 .L/.R 决定）
#
# MMD 标准骨架的躯干层级是：
#     グルーブ
#     └ 腰                    ← 上半身和下半身的共同父级
#        ├ 上半身 → 上半身2 → 首 → 頭
#        └ 下半身 → 足
#
# HS2 的层级（实测）：
#     cf_J_Hips                ← 同时带腿和上半身 → 对应 MMD 的「腰」
#     ├── cf_J_Kosi01 -> Kosi02 -> 大腿     ← 只带腿 → 对应「下半身」
#     └── cf_J_Spine01 -> ... -> Neck -> Head   ← 对应「上半身」
BASE_MAP = {
    "センター": "cf_J_Root", "center": "cf_J_Root",
    "腰": "cf_J_Hips",
    "下半身": "cf_J_Kosi01",
    "上半身": "cf_J_Spine01", "上半身2": "cf_J_Spine02", "上半身3": "cf_J_Spine03",
    "首": "cf_J_Neck", "頭": "cf_J_Head",
    "肩": "cf_J_Shoulder_{S}", "腕": "cf_J_ArmUp00_{S}",
    "ひじ": "cf_J_ArmLow01_{S}", "手首": "cf_J_Hand_{S}",
    "足": "cf_J_LegUp00_{S}", "ひざ": "cf_J_LegLow01_{S}",
    "足首": "cf_J_Foot01_{S}", "つま先": "cf_J_Foot02_{S}",
    "人指１": "cf_J_Hand_Index01_{S}", "人指２": "cf_J_Hand_Index02_{S}",
    "人指３": "cf_J_Hand_Index03_{S}",
    "中指１": "cf_J_Hand_Middle01_{S}", "中指２": "cf_J_Hand_Middle02_{S}",
    "中指３": "cf_J_Hand_Middle03_{S}",
    "薬指１": "cf_J_Hand_Ring01_{S}", "薬指２": "cf_J_Hand_Ring02_{S}",
    "薬指３": "cf_J_Hand_Ring03_{S}",
    "小指１": "cf_J_Hand_Little01_{S}", "小指２": "cf_J_Hand_Little02_{S}",
    "小指３": "cf_J_Hand_Little03_{S}",
    "親指１": "cf_J_Hand_Thumb01_{S}", "親指２": "cf_J_Hand_Thumb02_{S}",
    "親指３": "cf_J_Hand_Thumb03_{S}",
}

# 没有对应骨、但旋转必须保留的：把旋转**乘**到目标骨上（不是覆盖）。
MERGE_MAP = {}

# 「腰キャンセル」：MMD 里有专门的 腰キャンセル左右 骨，作用是**抵消腰的旋转**，
# 让腿不跟着腰一起转。
CANCEL_MAP = {"下半身": "腰"}

# 只有根骨用 VMD 里的位置；其它骨的 pos 一律忽略 ——
# VMD 的骨位移多来自 MMD 侧 IK 解算，直接搬到 HS2 会把骨节拉开。
POS_BONES = ("cf_J_Root",)

# 明确忽略的（IK / 扭转 / 物理 / 饰品）
SKIP_BASES = ("足ＩＫ", "つま先ＩＫ", "腕捩", "手捩", "ひじ補助", "足補", "足先",
              "足D", "ダミー", "肩C", "肩P", "目", "両目", "舌", "髪", "胸",
              "スカート", "リボン", "メガネ", "猫耳", "しっぽ", "ペンダント",
              "ヘッドセット", "IDカード", "呆毛", "もみあげ", "パーツ", "操作中心",
              "親指０", "下半身E", "全ての親", "グルーブ")

DEFAULT_CURVE = (0.2, 0.2, 0.8, 0.8)


# --------------------------------------------------------------------------
# 采样：MMD 的贝塞尔插值（和 MMDD vmdlib 的 mmdCubicBezierInterpolate 一致）
# --------------------------------------------------------------------------

def _sample_bezier_xy(h1, h2, t):
    """三次贝塞尔在参数 t 处的 (x, y)；端点是 (0,0) 和 (1,1)。"""
    u = 1.0 - t
    x = 3.0 * u * u * t * h1[0] + 3.0 * u * t * t * h2[0] + t * t * t
    y = 3.0 * u * u * t * h1[1] + 3.0 * u * t * t * h2[1] + t * t * t
    return x, y


def mmd_bezier(curve, x):
    """复刻 MMDD vmdlib.mmdCubicBezierInterpolate。

    curve = (x1, y1, x2, y2)，输入 x∈[0,1]，返回 y。
    官方实现是「二分参数 t，最多 10 次，容差 0.001」，不是解析解。
    这里按同样做法，保证和游戏里逐帧采样的数值一致（包括它的截断误差）。
    """
    p1x, p1y, p2x, p2y = curve
    h1 = (p1x, p1y)
    h2 = (p2x, p2y)
    xtgt = x
    x0, x1, t = 0.0, 1.0, x
    last_y = 0.0
    for _ in range(10):
        vx, vy = _sample_bezier_xy(h1, h2, t)
        last_y = vy
        if abs(xtgt - vx) < 0.001:
            break
        if vx > xtgt:
            x1 = t
        else:
            x0 = t
        t = (x0 + x1) * 0.5
    return last_y


def _is_linear(curve):
    return curve[0] == curve[1] and curve[2] == curve[3]


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

def _dec(b):
    return b.split(b"\x00")[0].decode("shift_jis", "replace").strip()


def _dec_ik(b):
    s = b.split(b"\x00")[0].decode("shift_jis", "replace").strip()
    return s


def parse(path):
    """返回 {'bones': {name: [(frame, pos, quat, curves)]}, 'morphs', 'ik', ...}

    curves = ((x1,y1,x2,y2), (x1,y1,x2,y2), (x1,y1,x2,y2), (x1,y1,x2,y2))
    分别对应位置 X/Y/Z、旋转。
    """
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"Vocaloid Motion Data"):
        raise ValueError("不是 VMD 文件")

    off = 30
    model = _dec(data[off:off + 20])
    off += 20
    n = struct.unpack("<I", data[off:off + 4])[0]
    off += 4

    bones = {}
    max_frame = 0
    for _ in range(n):
        if off + 111 > len(data):
            raise ValueError("VMD 骨骼帧数据不完整（%d/%d）" % (_, n))
        name = _dec(data[off:off + 15])
        frame = struct.unpack("<I", data[off + 15:off + 19])[0]
        px, py, pz = struct.unpack("<3f", data[off + 19:off + 31])
        qx, qy, qz, qw = struct.unpack("<4f", data[off + 31:off + 47])
        # 64 字节插值：16 个 32bit 小端整数，每个的低字节是有效控制点。
        # 每 4 个一组（第 0/4/8/12 字节）：X/Y/Z/R 通道各自的 (x1,y1,x2,y2)。
        raw = struct.unpack("<16I", data[off + 47:off + 111])
        b = [float(v & 0xFF) / 127.0 for v in raw]
        curves = (tuple(b[0:4]), tuple(b[4:8]), tuple(b[8:12]), tuple(b[12:16]))
        off += 111
        bones.setdefault(name, []).append((frame, (px, py, pz),
                                           (qx, qy, qz, qw), curves))
        if frame > max_frame:
            max_frame = frame

    for keys in bones.values():
        keys.sort(key=lambda k: k[0])

    morphs = {}
    if off + 4 <= len(data):
        mn = struct.unpack("<I", data[off:off + 4])[0]
        off += 4
        for _ in range(mn):
            if off + 23 > len(data):
                break
            name = _dec(data[off:off + 15])
            frame = struct.unpack("<I", data[off + 15:off + 19])[0]
            w = struct.unpack("<f", data[off + 19:off + 23])[0]
            off += 23
            morphs.setdefault(name, []).append((frame, w))

    # 跳过镜头 / 灯光 / 阴影，读 IK/可见帧
    iks = []
    try:
        if off + 4 > len(data):
            raise ValueError
        cam_n = struct.unpack("<I", data[off:off + 4])[0]
        off += 4 + cam_n * 61
        if off + 4 > len(data):
            raise ValueError
        light_n = struct.unpack("<I", data[off:off + 4])[0]
        off += 4 + light_n * 28
        if off + 4 > len(data):
            raise ValueError
        shadow_n = struct.unpack("<I", data[off:off + 4])[0]
        off += 4 + shadow_n * 9
        if off + 4 > len(data):
            raise ValueError
        ik_n = struct.unpack("<I", data[off:off + 4])[0]
        off += 4
        for _ in range(ik_n):
            if off + 9 > len(data):
                break
            frame = struct.unpack("<I", data[off:off + 4])[0]
            off += 4
            visible = bool(data[off])
            off += 1
            count = struct.unpack("<I", data[off:off + 4])[0]
            off += 4
            entries = {}
            for _k in range(count):
                if off + 21 > len(data):
                    break
                nm = _dec_ik(data[off:off + 20])
                off += 20
                entries[nm] = bool(data[off])
                off += 1
            iks.append((frame, visible, entries))
        iks.sort(key=lambda k: k[0])
    except Exception:
        pass

    return {"model": model, "bones": bones, "morphs": morphs, "ik": iks,
            "frames": max_frame, "count": n}


# --------------------------------------------------------------------------
# 采样
# --------------------------------------------------------------------------

def sample(keys, frame):
    """按 VMD 曲线采样一帧，返回 (pos, quat)。

    - 帧在首/尾之外：取首/尾关键帧（和 MMDD getVmdBoneFrame 一致）
    - 正好落在关键帧上：直接取该帧（不插值）
    - 其余：位置三轴各自贝塞尔、旋转按 r 曲线 slerp
    """
    if not keys:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)
    if frame <= keys[0][0]:
        return keys[0][1], keys[0][2]
    if frame >= keys[-1][0]:
        return keys[-1][1], keys[-1][2]

    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid][0] <= frame:
            lo = mid
        else:
            hi = mid
    f0, p0, q0, _c0 = keys[lo]
    f1, p1, q1, c1 = keys[hi]
    if frame == f0:
        return p0, q0
    if f1 == f0:
        return p1, q1
    t = (frame - f0) / float(f1 - f0)
    cx, cy, cz, cr = c1
    tx = t if _is_linear(cx) else mmd_bezier(cx, t)
    ty = t if _is_linear(cy) else mmd_bezier(cy, t)
    tz = t if _is_linear(cz) else mmd_bezier(cz, t)
    tr = t if _is_linear(cr) else mmd_bezier(cr, t)
    pos = (p0[0] + (p1[0] - p0[0]) * tx,
           p0[1] + (p1[1] - p0[1]) * ty,
           p0[2] + (p1[2] - p0[2]) * tz)
    q = _slerp(q0, q1, tr)
    return pos, q


def _slerp(q0, q1, t):
    d = sum(a * b for a, b in zip(q0, q1))
    if d < 0.0:
        q1 = tuple(-c for c in q1)
        d = -d
    if d > 0.9995:
        q = tuple(q0[i] + (q1[i] - q0[i]) * t for i in range(4))
    else:
        th = math.acos(max(-1.0, min(1.0, d)))
        s = math.sin(th)
        q = tuple(q0[i] * math.sin((1 - t) * th) / s +
                  q1[i] * math.sin(t * th) / s for i in range(4))
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def _sample(keys, frame, is_rot=True):
    """兼容旧调用：只要 pos 或 quat。"""
    p, q = sample(keys, frame)
    return q if is_rot else p


# --------------------------------------------------------------------------
# 坐标转换
# --------------------------------------------------------------------------

def split_side(name):
    """'腕.L' / '右腕' / '上半身' -> (base, side)"""
    if name.endswith(".L"):
        return name[:-2], "L"
    if name.endswith(".R"):
        return name[:-2], "R"
    if name.startswith("右"):
        return name[1:], "R"
    if name.startswith("左"):
        return name[1:], "L"
    return name, None


def resolve(name):
    """MMD 骨名 -> HS2 骨名（对不上返回 None）。"""
    base, side = split_side(name)
    hs2 = BASE_MAP.get(base)
    if hs2 is None:
        return None
    if "{S}" in hs2:
        if side is None:
            return None
        hs2 = hs2.replace("{S}", side)
    return hs2


# A-pose → T-pose 补偿：加在**大臂**上（MMD 的 腕），绕世界 Y 轴把手臂压低 arm_deg 度。
# 左臂指向 -X，绕 +Y 转是抬高，所以要负号；右臂反过来。
ARM_OFFSET = {"cf_J_ArmUp00_L": -1.0, "cf_J_ArmUp00_R": 1.0}


def mmd_quat_to_blender(q):
    """MMD 四元数 (x,y,z,w) → Blender。

    关键：MMD 和 Unity 虽然都是 Y-up，但**前后相反**（MMD 面向 -Z、
    Unity 面向 +Z）—— MMDD 官方 BoneAdjustment 的 spec "-x,y,-z" 就是
    这一步，等价于绕 Y 轴 180° 的共轭变换（四元数上 = x、z 分量取反）。
    """
    qx, qy, qz, qw = q
    return mu.to_blender_quat((-qx, qy, -qz, qw))


def unity_quat_to_blender(q):
    """Unity 四元数 (x,y,z,w) → Blender（只做 +90°X 换基）。"""
    return mu.to_blender_quat(q)


def mmd_pos_to_blender(p, scale=1.0):
    """MMD 位置 → Blender，按 MMDD 的坐标语义：

        MMDD GetAdjustedPosition:  (-x, +y, -z) * positionScale
        Unity → Blender:           ( x, -z,  y)
        ⇒ 合起来                  (-x, +z,  y) * scale

    旧版这里 X 不取反，直接映射路径的根位移和虚拟骨架/MMDD 是**左右镜像**
    （实测帧 500 差 0.388 m）。虚拟骨架路径不受影响，因为那边走
    `GetAdjustedPosition` 的独立实现。
    """
    return (-p[0] * scale, p[2] * scale, p[1] * scale)


# --------------------------------------------------------------------------
# 转成逐帧轨道
# --------------------------------------------------------------------------

def all_tracks(vmd, fps=30.0, scale=1.0, frame_count=None):
    """VMD 里**所有**骨骼的逐帧旋转/位置（MMD 单位），不映射。

    返回 {'rot': [(w,x,y,z)...], 'pos': [(x,y,z)...], 'curves': ...}
    位置是 MMD 单位，乘 scale 才变米；旋转已换成 Blender 基。
    """
    if frame_count is None:
        frame_count = int(round(vmd["frames"] / 30.0 * fps)) + 1
    out = {}
    for mmd_name, keys in vmd["bones"].items():
        if not keys:
            continue
        rots, poss, raw_rots = [], [], []
        for f in range(frame_count):
            src = f * 30.0 / fps
            p, q = sample(keys, src)
            raw_rots.append(tuple(q))
            bx, by, bz, bw = mmd_quat_to_blender(q)
            qb = Quaternion((bw, bx, by, bz))
            rots.append((qb.w, qb.x, qb.y, qb.z))
            poss.append(tuple(p))
        # raw_rot 保留 MMD 原始四元数 (x,y,z,w)，mmdrig 的 BoneAdjustment
        # 必须在 MMD 空间里做（spec 是 MMD→Unity），不能拿 Blender 旋转反推。
        out[mmd_name] = {"rot": rots, "pos": poss, "raw_rot": raw_rots}
    return out


def ik_enabled(vmd, vmd_bone, frame, default=True):
    """VMD 的 IK 可见/启用帧 → 该帧是否解算这根 IK 骨。

    规则和 MMDD 的 rangeSetupDefaultTrue 一样：没有关键帧 → default；
    第一个关键帧之前 → True；之后取「最后一个 <= frame 的关键帧」的值。
    """
    keys = vmd.get("ik") or []
    if not keys:
        return default
    val = True
    if frame < keys[0][0]:
        return True
    for fr, _vis, entries in keys:
        if fr > frame:
            break
        if vmd_bone in entries:
            val = entries[vmd_bone]
    return val


def to_hs2(vmd, fps=30.0, scale=0.0832, arm_deg=35.0, use_ik=False):
    """把 VMD 转成 HS2 骨架上的逐帧局部变换（旧的「直接映射」路径）。

    返回 {blender_bone_name: {"rot": [(w,x,y,z)...], "pos": [(x,y,z)...]}}
    """
    frames = int(round(vmd["frames"] / 30.0 * fps)) + 1
    out = {}
    merged = {}
    raw = {}

    def bone_rots(keys, q_off=None):
        rots = []
        for f in range(frames):
            src = f * 30.0 / fps
            _p, q = sample(keys, src)
            bx, by, bz, bw = mmd_quat_to_blender(q)
            qb = Quaternion((bw, bx, by, bz))
            if q_off is not None:
                qb = q_off @ qb
            rots.append((qb.w, qb.x, qb.y, qb.z))
        return rots

    for mmd_name, keys in vmd["bones"].items():
        base, side = split_side(mmd_name)
        if base.startswith(SKIP_BASES) or "ＩＫ" in mmd_name:
            continue

        if base in MERGE_MAP:
            merged[MERGE_MAP[base]] = bone_rots(keys)
            continue

        hs2 = resolve(mmd_name)
        if hs2 is None:
            continue

        q_off = None
        if abs(arm_deg) > 1e-6 and hs2 in ARM_OFFSET:
            q_off = Quaternion((0.0, 1.0, 0.0),
                               math.radians(arm_deg) * ARM_OFFSET[hs2])

        rots = bone_rots(keys)
        raw[base] = rots
        if q_off is not None:
            vals = []
            for r in rots:
                q = q_off @ Quaternion(r)
                vals.append((q.w, q.x, q.y, q.z))
            rots = vals
        entry = {"rot": rots, "src": mmd_name}
        if hs2 in POS_BONES:
            entry["pos"] = [mmd_pos_to_blender(sample(keys, f * 30.0 / fps)[0],
                                               scale)
                            for f in range(frames)]
        out[hs2] = entry

    for hs2, qs in merged.items():
        e = out.get(hs2)
        if e is None:
            out[hs2] = {"rot": qs, "src": "merged"}
            continue
        vals = []
        for a, b in zip(qs, e["rot"]):
            q = Quaternion(a) @ Quaternion(b)
            vals.append((q.w, q.x, q.y, q.z))
        e["rot"] = vals

    for mmd_base, cancel_base in CANCEL_MAP.items():
        cancel_qs = raw.get(cancel_base)
        e = out.get(BASE_MAP.get(mmd_base, ""))
        if cancel_qs is None or e is None or len(cancel_qs) != len(e["rot"]):
            continue
        vals = []
        for a, b in zip(cancel_qs, e["rot"]):
            q = Quaternion(a).inverted() @ Quaternion(b)
            vals.append((q.w, q.x, q.y, q.z))
        e["rot"] = vals
        e["src"] = mmd_base + " (腰キャンセル)"
    return out, frames
