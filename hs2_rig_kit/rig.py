"""控制层：把 HS2 的纯 FK 骨架包成动画友好的全身 IK / FK 操控骨架。

三层结构
--------
1. **变形骨**（游戏原生 693 根）：静止矩阵、层级、朝向一律不动，导出的局部旋转
   才能直接套回游戏。
2. **FK 控制层**（`CTRL_*`，不导出）：每个关节一个控制器，层级照着骨架搭。
   变形骨用 `Copy Transforms` 跟随控制器 —— 导入动作时把关键帧也镜像一份到
   控制器上（`key_targets` 一起做），之后改动作就是拖控制器。
3. **IK 机械层**（`IKB_*` / `CAR_*`，不导出）：腿/臂的两段 IK 挂在几何正确的
   机械骨上，再用 `CAR_` 搬运骨把「世界增量」转给变形骨。

为什么 IK 不能直接挂游戏骨
--------------------------
HS2 是「纯枢轴骨架」：所有骨静止时局部旋转都是单位四元数，tail 全部朝上。
Blender 的 IK 按骨长 + Y 轴算几何，直接挂上去会把腿看成一条朝后的直线
（实测踝关节偏 0.35 m）。改 tail 又会破坏静止朝向，导出的局部旋转对不上游戏。

所以：`IKB_` 负责「像腿」，`CAR_` 负责「把旋转翻译回游戏骨」。

关节补偿（dam 骨）
------------------
HS2 有一批 `*_dam` 骨（膝盖内侧、肘内侧、臀、腿根），游戏动作里是烘焙好的。
你在 Blender 里改关节它不会自动跟。这里用「rest 朝向与 dam 相同的 helper」
重建：

    helper = slerp(I, 主骨 local, f)   # COPY_ROTATION LOCAL, influence=f
    dam    = helper                    # COPY_ROTATION LOCAL, influence=1

helper 的 rest 和 dam 一致，Blender 会自动把主骨局部旋转转换到 dam 的
rest 基；这正是旧实现（helper rest=identity 或直接 copy 主骨）差 10~22°
的原因。逐帧对拍 8 个游戏动作、左右两侧：膝盖/肘内侧约 0~3°，
臀/腿根在极端动作仍有 5~28°。

控制器一览
----------
    CTRL_root / center / lower / upper / spine2 / chest / neck / head
    CTRL_shoulder / arm / fore / hand _L/R       手臂 FK
    CTRL_thigh / calf / foot _L/R                腿 FK
    STU_Body / Shoulder / ArmChain / Hand / Thigh / LegChain / Foot / Toe
                                                 Studio 式 IK/位置效应器
    CTRL_f_* （可选）                             手指 FK
"""

import math

import bpy
from mathutils import Matrix, Vector

from . import widgets

# --------------------------------------------------------------------------
# 命名
# --------------------------------------------------------------------------

MECH_PREFIX = "IKB_"
CARRIER_PREFIX = "CAR_"
IK_PREFIX = "STU_"          # Studio 式 IK 目标（原名 IK_/Toe_）
FK_PREFIX = "CTRL_"

CTRL_PREFIX = ("STU_", "IK_", "Toe_", "IKB_", "CAR_", "CTRL_",
               "FIXH_", "FIXH1_", "FIXH2_", "FIXR_")

CON_PREFIX = "HS2_"

LEG = {
    "L": {"thigh": "cf_J_LegUp00_L", "calf": "cf_J_LegLow01_L",
          "roll": "cf_J_LegLowRoll_L", "foot": "cf_J_Foot01_L",
          "toe": "cf_J_Foot02_L", "toes": "cf_J_Toes01_L"},
    "R": {"thigh": "cf_J_LegUp00_R", "calf": "cf_J_LegLow01_R",
          "roll": "cf_J_LegLowRoll_R", "foot": "cf_J_Foot01_R",
          "toe": "cf_J_Foot02_R", "toes": "cf_J_Toes01_R"},
}

ARM = {
    "L": {"shoulder": "cf_J_Shoulder_L", "upper": "cf_J_ArmUp00_L",
          "lower": "cf_J_ArmLow01_L", "hand": "cf_J_Hand_L",
          "finger": "cf_J_Hand_Middle01_L"},
    "R": {"shoulder": "cf_J_Shoulder_R", "upper": "cf_J_ArmUp00_R",
          "lower": "cf_J_ArmLow01_R", "hand": "cf_J_Hand_R",
          "finger": "cf_J_Hand_Middle01_R"},
}

COLL_CTRL = "HS2 控制"
COLL_IK = "HS2 IK"
COLL_MECH = "HS2 机械"
COLL_FINGER = "HS2 手指"
COLL_DEF = "HS2 变形"
COLL_FACE = "HS2 面部"
COLL_MISC = "HS2 其它"

# 躯干控制骨：(控制名, 目标骨, 父控制, 造型, 尺寸)
# 造型说明见 widgets.py：ring = 水平圆环（因为 HS2 骨骼静止旋转是单位矩阵，
# 本地 X/Y/Z 就是世界 X/Y/Z，不能按「XZ 平面」画，否则会变成竖着朝前的盘子）
BODY_CTRL = [
    ("CTRL_root", "cf_J_Root", None, "arrow", 0.30),
    # Studio 的 Body effector（骨盆位置）：STU_Body 是 CTRL_center 的父级，
    # 移动它就是整体平移骨盆 + 上下半身。它只在 build 里单独建，不参与
    # controlled_bones（否则打帧会把它当绝对目标，offset 语义就没了）。
    ("CTRL_center", "cf_J_Hips", "STU_Body", "ring", 0.165),
    ("CTRL_lower", "cf_J_Kosi02", "CTRL_center", "ring", 0.115),
    ("CTRL_upper", "cf_J_Spine01", "CTRL_center", "ring", 0.145),
    ("CTRL_spine2", "cf_J_Spine02", "CTRL_upper", "ring", 0.125),
    ("CTRL_chest", "cf_J_Spine03", "CTRL_spine2", "ring", 0.118),
    ("CTRL_neck", "cf_J_Neck", "CTRL_chest", "ring_small", 0.052),
    ("CTRL_head", "cf_J_Head", "CTRL_neck", "ring_small", 0.092),
]

# 左右控制骨：(后缀, 目标骨模板, 父控制模板, 造型, 尺寸, 造型偏移)
SIDE_CTRL = [
    ("shoulder", "cf_J_Shoulder_{S}", "CTRL_chest", "ring_side", 0.042, (0, 0, 0)),
    # Studio 的 Shoulder effector（上臂位置）：STU_Shoulder 作为上臂控制器的父级
    ("arm", "cf_J_ArmUp00_{S}", "STU_Shoulder_{S}", "ring_side", 0.052, (0, 0, 0)),
    ("fore", "cf_J_ArmLow01_{S}", "CTRL_arm_{S}", "ring_side", 0.044, (0, 0, 0)),
    ("hand", "cf_J_Hand_{S}", "CTRL_fore_{S}", "hand", 0.034, (0, 0, 0)),
    # Studio 的 Thigh effector（大腿位置）
    ("thigh", "cf_J_LegUp00_{S}", "STU_Thigh_{S}", "ring", 0.078, (0, 0, 0)),
    ("calf", "cf_J_LegLow01_{S}", "CTRL_thigh_{S}", "ring", 0.064, (0, 0, 0)),
    ("foot", "cf_J_Foot01_{S}", "CTRL_calf_{S}", "ring_small", 0.046, (0, 0.04, 0)),
]

FINGERS = [("Index", 3), ("Middle", 3), ("Ring", 3), ("Little", 3), ("Thumb", 3)]

# 关节补偿：(驱动骨模板, dam 骨模板, 比例)
#
# 比例不是拍的：从游戏里 8 个动作（swim_Idle / Broken_Chair / cook_Idle /
# Broken_stand …）逐帧拟合 `dam_local = slerp(I, main_local, f)` 得到。
# 旧表按 x/y/z 轴分别取比例，还带 invert_y/z —— 实测那会把旋转轴掰歪，
# 膝盖内侧/手肘差 3~10°。现在统一用「整体 Slerp」。
#
# 手部 dam（Hand_dam / Hand_Wrist_dam / ArmLow02_dam）和主骨不是简单的
# Slerp 关系（拟合残差 8~20°），先不驱动，避免把原动画手感改坏。
# 比例是在 Blender 里对拍拟合的（不是 Unity 原始 local 空间）：
#   helper = slerp(I, 主骨 local, f)，helper rest = dam rest。
CORRECTIVES = [
    ("cf_J_LegLow01_{S}", "cf_J_LegKnee_dam_{S}", 0.50),
    ("cf_J_LegLow01_{S}", "cf_J_LegKnee_back_{S}", 1.00),
    ("cf_J_LegUp00_{S}", "cf_J_SiriDam_{S}", 0.40),
    ("cf_J_LegUp00_{S}", "cf_J_LegUpDam_{S}", 0.56),
    ("cf_J_ArmLow01_{S}", "cf_J_ArmElboura_dam_{S}", 0.59),
    ("cf_J_ArmLow01_{S}", "cf_J_ArmElbo_dam_01_{S}", 0.60),
    # 注：HS2PE 的 Ankles 校正（cf_J_Foot02 = 0.9 × Foot01 的 X 角）没有
    # 直接搬：它要读 Foot01 的**原始 localRotation**，而 Blender 里 Foot01
    # 是 IK/约束驱动的，COPY_ROTATION 读到的是含 rest 的求值矩阵，静止时
    # 也会把 Toes 带偏 3.6 cm。这个修正放到以后用「求值后 X 角」的驱动/烘焙做。
]

# HS2PE「Crotch」里 cf_J_Kosi02_s 的额外一层：
#   avg = Lerp(左大腿 local, 右大腿 local, 0.5)
#   cf_J_Kosi02_s = Lerp(identity, avg, 0.25)
KOSI_FIX = "cf_J_Kosi02_s"

def body_ctrl():
    return "STU_Body"


def shoulder_ctrl(side):
    return "STU_Shoulder_" + side


def thigh_ctrl(side):
    return "STU_Thigh_" + side


def foot_ctrl(side):
    return "STU_Foot_" + side


def knee_ctrl(side):
    return "STU_LegChain_" + side


def hand_ctrl(side):
    return "STU_Hand_" + side


def elbow_ctrl(side):
    return "STU_ArmChain_" + side


def toe_ctrl(side):
    return "STU_Toe_" + side


def mech_thigh(side):
    return MECH_PREFIX + "thigh_" + side


def mech_calf(side):
    return MECH_PREFIX + "calf_" + side


def mech_upper(side):
    return MECH_PREFIX + "upper_" + side


def mech_lower(side):
    return MECH_PREFIX + "lower_" + side


def car_thigh(side):
    return CARRIER_PREFIX + "thigh_" + side


def car_calf(side):
    return CARRIER_PREFIX + "calf_" + side


def car_upper(side):
    return CARRIER_PREFIX + "upper_" + side


def car_lower(side):
    return CARRIER_PREFIX + "lower_" + side


def fk_ctrl(bone):
    return FK_PREFIX + bone


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _pose_mode(arm, mode):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for ob in bpy.context.selected_objects:
        ob.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode=mode)


def _rest(arm, name):
    b = arm.data.bones.get(name)
    return b.matrix_local.copy() if b else None


def _head_of(arm, name):
    m = _rest(arm, name)
    return m.to_translation() if m else None


def _orient_like(eb, mat, length, exact=False):
    """把骨骼摆成 mat 的朝向。

    exact=True 严格复制矩阵（CAR 搬运骨必须这样：它的静止朝向要等于游戏骨，
    才能把世界旋转翻译成纯增量；用 tail+align_roll 会有 1~3° 残差，
    IK 就会差出 1~2 cm）。
    """
    if not exact:
        eb.head = mat.to_translation()
        y = mat.col[1].to_3d()
        if y.length < 1e-6:
            y = Vector((0.0, 0.0, 1.0))
        eb.tail = eb.head + y.normalized() * max(length, 1e-4)
        z = mat.col[2].to_3d()
        if z.length > 1e-6:
            try:
                eb.align_roll(z)
            except Exception:
                pass
        return eb
    rot = Matrix((
        (mat[0][0], mat[0][1], mat[0][2]),
        (mat[1][0], mat[1][1], mat[1][2]),
        (mat[2][0], mat[2][1], mat[2][2]),
    ))
    try:
        q = rot.to_quaternion()
        q.normalize()
        m = q.to_matrix().to_4x4()
        m.translation = mat.to_translation()
        eb.matrix = m
        eb.length = max(length, 1e-4)
    except Exception:
        eb.head = mat.to_translation()
        y = mat.col[1].to_3d()
        if y.length < 1e-6:
            y = Vector((0.0, 0.0, 1.0))
        eb.tail = eb.head + y.normalized() * max(length, 1e-4)
    return eb


def _segment(eb, head, tail):
    eb.head = head
    eb.tail = tail
    return eb


def _project_on_line(a, b, p):
    ab = b - a
    d = ab.length_squared
    if d < 1e-12:
        return a.copy()
    t = max(0.0, min(1.0, (p - a).dot(ab) / d))
    return a + ab * t


def _bend_dir(joint, a, b, fallback):
    d = joint - _project_on_line(a, b, joint)
    if d.length < 1e-5:
        return fallback.normalized()
    return d.normalized()


def _move_to(pb, pos):
    m = pb.matrix.copy()
    m.translation = pos
    pb.matrix = m


def _set_child_world(pb, parent_world, world):
    rel = pb.bone.parent.matrix_local.inverted() @ pb.bone.matrix_local
    pb.matrix_basis = (parent_world @ rel).inverted() @ world


def _con(arm, name):
    for pb in arm.pose.bones:
        c = pb.constraints.get(name)
        if c is not None:
            return c
    return None


def _ik_limits(pb, x=None, y=None, z=None):
    """给 IK 链上的骨设关节旋转范围（度）。

    「拖远不扭曲」的关键。三道保险一起上：
      * ``use_ik_limit_*`` + ``ik_min/max_*``  角度范围
      * ``lock_ik_*``                          完全锁掉不该动的轴
      * ``ik_stiffness``                       在上面 _ik_stiff 里

    注意 Blender 对链**末端**骨的 ik_limit 有时不生效，所以膝盖那根
    （IKB_calf）必须和髋（IKB_thigh）一起限制才拦得住反折。
    """
    if pb is None:
        return
    for axis, rng in (("x", x), ("y", y), ("z", z)):
        limited = rng is not None
        setattr(pb, "use_ik_limit_" + axis, limited)
        if limited:
            setattr(pb, "ik_min_" + axis, math.radians(rng[0]))
            setattr(pb, "ik_max_" + axis, math.radians(rng[1]))
        # 范围正好是一个点（或没给）→ 直接锁死该轴
        locked = (rng is not None and abs(rng[1] - rng[0]) < 1e-6)
        try:
            setattr(pb, "lock_ik_" + axis, locked)
        except Exception:
            pass


def _ik_lock(pb, x=None, y=None, z=None):
    """在 IK 之后夹一层 LIMIT_ROTATION，真正拦住反折。

    为什么不用 ``use_ik_limit_*``：Blender 里 IK 约束的 **owner 骨**（链条末端）
    的 ik_limit 是不生效的 —— 实测设了 [-20,170] 膝盖照样能弯到 193°。
    所以再加一条 LIMIT_ROTATION（约束列表里排在 IK 后面）把结果夹回来。
    代价是超出范围时脚会「够不到」目标，但姿势不会扭曲 —— 这正是要的。
    """
    if pb is None:
        return None
    con = pb.constraints.new("LIMIT_ROTATION")
    con.name = CON_PREFIX + "LIM_" + pb.name
    con.owner_space = "LOCAL"
    for axis, rng in (("x", x), ("y", y), ("z", z)):
        setattr(con, "use_limit_" + axis, rng is not None)
        if rng is None:
            continue
        lo, hi = sorted(rng)
        setattr(con, "min_" + axis, math.radians(lo))
        setattr(con, "max_" + axis, math.radians(hi))
    con.influence = 1.0
    return con


def _ik_stiff(pb, x=0.0, y=0.0, z=0.0):
    """IK 刚度。默认全 0 —— Blender 的 stiffness 是「抵抗旋转」，只要大于 0
    就会让解算器解不到吸附后的姿势（实测 0.35 就跳 16cm）。留着接口方便调。"""
    if pb is None:
        return
    try:
        pb.ik_stiffness_x = x
        pb.ik_stiffness_y = y
        pb.ik_stiffness_z = z
    except Exception:
        pass


# --------------------------------------------------------------------------
# 创建 / 清理
# --------------------------------------------------------------------------

def has_rig(arm):
    return arm is not None and arm.data.bones.get(foot_ctrl("L")) is not None


def _rig_constraints(arm):
    out = []
    for pb in arm.pose.bones:
        for c in pb.constraints:
            if c.name.startswith(CON_PREFIX):
                out.append(c)
    return out


def clear(arm):
    """删掉控制层（FK 控制骨 / 机械骨 / 约束 / 集合）。变形骨原封不动。"""
    if arm is None:
        return
    _pose_mode(arm, "POSE")
    for pb in arm.pose.bones:
        for c in list(pb.constraints):
            if c.name.startswith(CON_PREFIX):
                pb.constraints.remove(c)
    for pb in arm.pose.bones:
        for prop in ("ik", "jointfix"):
            if prop in pb.keys():
                del pb[prop]

    _pose_mode(arm, "EDIT")
    ebs = arm.data.edit_bones

    def depth(n):
        d, cur = 0, ebs.get(n)
        while cur is not None and cur.parent is not None:
            d += 1
            cur = cur.parent
        return d

    victims = [eb.name for eb in ebs if eb.name.startswith(CTRL_PREFIX)]
    for name in sorted(victims, key=depth, reverse=True):
        eb = ebs.get(name)
        if eb is not None:
            ebs.remove(eb)
    _pose_mode(arm, "OBJECT")

    for nm in (COLL_CTRL, COLL_IK, COLL_MECH, COLL_FINGER):
        coll = arm.data.collections_all.get(nm)
        if coll is not None:
            arm.data.collections.remove(coll)


def _ensure_collection(arm, name):
    coll = arm.data.collections_all.get(name)
    if coll is None:
        coll = arm.data.collections.new(name)
    return coll


def _paint(arm, name, palette):
    b = arm.data.bones.get(name)
    if b is None:
        return
    try:
        b.color.palette = palette
    except Exception:
        pass


# --------------------------------------------------------------------------
# 建控制层
# --------------------------------------------------------------------------

def build(arm, key_animation=True, fingers=False):
    """在 arm 上搭完整控制层。返回统计信息。"""
    if arm is None:
        raise RuntimeError("没有骨架")
    if has_rig(arm):
        clear(arm)

    widgets.build_all()

    names = {b.name for b in arm.data.bones}
    for side in LEG:
        if LEG[side]["thigh"] not in names:
            raise RuntimeError("这副骨架不像 HS2 骨架（缺少 %s）" % LEG[side]["thigh"])

    stats = {"controls": 0, "mech": 0, "constraints": 0, "pole": {},
             "keyed": 0, "correctives": 0, "fingers": 0}

    _pose_mode(arm, "EDIT")
    ebs = arm.data.edit_bones

    def new_ctrl(name, like_mat, length, exact=False):
        eb = ebs.new(name)
        _orient_like(eb, like_mat, length, exact=exact)
        eb.use_deform = False
        return eb

    # ================= FK 控制骨 =================
    ctrl_bones = []
    for cname, bone, parent, _shape, _size in BODY_CTRL:
        mat = _rest(arm, bone)
        if mat is None:
            continue
        eb = new_ctrl(cname, mat, 0.16)
        if parent and ebs.get(parent) is not None:
            eb.parent = ebs[parent]
            eb.use_connect = False
        ctrl_bones.append((cname, bone, parent))

    # Studio Body effector：插在 CTRL_root 和 CTRL_center 之间。
    body_eb = new_ctrl(body_ctrl(), _rest(arm, "cf_J_Hips"), 0.16)
    if ebs.get("CTRL_root") is not None:
        body_eb.parent = ebs["CTRL_root"]
        body_eb.use_connect = False
    if ebs.get("CTRL_center") is not None:
        ebs["CTRL_center"].parent = body_eb
        ebs["CTRL_center"].use_connect = False

    for side in ("L", "R"):
        for suffix, bone_t, parent_t, _shape, _size, _off in SIDE_CTRL:
            bone = bone_t.format(S=side)
            parent = parent_t.format(S=side)
            mat = _rest(arm, bone)
            if mat is None:
                continue
            # Studio Shoulder / Thigh effector：插在关节控制器和父级之间。
            if suffix == "arm":
                stu = new_ctrl(shoulder_ctrl(side),
                               _rest(arm, ARM[side]["upper"]), 0.06)
                if ebs.get("CTRL_shoulder_" + side) is not None:
                    stu.parent = ebs["CTRL_shoulder_" + side]
                    stu.use_connect = False
            elif suffix == "thigh":
                stu = new_ctrl(thigh_ctrl(side),
                               _rest(arm, LEG[side]["thigh"]), 0.08)
                if ebs.get("CTRL_lower") is not None:
                    stu.parent = ebs["CTRL_lower"]
                    stu.use_connect = False
            cname = "CTRL_%s_%s" % (suffix, side)
            eb = new_ctrl(cname, mat, 0.1)
            if ebs.get(parent) is not None:
                eb.parent = ebs[parent]
                eb.use_connect = False
            ctrl_bones.append((cname, bone, parent))

    if fingers:
        for side in ("L", "R"):
            for fname, count in FINGERS:
                prev = "CTRL_hand_" + side
                for i in range(1, count + 1):
                    bone = "cf_J_Hand_%s%02d_%s" % (fname, i, side)
                    mat = _rest(arm, bone)
                    if mat is None:
                        prev = None
                        continue
                    cname = "CTRL_f_%s%02d_%s" % (fname, i, side)
                    eb = new_ctrl(cname, mat, 0.03)
                    if prev and ebs.get(prev) is not None:
                        eb.parent = ebs[prev]
                        eb.use_connect = False
                    ctrl_bones.append((cname, bone, prev))
                    prev = cname
                    stats["fingers"] += 1

    # ================= IK 机械骨 & 目标 =================
    for side, ch in LEG.items():
        hip = _head_of(arm, ch["thigh"])
        knee = _head_of(arm, ch["calf"])
        ankle = _head_of(arm, ch["foot"])
        toe = _head_of(arm, ch["toes"])

        pelvis = ebs.get("cf_J_Kosi02") or ebs.get("cf_J_Hips")
        m = ebs.new(mech_thigh(side))
        _segment(m, hip, knee)
        m.use_deform = False
        # Studio Thigh effector：机械腿链挂在 STU_Thigh 下，移动它对 IK/FK
        # 都生效（FBBIK 的 thigh effector 也是拉整条腿的根）。
        m.parent = ebs.get(thigh_ctrl(side)) or pelvis

        m = ebs.new(mech_calf(side))
        _segment(m, knee, ankle)
        m.use_deform = False
        m.parent = ebs[mech_thigh(side)]
        m.use_connect = True

        c = new_ctrl(car_thigh(side), _rest(arm, ch["thigh"]), (knee - hip).length)
        c.parent = ebs[mech_thigh(side)]
        c.use_connect = False

        c = new_ctrl(car_calf(side), _rest(arm, ch["calf"]), (ankle - knee).length)
        c.parent = ebs[mech_calf(side)]
        c.use_connect = False

        # 脚 IK 目标：**与游戏骨同 rest 朝向**（head 在踝 = IK 目标点）。
        # cf_J_Foot01 用 COPY_ROTATION(WORLD/REPLACE) 跟随它 —— 控制骨的 rest
        # 朝向必须等于游戏骨（HS2 = 单位旋转），乱设 head/tail 会把静止姿势
        # 的脚拧歪（第八轮反馈的「生成控制器后脚变形」就是这个原因）。
        # 想要的脚掌造型由 widgets.foot 画（水平脚印），与骨骼朝向无关。
        new_ctrl(foot_ctrl(side), _rest(arm, ch["foot"]), 0.1)

        # 极向控制器的方位角（只用到位置，朝向无所谓）
        fwd = (toe - ankle)
        if fwd.length < 1e-5:
            fwd = Vector((0.0, -0.1, 0.0))
        fb = fwd.copy()
        fb.z = 0.0
        if fb.length < 1e-5:
            fb = Vector((0.0, -1.0, 0.0))
        eb = new_ctrl(knee_ctrl(side), _rest(arm, ch["calf"]), 0.05)
        eb.head = knee + fb.normalized() * 0.38
        eb.tail = eb.head + Vector((0.0, 0.0, 0.05))

        eb = new_ctrl(toe_ctrl(side), _rest(arm, ch["toe"]), 0.05)
        eb.parent = ebs[foot_ctrl(side)]
        eb.use_connect = False

    for side, ch in ARM.items():
        shoulder = _head_of(arm, ch["upper"])
        elbow = _head_of(arm, ch["lower"])
        wrist = _head_of(arm, ch["hand"])

        m = ebs.new(mech_upper(side))
        _segment(m, shoulder, elbow)
        m.use_deform = False
        # Studio Shoulder effector：机械臂链挂在 STU_Shoulder 下。
        m.parent = ebs.get(shoulder_ctrl(side)) or ebs.get(ch["shoulder"])

        m = ebs.new(mech_lower(side))
        _segment(m, elbow, wrist)
        m.use_deform = False
        m.parent = ebs[mech_upper(side)]
        m.use_connect = True

        c = new_ctrl(car_upper(side), _rest(arm, ch["upper"]), (elbow - shoulder).length)
        c.parent = ebs[mech_upper(side)]
        c.use_connect = False

        c = new_ctrl(car_lower(side), _rest(arm, ch["lower"]), (wrist - elbow).length)
        c.parent = ebs[mech_lower(side)]
        c.use_connect = False

        # 手控制骨同理：rest 朝向必须 = 游戏骨（cf_J_Hand 走 COPY_ROTATION）
        new_ctrl(hand_ctrl(side), _rest(arm, ch["hand"]), 0.12)

        eb = new_ctrl(elbow_ctrl(side), _rest(arm, ch["lower"]), 0.05)
        eb.head = elbow + Vector((0.0, 0.32, 0.0))
        eb.tail = eb.head + Vector((0.0, 0.0, 0.05))

    stats["controls"] = len([e for e in ebs if e.name.startswith(
        ("STU_", "IK_", "Toe_", "CTRL_"))])
    stats["mech"] = len([e for e in ebs if e.name.startswith(("IKB_", "CAR_"))])
    _pose_mode(arm, "POSE")

    # ================= 造型 =================
    def shape(ctrl, wname, scale, translate=(0.0, 0.0, 0.0)):
        pb = arm.pose.bones.get(ctrl)
        if pb is None:
            return
        pb.custom_shape = widgets.ensure(wname)
        pb.use_custom_shape_bone_size = False
        pb.custom_shape_scale_xyz = (scale, scale, scale)
        pb.custom_shape_translation = translate
        pb.rotation_mode = "QUATERNION"

    for cname, _bone, _parent, wname, size in BODY_CTRL:
        # 头的环抬到头顶上方，不然套在脖子上
        shape(cname, wname, size,
              (0.0, 0.0, 0.13) if cname == "CTRL_head" else (0, 0, 0))
    shape(body_ctrl(), "ring", 0.11)
    for side in ("L", "R"):
        shape(shoulder_ctrl(side), "ring_side", 0.045)
        shape(thigh_ctrl(side), "ring", 0.075)
    for side in ("L", "R"):
        for suffix, _bt, _pt, wname, size, off in SIDE_CTRL:
            w = ("hand_" + side) if wname == "hand" else wname
            shape("CTRL_%s_%s" % (suffix, side), w, size, off)
        shape(foot_ctrl(side), "foot", 0.13)
        shape(knee_ctrl(side), "diamond", 0.028)
        shape(toe_ctrl(side), "knuckle", 0.022)
        shape(hand_ctrl(side), "hand_" + side, 0.095)
        shape(elbow_ctrl(side), "diamond", 0.024)
        if fingers:
            for fname, count in FINGERS:
                for i in range(1, count + 1):
                    shape("CTRL_f_%s%02d_%s" % (fname, i, side),
                          "knuckle", 0.010)

    # ================= 约束 =================
    ik_bones = {}
    for side in LEG:
        for key in ("thigh", "calf", "foot", "toe"):
            ik_bones[LEG[side][key]] = foot_ctrl(side)
    for side in ARM:
        for key in ("upper", "lower", "hand"):
            ik_bones[ARM[side][key]] = hand_ctrl(side)

    # FK：变形骨跟随控制骨
    for cname, bone, _parent in ctrl_bones:
        pb = arm.pose.bones.get(bone)
        if pb is None:
            continue
        con = pb.constraints.new("COPY_TRANSFORMS")
        con.name = CON_PREFIX + "FK_" + bone
        con.target = arm
        con.subtarget = cname
        con.target_space = "WORLD"
        con.owner_space = "WORLD"
        con.mix_mode = "REPLACE"
        con.influence = 1.0
        stats["constraints"] += 1
        limb = ik_bones.get(bone)
        if limb:
            _drive_fk(con, limb)

    # IK：机械链
    for side, ch in LEG.items():
        pb = arm.pose.bones[mech_calf(side)]
        con = pb.constraints.new("IK")
        con.name = CON_PREFIX + "IK_leg_" + side
        con.target = arm
        con.subtarget = foot_ctrl(side)
        con.chain_count = 2
        # use_tail = 「链条末端算不算 owner 的 tail」。必须是 True：
        # 设 False 就变成「让膝盖去够目标点」，脚完全拖不动（实测位移只有 3cm）。
        con.use_tail = True
        con.use_stretch = False
        con.pole_target = arm
        con.pole_subtarget = knee_ctrl(side)
        con.pole_angle = 0.0
        stats["constraints"] += 1

        # 膝盖只能单向弯（0.5°~180°），Y/Z 锁死 → 反折和自转都没了。
        # 这组数字来自 MMDD（HS2 的 MMD 舞蹈插件）的 chara_default_2.0.ini：
        #     LIMITS=(((0.5, 180), (0, 0), (0, 0)), None)
        # ik_limit 对末端骨不生效，所以再加一层 LIMIT_ROTATION 兜底。
        _ik_limits(pb, x=(0.5, 180.0), y=(0.0, 0.0), z=(0.0, 0.0))
        _ik_lock(pb, x=(0.5, 180.0), y=(0.0, 0.0), z=(0.0, 0.0))
        stats["constraints"] += 1
        _ik_stiff(pb)

        for deform_bone, carrier in ((ch["thigh"], car_thigh(side)),
                                     (ch["calf"], car_calf(side)),
                                     (ch["foot"], foot_ctrl(side)),
                                     (ch["toe"], toe_ctrl(side))):
            pbd = arm.pose.bones.get(deform_bone)
            if pbd is None:
                continue
            rot = pbd.constraints.new("COPY_ROTATION")
            rot.name = CON_PREFIX + "ROT_" + deform_bone
            rot.target = arm
            rot.subtarget = carrier
            rot.target_space = "WORLD"
            rot.owner_space = "WORLD"
            rot.mix_mode = "REPLACE"
            rot.influence = 1.0
            stats["constraints"] += 1

        arm.pose.bones[foot_ctrl(side)]["ik"] = 1.0
        _ui_range(arm.pose.bones[foot_ctrl(side)], "ik")

    for side, ch in ARM.items():
        pb = arm.pose.bones[mech_lower(side)]
        con = pb.constraints.new("IK")
        con.name = CON_PREFIX + "IK_arm_" + side
        con.target = arm
        con.subtarget = hand_ctrl(side)
        con.chain_count = 2
        con.use_tail = True
        con.use_stretch = False
        con.pole_target = arm
        con.pole_subtarget = elbow_ctrl(side)
        con.pole_angle = 0.0
        stats["constraints"] += 1

        # 肘不设 ik_limit：手臂的 FK 姿势里有超伸（游泳举过头），
        # 锁了会让「吸附后开 IK」跳几厘米。肘的扭曲靠 pole 目标控制就够了。
        _ik_stiff(pb)

        for deform_bone, carrier in ((ch["upper"], car_upper(side)),
                                     (ch["lower"], car_lower(side)),
                                     (ch["hand"], hand_ctrl(side))):
            pbd = arm.pose.bones.get(deform_bone)
            if pbd is None:
                continue
            rot = pbd.constraints.new("COPY_ROTATION")
            rot.name = CON_PREFIX + "ROT_" + deform_bone
            rot.target = arm
            rot.subtarget = carrier
            rot.target_space = "WORLD"
            rot.owner_space = "WORLD"
            rot.mix_mode = "REPLACE"
            rot.influence = 1.0
            stats["constraints"] += 1

        arm.pose.bones[hand_ctrl(side)]["ik"] = 1.0
        _ui_range(arm.pose.bones[hand_ctrl(side)], "ik")

    # Studio Shoulder / Thigh effector 是**位置**效应器：直接把上臂/大腿骨
    # 的世界位置跟到控制器上（FBBIK 也是拉 chain 根的位置）。
    for side in ("L", "R"):
        for ctrl, bone in ((shoulder_ctrl(side), ARM[side]["upper"]),
                           (thigh_ctrl(side), LEG[side]["thigh"])):
            pb = arm.pose.bones.get(bone)
            if pb is None:
                continue
            con = pb.constraints.new("COPY_LOCATION")
            con.name = CON_PREFIX + "POS_" + bone
            con.target = arm
            con.subtarget = ctrl
            con.target_space = "WORLD"
            con.owner_space = "WORLD"
            con.influence = 1.0
            stats["constraints"] += 1

    stats["correctives"] = _build_correctives(arm)

    for side in LEG:
        for con_name in (CON_PREFIX + "IK_leg_" + side,
                         CON_PREFIX + "ROT_" + LEG[side]["thigh"],
                         CON_PREFIX + "ROT_" + LEG[side]["calf"],
                         CON_PREFIX + "ROT_" + LEG[side]["foot"],
                         CON_PREFIX + "ROT_" + LEG[side]["toe"]):
            _drive_ik(arm, con_name, foot_ctrl(side))
    for side in ARM:
        for con_name in (CON_PREFIX + "IK_arm_" + side,
                         CON_PREFIX + "ROT_" + ARM[side]["upper"],
                         CON_PREFIX + "ROT_" + ARM[side]["lower"],
                         CON_PREFIX + "ROT_" + ARM[side]["hand"]):
            _drive_ik(arm, con_name, hand_ctrl(side))

    _make_collections(arm, fingers)

    for side in LEG:
        a = _solve_pole(arm, CON_PREFIX + "IK_leg_" + side, side, True)
        if a is not None:
            stats["pole"]["leg_" + side] = a
    for side in ARM:
        a = _solve_pole(arm, CON_PREFIX + "IK_arm_" + side, side, False)
        if a is not None:
            stats["pole"]["arm_" + side] = a

    if key_animation and arm.animation_data and arm.animation_data.action:
        scn = bpy.context.scene
        try:
            stats["keyed"] = key_targets(arm, scn.frame_start, scn.frame_end)
        except Exception as e:
            print("[HS2] 控制器打帧失败：", e)

    # 控制器在身体内部，不开「在前面」会被网格挡住 → 看起来「没有控制器」
    arm.show_in_front = True
    try:
        arm.data.display_type = "OCTAHEDRAL"
    except Exception:
        pass
    set_joint_fix(arm, False)
    try:
        arm.hs2_jointfix = False        # 属性回调会同步约束静音状态
    except Exception:
        arm["hs2_jointfix"] = 0
    bpy.context.view_layer.update()
    arm["hs2_rig"] = 1
    arm["hs2_rig_version"] = 4
    arm["hs2_fingers"] = 1 if fingers else 0
    return stats


def _ensure_fix_helpers(arm):
    """给每根 dam 骨建一根 rest 朝向与 dam 相同的 helper。

    关键：helper 的 rest **必须和 dam 一样**。Blender 的 COPY_ROTATION
    (LOCAL) 会把 target 的局部旋转按各自 rest 基转换；helper 和 dam
    rest 一致时，h = slerp(I, main_local, f) 在 dam 局部空间里正好是
    游戏里 dam 的旋转（连固定偏移一起带上）。
    """
    needed = []
    expected = set()
    for _target_t, dam_t, _ratio in CORRECTIVES:
        for side in ("L", "R"):
            dam = dam_t.format(S=side)
            nm = "FIXH_" + dam
            expected.add(nm)
            if arm.pose.bones.get(dam) is not None and \
                    arm.pose.bones.get(nm) is None:
                needed.append((dam, nm))

    nm = "FIXH_" + KOSI_FIX
    expected.add(nm)
    if arm.pose.bones.get(KOSI_FIX) is not None and \
            arm.pose.bones.get(nm) is None:
        needed.append((KOSI_FIX, nm))

    # 清掉历史版本/错误解包留下的 FIXH_*（例如建到主骨上的那些）
    stale = [pb.name for pb in arm.pose.bones
             if pb.name.startswith("FIXH_") and pb.name not in expected]
    if not needed and not stale:
        return

    _pose_mode(arm, "EDIT")
    ebs = arm.data.edit_bones
    for nm in stale:
        eb = ebs.get(nm)
        if eb is not None:
            ebs.remove(eb)
    for dam, nm in needed:
        db = ebs.get(dam)
        if db is None:
            continue
        eb = ebs.new(nm)
        eb.head = db.head.copy()
        eb.tail = db.tail.copy()
        eb.parent = db.parent
        eb.use_connect = False
        eb.use_deform = False
        try:
            eb.matrix = db.matrix.copy()
        except Exception:
            pass
    _pose_mode(arm, "POSE")

    coll = arm.data.collections_all.get(COLL_MECH)
    if coll is None:
        try:
            coll = arm.data.collections.new(COLL_MECH)
        except Exception:
            coll = None
    if coll is not None:
        for _dam, nm in needed:
            b = arm.data.bones.get(nm)
            if b is not None:
                coll.assign(b)
        try:
            coll.is_visible = False
        except Exception:
            pass


def _build_correctives(arm):
    """dam 骨随关节旋转。

    每条关系用「rest 与 dam 相同的 helper」：
        helper = slerp(I, main_local, f)     （COPY_ROTATION, LOCAL）
        dam    = helper                      （COPY_ROTATION, LOCAL, 1.0）
    这样 rest 基由 Blender 在 COPY_ROTATION 里自动转换，不会像旧实现那样
    把主骨局部旋转直接套到 rest 不同的 dam 上（实测差 10~22°）。
    """
    _ensure_fix_helpers(arm)
    made = 0
    for target_t, dam_t, ratio in CORRECTIVES:
        for side in ("L", "R"):
            target = target_t.format(S=side)
            dam = dam_t.format(S=side)
            pbd = arm.pose.bones.get(dam)
            h = arm.pose.bones.get("FIXH_" + dam)
            if pbd is None or h is None or target not in arm.pose.bones:
                continue
            for pb in (pbd, h):
                for old in list(pb.constraints):
                    if old.name.startswith(CON_PREFIX + "FIX"):
                        pb.constraints.remove(old)
            con = h.constraints.new("COPY_ROTATION")
            con.name = CON_PREFIX + "FIXH_" + dam
            con.target = arm
            con.subtarget = target
            con.target_space = "LOCAL"
            con.owner_space = "LOCAL"
            con.mix_mode = "REPLACE"
            con.use_x = True
            con.use_y = True
            con.use_z = True
            con.influence = abs(ratio)

            con = pbd.constraints.new("COPY_ROTATION")
            con.name = CON_PREFIX + "FIX_" + dam
            con.target = arm
            con.subtarget = h.name
            con.target_space = "LOCAL"
            con.owner_space = "LOCAL"
            con.mix_mode = "REPLACE"
            con.use_x = True
            con.use_y = True
            con.use_z = True
            con.influence = 1.0
            made += 1

    # HS2PE Crotch 的 Kosi02_s：Lerp(identity, avg(左右大腿), 0.25)
    pbd = arm.pose.bones.get(KOSI_FIX)
    h = arm.pose.bones.get("FIXH_" + KOSI_FIX)
    if pbd is not None and h is not None:
        for pb in (pbd, h):
            for old in list(pb.constraints):
                if old.name.startswith(CON_PREFIX + "FIX"):
                    pb.constraints.remove(old)
        for side in ("L", "R"):
            src = "cf_J_LegUp00_" + side
            if src not in arm.pose.bones:
                continue
            con = h.constraints.new("COPY_ROTATION")
            con.name = CON_PREFIX + "FIXH_%s_%s" % (KOSI_FIX, side)
            con.target = arm
            con.subtarget = src
            con.target_space = "LOCAL"
            con.owner_space = "LOCAL"
            con.mix_mode = "REPLACE"
            con.use_x = True
            con.use_y = True
            con.use_z = True
            con.influence = 0.5
        con = pbd.constraints.new("COPY_ROTATION")
        con.name = CON_PREFIX + "FIX_" + KOSI_FIX
        con.target = arm
        con.subtarget = h.name
        con.target_space = "LOCAL"
        con.owner_space = "LOCAL"
        con.mix_mode = "REPLACE"
        con.use_x = True
        con.use_y = True
        con.use_z = True
        con.influence = 0.25
        made += 1
    return made


def _ui_range(pb, prop):
    try:
        pb.id_properties_ui(prop).update(min=0.0, max=1.0, soft_min=0.0,
                                         soft_max=1.0, step=10, precision=2,
                                         description="0 = 纯 FK，1 = IK")
    except Exception:
        pass


def _drive_ik(arm, con_name, ctrl):
    con = _con(arm, con_name)
    if con is None:
        return
    fc = con.driver_add("influence")
    drv = fc.driver
    drv.type = "AVERAGE"
    var = drv.variables.new()
    var.name = "ik"
    var.type = "SINGLE_PROP"
    t = var.targets[0]
    t.id_type = "OBJECT"
    t.id = arm
    t.data_path = 'pose.bones["%s"]["ik"]' % ctrl


def _drive_fk(con, limb_ctrl):
    """FK 控制的影响 = 1 - ik（IK 打开时 FK 层让位）。"""
    fc = con.driver_add("influence")
    drv = fc.driver
    drv.type = "SCRIPTED"
    drv.expression = "1.0 - ik"
    var = drv.variables.new()
    var.name = "ik"
    var.type = "SINGLE_PROP"
    t = var.targets[0]
    t.id_type = "OBJECT"
    t.id = con.id_data
    t.data_path = 'pose.bones["%s"]["ik"]' % limb_ctrl


def _make_collections(arm, fingers=False):
    ctrl = _ensure_collection(arm, COLL_CTRL)
    ik = _ensure_collection(arm, COLL_IK)
    mech = _ensure_collection(arm, COLL_MECH)
    finger = _ensure_collection(arm, COLL_FINGER) if fingers else None
    deform = _ensure_collection(arm, COLL_DEF)
    face = _ensure_collection(arm, COLL_FACE)
    misc = _ensure_collection(arm, COLL_MISC)

    faces = set()
    for b in arm.data.bones:
        n = b.name
        if n.startswith(("cf_J_Face", "cf_J_Cheek", "cf_J_Chin", "cf_J_Mouth",
                         "cf_J_Nose", "cf_J_Ear", "cf_J_Eye", "cf_J_Mayu",
                         "cf_J_look", "cf_J_pupil", "cf_J_eye", "N_")):
            faces.add(n)

    for b in arm.data.bones:
        name = b.name
        if name.startswith(("IKB_", "CAR_")):
            mech.assign(b)
        elif name.startswith("CTRL_f_"):
            if finger is not None:
                finger.assign(b)
                _paint(arm, name, "THEME06")
        elif name.startswith(("STU_", "IK_", "Toe_")):
            ik.assign(b)
            _paint(arm, name, "THEME09")
        elif name.startswith("CTRL_"):
            ctrl.assign(b)
            _paint(arm, name, "THEME04")
        elif name in faces:
            face.assign(b)
        elif name.startswith(("cf_hit", "k_f_", "a_n_", "n_")) or not b.use_deform:
            misc.assign(b)
        else:
            deform.assign(b)

    # 变形骨默认**隐藏**：693 根骨一起用八面体显示，视觉上就是一地碎块，
    # 完全盖住控制环（用户反馈「看着不专业」）。要看得去面板里勾「变形」。
    pairs = [(face, False), (misc, False), (mech, False),
             (deform, False), (ctrl, True), (ik, True)]
    if finger is not None:
        pairs.append((finger, False))
    for coll, vis in pairs:
        try:
            coll.is_visible = vis
        except Exception:
            pass


# --------------------------------------------------------------------------
# 极向角
# --------------------------------------------------------------------------

def _solve_pole(arm, con_name, side, is_leg):
    """用合成弯曲姿势反解 pole_angle。"""
    con = _con(arm, con_name)
    if con is None:
        return None

    ch = (LEG if is_leg else ARM)[side]
    if is_leg:
        root_bone, mid_bone = mech_thigh(side), mech_calf(side)
        ctrl, pole = foot_ctrl(side), knee_ctrl(side)
        desired = Vector((0.0, -1.0, 0.0))
    else:
        root_bone, mid_bone = mech_upper(side), mech_lower(side)
        ctrl, pole = hand_ctrl(side), elbow_ctrl(side)
        desired = Vector((0.0, 1.0, 0.0))

    ad = arm.animation_data
    saved_action = ad.action if ad else None
    if ad is not None:
        ad.action = None

    saved_basis = {pb.name: pb.matrix_basis.copy() for pb in arm.pose.bones}
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix()

    watch = [root_bone, mid_bone, ctrl, pole]
    prev = {}
    for nm in watch:
        pb = arm.pose.bones.get(nm)
        if pb is not None:
            prev[nm] = pb.matrix_basis.copy()

    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()

    _mute_rig(arm, True)
    try:
        root_pb = arm.pose.bones[root_bone]
        mid_pb = arm.pose.bones[mid_bone]

        start0 = root_pb.head.copy()
        end0 = mid_pb.tail.copy()
        chain_dir = (end0 - start0)
        axis = chain_dir.cross(desired)
        if axis.length < 1e-5:
            axis = Vector((1.0, 0.0, 0.0))
        axis.normalize()

        def bend_with(sign):
            r_axis = root_pb.bone.matrix_local.to_3x3().inverted() @ axis
            m_axis = mid_pb.bone.matrix_local.to_3x3().inverted() @ axis
            root_pb.matrix_basis = Matrix.Rotation(math.radians(30.0 * sign), 4, r_axis)
            mid_pb.matrix_basis = Matrix.Rotation(math.radians(-60.0 * sign), 4, m_axis)
            dg2 = bpy.context.evaluated_depsgraph_get()
            dg2.update()
            ev = arm.evaluated_get(dg2)
            return (ev.pose.bones[mid_bone].head.copy(),
                    ev.pose.bones[mid_bone].tail.copy(),
                    ev.pose.bones[root_bone].head.copy())

        knee, ankle, start = bend_with(1.0)
        if _bend_dir(knee, start, ankle, desired).dot(desired) < 0.0:
            knee, ankle, start = bend_with(-1.0)

        bend = _bend_dir(knee, start, ankle, desired)
        _move_to(arm.pose.bones[pole], knee + bend * 0.38)
        _move_to(arm.pose.bones[ctrl], ankle)

        con.mute = False

        def probe(deg):
            con.pole_angle = math.radians(deg)
            dg2 = bpy.context.evaluated_depsgraph_get()
            dg2.update()
            pos = arm.evaluated_get(dg2).pose.bones[mid_bone].head
            return (pos - knee).length, _bend_dir(pos, start, ankle, desired)

        best = (1e9, 0.0)
        for deg in range(-180, 181, 20):
            e, _ = probe(deg)
            if e < best[0]:
                best = (e, float(deg))
        base = best[1]
        for deg in range(int(base) - 18, int(base) + 19, 2):
            e, _ = probe(deg)
            if e < best[0]:
                best = (e, float(deg))

        e, bd = probe(best[1])
        if bd.dot(bend) < 0.0:
            alt = best[1] + 180.0
            if alt > 180.0:
                alt -= 360.0
            e2, bd2 = probe(alt)
            if bd2.dot(bend) > bd.dot(bend):
                best = (e2, alt)
        con.pole_angle = math.radians(best[1])
        return best[1]
    finally:
        _mute_rig(arm, False)
        for nm, m in prev.items():
            pb = arm.pose.bones.get(nm)
            if pb is not None:
                pb.matrix_basis = m.copy()
        for nm, m in saved_basis.items():
            pb = arm.pose.bones.get(nm)
            if pb is not None:
                pb.matrix_basis = m
        if ad is not None:
            ad.action = saved_action
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()


# --------------------------------------------------------------------------
# 打帧：控制骨 + IK 目标
# --------------------------------------------------------------------------

def _mute_rig(arm, mute):
    """临时静音 IK/FK 约束。

    关节补偿（HS2_FIX_*）不参与：它是用户开关的状态，清 action 后如果要还原姿势，
    把这些约束一起解静音会让 dam 骨按错误比例转，看起来就是「整个人被拧了」。
    """
    for c in _rig_constraints(arm):
        if c.name.startswith((CON_PREFIX + "FIX_",
                              CON_PREFIX + "FIXH",
                              CON_PREFIX + "FIXR")):
            continue
        c.mute = mute


def controlled_bones(arm):
    """[(控制名, 目标骨, 父控制名)]，按层级顺序。"""
    out = []
    for cname, bone, parent, _s, _z in BODY_CTRL:
        if arm.data.bones.get(cname) is not None:
            out.append((cname, bone, parent))
    for side in ("L", "R"):
        for suffix, bone_t, parent_t, _s, _z, _o in SIDE_CTRL:
            cname = "CTRL_%s_%s" % (suffix, side)
            if arm.data.bones.get(cname) is not None:
                out.append((cname, bone_t.format(S=side), parent_t.format(S=side)))
        if arm.get("hs2_fingers"):
            for fname, count in FINGERS:
                for i in range(1, count + 1):
                    cname = "CTRL_f_%s%02d_%s" % (fname, i, side)
                    if arm.data.bones.get(cname) is not None:
                        parent = ("CTRL_hand_" + side) if i == 1 else \
                            "CTRL_f_%s%02d_%s" % (fname, i - 1, side)
                        out.append((cname, "cf_J_Hand_%s%02d_%s" % (fname, i, side),
                                    parent))
    return out


def _rel_rest(arm, cname, parent):
    c = _rest(arm, cname)
    if parent:
        p = _rest(arm, parent)
        if p is not None:
            return p.inverted() @ c
    return c


def _limb_targets(arm, side, is_leg):
    ch = (LEG if is_leg else ARM)[side]
    if is_leg:
        end, mid, root_bone = ch["foot"], ch["calf"], ch["thigh"]
        ctrl, pole = foot_ctrl(side), knee_ctrl(side)
        fallback = Vector((0.0, -1.0, 0.0))
    else:
        end, mid, root_bone = ch["hand"], ch["lower"], ch["upper"]
        ctrl, pole = hand_ctrl(side), elbow_ctrl(side)
        fallback = Vector((0.0, 1.0, 0.0))

    end_pb = arm.pose.bones[end]
    mid_pb = arm.pose.bones[mid]
    root_pb = arm.pose.bones[root_bone]

    bend = _bend_dir(mid_pb.head, root_pb.head, end_pb.head, fallback)
    return ctrl, end_pb.matrix.copy(), pole, mid_pb.head + bend * 0.38


def key_targets(arm, start=None, end=None):
    """把 FK 控制骨 + IK 目标逐帧钉在当前动作上（一次遍历，批量写曲线）。"""
    if not has_rig(arm):
        return 0
    if arm.animation_data is None or arm.animation_data.action is None:
        return 0

    scn = bpy.context.scene
    start = scn.frame_start if start is None else start
    end = scn.frame_end if end is None else end
    cur = scn.frame_current

    ctrls = controlled_bones(arm)
    ik_names = ([foot_ctrl(s) for s in LEG] + [toe_ctrl(s) for s in LEG]
                + [hand_ctrl(s) for s in ARM]
                + [knee_ctrl(s) for s in LEG] + [elbow_ctrl(s) for s in ARM])
    rel = {c: _rel_rest(arm, c, p) for c, _b, p in ctrls}

    data = {}
    for c, _b, _p in ctrls:
        data[c] = ([], [])
    for n in ik_names:
        data[n] = ([], [])

    _mute_rig(arm, True)
    try:
        for f in range(start, end + 1):
            scn.frame_set(f)
            worlds = {}
            for cname, bone, parent in ctrls:
                pb = arm.pose.bones.get(bone)
                if pb is None:
                    continue
                worlds[bone] = pb.matrix.copy()
                worlds[cname] = worlds[bone]
                pb_c = arm.pose.bones.get(cname)
                if pb_c is None:
                    continue
                base = worlds.get(parent) if parent else None
                if base is None and parent:
                    pbp = arm.pose.bones.get(parent)
                    base = pbp.matrix.copy() if pbp is not None else Matrix()
                base = base or Matrix()
                m = base @ rel[cname]
                pb_c.matrix_basis = m.inverted() @ worlds[bone]
                data[cname][0].append(pb_c.location.copy())
                data[cname][1].append(pb_c.rotation_quaternion.copy())

            for side in LEG:
                ctrl, mat, pole, ppos = _limb_targets(arm, side, True)
                _set_world_record(arm, ctrl, mat, data)
                _move_to(arm.pose.bones[pole], ppos)
                _record(arm, pole, data)
                tpb = arm.pose.bones.get(toe_ctrl(side))
                if tpb is not None:
                    _set_child_world(tpb, mat,
                                     arm.pose.bones[LEG[side]["toe"]].matrix.copy())
                    _record(arm, toe_ctrl(side), data)
            for side in ARM:
                ctrl, mat, pole, ppos = _limb_targets(arm, side, False)
                _set_world_record(arm, ctrl, mat, data)
                _move_to(arm.pose.bones[pole], ppos)
                _record(arm, pole, data)
    finally:
        _mute_rig(arm, False)
        scn.frame_set(cur)

    _write_curves(arm, data, start)
    return len(range(start, end + 1))


def _record(arm, cname, data):
    pb = arm.pose.bones.get(cname)
    if pb is None:
        return
    data[cname][0].append(pb.location.copy())
    data[cname][1].append(pb.rotation_quaternion.copy())


def _set_world_record(arm, cname, mat, data):
    pb = arm.pose.bones.get(cname)
    if pb is None:
        return
    pb.matrix = mat
    data[cname][0].append(pb.location.copy())
    data[cname][1].append(pb.rotation_quaternion.copy())


def _write_curves(arm, data, start):
    from . import compat

    act = arm.animation_data.action
    curves = compat.bag(act, arm)
    for cname, (locs, quats) in data.items():
        if not locs:
            continue
        dp = 'pose.bones["%s"]' % cname
        for path, dim, vecs in ((".location", 3, locs),
                                (".rotation_quaternion", 4, quats)):
            full = dp + path
            for i in range(dim):
                old = compat.find_fcurve(curves, full, i)
                if old is not None:
                    curves.remove(old)
                fc = compat.new_fcurve(curves, full, i, cname)
                n = len(vecs)
                fc.keyframe_points.add(count=n)
                flat = []
                for k in range(n):
                    flat.extend((start + k, vecs[k][i]))
                fc.keyframe_points.foreach_set("co", flat)
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
                fc.update()


def snap_ik_to_fk(arm):
    """IK 目标吸附到当前**看到的**姿势（FK 调完再开 IK 不跳）。

    读的是求值后的姿势（ev），不是静音约束后的 fcurve —— 用户看到哪个就该吸附哪个。
    """
    if not has_rig(arm):
        return 0
    was = ik_state(arm)
    set_ik(arm, False)
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = arm.evaluated_get(dg)

    for side in LEG:
        ch = LEG[side]
        ankle = ev.pose.bones[ch["foot"]]
        knee = ev.pose.bones[ch["calf"]].head.copy()
        hip = ev.pose.bones[ch["thigh"]].head.copy()
        bend = _bend_dir(knee, hip, ankle.head.copy(), Vector((0.0, -1.0, 0.0)))
        pb = arm.pose.bones.get(foot_ctrl(side))
        if pb is not None:
            pb.matrix = ankle.matrix.copy()
        _move_to(arm.pose.bones[knee_ctrl(side)], knee + bend * 0.38)
        tpb = arm.pose.bones.get(toe_ctrl(side))
        if tpb is not None:
            _set_child_world(tpb, ankle.matrix.copy(),
                             ev.pose.bones[ch["toe"]].matrix.copy())

    for side in ARM:
        ch = ARM[side]
        wrist = ev.pose.bones[ch["hand"]]
        elbow = ev.pose.bones[ch["lower"]].head.copy()
        shoulder = ev.pose.bones[ch["upper"]].head.copy()
        bend = _bend_dir(elbow, shoulder, wrist.head.copy(), Vector((0.0, 1.0, 0.0)))
        pb = arm.pose.bones.get(hand_ctrl(side))
        if pb is not None:
            pb.matrix = wrist.matrix.copy()
        _move_to(arm.pose.bones[elbow_ctrl(side)], elbow + bend * 0.38)

    bpy.context.view_layer.update()
    set_ik(arm, was)
    return 1


def snap_fk_to_ik(arm, key=True):
    """把 IK 的结果写回 FK 控制骨（切回 FK 不跳）。"""
    if not has_rig(arm):
        return 0
    set_ik(arm, True)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ev = arm.evaluated_get(dg)

    ctrls = controlled_bones(arm)
    rel = {c: _rel_rest(arm, c, p) for c, _b, p in ctrls}
    worlds = {}
    n = 0
    for cname, bone, parent in ctrls:
        src = ev.pose.bones.get(bone)
        dst = arm.pose.bones.get(cname)
        if src is None or dst is None:
            continue
        worlds[bone] = src.matrix.copy()
        worlds[cname] = worlds[bone]
        base = worlds.get(parent) if parent else None
        if base is None and parent:
            pbp = arm.pose.bones.get(parent)
            base = pbp.matrix.copy() if pbp is not None else Matrix()
        base = base or Matrix()
        m = base @ rel[cname]
        dst.matrix_basis = m.inverted() @ worlds[bone]
        if key:
            dst.keyframe_insert("location", group=dst.name)
            dst.keyframe_insert("rotation_quaternion", group=dst.name)
        n += 1
    set_ik(arm, False)
    return n


def ik_state(arm):
    for side in LEG:
        pb = arm.pose.bones.get(foot_ctrl(side))
        if pb is not None and "ik" in pb.keys():
            return bool(pb["ik"] > 0.5)
    return True


def set_ik(arm, on):
    if not has_rig(arm):
        return 0
    n = 0
    for side in LEG:
        pb = arm.pose.bones.get(foot_ctrl(side))
        if pb is not None:
            pb["ik"] = 1.0 if on else 0.0
            n += 1
    for side in ARM:
        pb = arm.pose.bones.get(hand_ctrl(side))
        if pb is not None:
            pb["ik"] = 1.0 if on else 0.0
            n += 1
    # 自定义属性变了要让驱动器重算，否则约束影响还是旧值
    arm.update_tag()
    return n


def set_joint_fix(arm, on):
    """关节补偿开关。"""
    n = 0
    for pb in arm.pose.bones:
        for c in pb.constraints:
            if c.name.startswith(CON_PREFIX + "FIX_"):
                c.mute = not on
                n += 1
    return n


# --------------------------------------------------------------------------
# Studio 分组开关（对应 studio 的 ActiveIK / ActiveFK）
# --------------------------------------------------------------------------

STUDIO_GROUPS = ("Body", "Arm_L", "Arm_R", "Leg_L", "Leg_R")


def _studio_offset_bones(group):
    if group == "Body":
        return [body_ctrl()]
    if group.startswith("Arm_"):
        return [shoulder_ctrl(group[-1]), elbow_ctrl(group[-1])]
    if group.startswith("Leg_"):
        return [thigh_ctrl(group[-1]), knee_ctrl(group[-1])]
    return []


def set_studio_group(arm, group, on):
    """开/关一个 Studio IK 分组。

    * Body / Shoulder / Thigh 是「偏移父级」：关掉时把 matrix_basis 存起来
      并归零，打开时恢复。
    * Arm / Leg 同时把对应手脚的 ``ik`` 属性切到 0/1（studio 的 ActiveIK）。
    """
    if not has_rig(arm):
        return 0
    n = 0
    for name in _studio_offset_bones(group):
        pb = arm.pose.bones.get(name)
        if pb is None:
            continue
        if on:
            saved = pb.get("studio_saved")
            if saved is not None and len(saved) == 16:
                pb.matrix_basis = Matrix((saved[0:4], saved[4:8],
                                          saved[8:12], saved[12:16]))
            pb["studio_on"] = 1
        else:
            if pb.get("studio_on", 1):
                pb["studio_saved"] = [v for row in pb.matrix_basis for v in row]
                pb.matrix_basis = Matrix()
            pb["studio_on"] = 0
        n += 1
    side = group[-1]
    if group.startswith("Arm_"):
        pb = arm.pose.bones.get(hand_ctrl(side))
        if pb is not None:
            pb["ik"] = 1.0 if on else 0.0
            n += 1
    elif group.startswith("Leg_"):
        pb = arm.pose.bones.get(foot_ctrl(side))
        if pb is not None:
            pb["ik"] = 1.0 if on else 0.0
            n += 1
    arm.update_tag()
    return n


def restore_studio_offsets(arm):
    """把之前关掉的分组偏移恢复回来（打开分组时用）。"""
    n = 0
    for g in STUDIO_GROUPS:
        for name in _studio_offset_bones(g):
            pb = arm.pose.bones.get(name)
            if pb is None or pb.get("studio_on", 1):
                continue
            saved = pb.get("studio_saved")
            if saved is not None and len(saved) == 16:
                pb.matrix_basis = Matrix((saved[0:4], saved[4:8],
                                          saved[8:12], saved[12:16]))
                pb["studio_on"] = 1
                n += 1
    return n


def deform_bone_names(arm):
    return [b.name for b in arm.data.bones if b.use_deform]
