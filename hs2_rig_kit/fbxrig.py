"""工作室（HS2MeshExporter）FBX 角色：修骨架 + 对齐默认骨架姿势 + 套动作。

一次解决三件事：

1. **骨骼错位**
   HS2MeshExporter 导出的 FBX 里，BindPose 的全局矩阵带着 0.9 左右的身体
   缩放（角色的身高/体型），而 Blender 自带导入器建 rest 时用的是
   ``bind_matrix.normalized()`` —— 缩放被扔掉，骨头就比蒙皮大了 1/0.9，
   手臂骨头比手臂网格高 0.15m，动画一装就绕着错误的枢轴转。
   这里直接拿 BindPose 的平移/旋转重建 rest，骨头回到蒙皮里，一格不差。

2. **默认姿势和 p_cf_anim 不一致**
   工作室导出的 bind 是"手臂下垂、手指微弯"的姿势，插件默认骨架
   p_cf_anim 是手臂平举的 T-pose。这里把每根骨的世界朝向直接抄成默认
   骨架的朝向，她的站姿、手型就和插件默认角色完全一样。

3. **套动作**
   游戏动作是「骨骼世界朝向的绝对值」，所以只要把每帧每根骨的世界朝向照
   抄过来，再按她自己的骨长/比例反解成局部值，动作就是无损的：不会出现
   手臂拉成带状、胸/头塌陷。根位移按身高比缩放，脚不会离地。

L/R 会自动按世界 X 位置配对：工作室导出把 X 镜像了（FBX 里的
``*_L`` 落在 +X，而游戏里 ``*_L`` 在 -X），照名字直接套会让手臂横穿身体。
"""

import os

import bpy
from mathutils import Matrix, Quaternion, Vector

from . import bdata, compat
from .ripper import mathutil as mu


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _base_name(name):
    """去掉 Blender 的 .001 后缀。"""
    if len(name) > 4 and name[-4] == "." and name[-3:].isdigit():
        return name[:-4]
    return name


def _swap_lr(name):
    if name.endswith("_L"):
        return name[:-2] + "_R"
    if name.endswith("_R"):
        return name[:-2] + "_L"
    return name


def _q(mat):
    return mat.to_3x3().normalized().to_quaternion()


def bone_order(arm):
    """父级优先的骨头顺序（矩阵反解必须按这个顺序算）。"""
    out = []
    seen = set()

    def walk(b):
        if b.name in seen:
            return
        seen.add(b.name)
        out.append(b)
        for c in b.children:
            walk(c)

    for b in arm.data.bones:
        if b.parent is None:
            walk(b)
    for b in arm.data.bones:          # 兜底：孤儿骨
        walk(b)
    return out


# --------------------------------------------------------------------------
# FBX BindPose
# --------------------------------------------------------------------------

_BIND_CACHE = {}


def _parse_fbx_module():
    try:
        from io_scene_fbx import parse_fbx
        return parse_fbx
    except Exception:
        pass
    import sys
    cands = []
    try:
        cands.append(bpy.utils.system_resource("SCRIPTS", path="addons_core"))
    except Exception:
        pass
    try:
        cands.append(os.path.join(bpy.utils.resource_path("LOCAL"),
                                  "scripts", "addons_core"))
    except Exception:
        pass
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "io_scene_fbx")):
            if c not in sys.path:
                sys.path.append(c)
            try:
                from io_scene_fbx import parse_fbx
                return parse_fbx
            except Exception:
                continue
    return None


def _elem(elem, name):
    for e in elem.elems:
        if e.id == name:
            return e
    return None


def read_bindpose(path):
    """从 FBX 里读 BindPose：``{节点名: 4x4 全局矩阵（FBX 空间）}``。

    解析结果按文件大小/时间缓存，重复导入同一个 FBX 不会重复解析。
    """
    key = os.path.abspath(path)
    try:
        st = os.stat(key)
    except OSError:
        return {}
    stamp = (st.st_size, int(st.st_mtime))
    hit = _BIND_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]

    bind = {}
    parse_fbx = _parse_fbx_module()
    if parse_fbx is not None:
        try:
            root, _ver = parse_fbx.parse(path)
            objs = _elem(root, b"Objects")
            if objs is not None:
                id2name = {}
                for e in objs.elems:
                    if len(e.props) >= 2 and isinstance(e.props[1], bytes):
                        nm = e.props[1].split(b"\x00")[0].decode("utf-8", "replace")
                        id2name[e.props[0]] = nm
                for e in objs.elems:
                    if e.id != b"Pose":
                        continue
                    if len(e.props) < 3 or e.props[2] != b"BindPose":
                        continue
                    for pn in e.elems:
                        if pn.id != b"PoseNode":
                            continue
                        ne = _elem(pn, b"Node")
                        me = _elem(pn, b"Matrix")
                        if ne is None or me is None:
                            continue
                        m = me.props[0]
                        if len(m) < 16:
                            continue
                        nm = id2name.get(ne.props[0])
                        if not nm:
                            continue
                        bind[nm] = Matrix((
                            (m[0], m[1], m[2], m[3]),
                            (m[4], m[5], m[6], m[7]),
                            (m[8], m[9], m[10], m[11]),
                            (m[12], m[13], m[14], m[15]),
                        )).transposed()
        except Exception as e:
            print("[HS2] 读 BindPose 失败：%s" % e)
    _BIND_CACHE[key] = (stamp, bind)
    return bind


# --------------------------------------------------------------------------
# 1. 按 BindPose 修 rest
# --------------------------------------------------------------------------

def fix_rest(arm, bind):
    """按 BindPose 重建 rest 骨：位置取 BindPose 平移，朝向取 BindPose 旋转。

    返回统计：修了多少根骨、平均移动了多少（用来验证是不是真的动了）。

    Blender 的 FBX 导入器把轴转换（Y-up→Z-up）放在**骨架物体**的
    matrix_world 上，骨头自己的 rest 矩阵就是 FBX 的全局矩阵，所以这里
    直接照搬 BindPose，不能再自己转一次基。
    """
    if not bind:
        return {"fixed": 0, "avg": 0.0, "reason": "FBX 里没有 BindPose"}

    targets = {}
    for b in arm.data.bones:
        # 先按原名找：HS2 的阴道骨本来就叫 cf_J_Vagina_L.001 这种，
        # 把 .001 当 Blender 的重名后缀去掉会去查一根不存在的骨。
        m = bind.get(b.name)
        if m is None:
            m = bind.get(_base_name(b.name))
        if m is None:
            continue
        targets[b.name] = m
    if not targets:
        return {"fixed": 0, "avg": 0.0, "reason": "BindPose 和骨头名对不上"}

    # 骨长：和 Better FBX 的自动骨朝向一致 —— 非叶子骨取所有子骨 head 的
    # 平均值，叶子骨沿父骨方向延长一倍（纯显示，不影响形变）。
    length = {}
    for b in arm.data.bones:
        t = targets.get(b.name)
        if t is None:
            continue
        head = t.translation
        kids = [targets[c.name].translation for c in b.children
                if c.name in targets]
        if kids:
            best = (sum(kids, Vector()) / len(kids) - head).length
        else:
            par = b.parent
            pt = targets.get(par.name) if par is not None else None
            best = ((head - pt.translation).length if pt is not None else 0.0)
        if best is None or best < 1e-3:
            best = b.length or 0.05
        length[b.name] = max(best, 0.008)

    moved = []
    prev_active = bpy.context.view_layer.objects.active
    prev_mode = (bpy.context.object.mode
                 if bpy.context.object is not None else "OBJECT")
    try:
        if prev_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for ob in bpy.context.selected_objects:
            ob.select_set(False)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")

        ebs = arm.data.edit_bones
        # 先全部断开连接，否则改子骨 head 会被父骨 tail 拽回去
        for eb in ebs:
            eb.use_connect = False

        for b in bone_order(arm):
            t = targets.get(b.name)
            if t is None:
                continue
            eb = ebs.get(b.name)
            if eb is None:
                continue
            head = t.translation.copy()
            y = Vector((t[0][1], t[1][1], t[2][1]))
            z = Vector((t[0][2], t[1][2], t[2][2]))
            if y.length < 1e-8:
                y = Vector((0.0, 1.0, 0.0))
            y.normalize()
            moved.append((eb.head - head).length)
            eb.head = head
            eb.tail = head + y * length.get(b.name, 0.05)
            if z.length > 1e-8:
                try:
                    eb.align_roll(z)
                except Exception:
                    pass
        bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        try:
            if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        if prev_active is not None:
            try:
                bpy.context.view_layer.objects.active = prev_active
            except Exception:
                pass

    avg = sum(moved) / len(moved) if moved else 0.0
    return {"fixed": len(moved), "avg": avg}


# --------------------------------------------------------------------------
# 1b. 根骨收口：p_cf_anim 必须是根
# --------------------------------------------------------------------------

ROOT_BONE = "p_cf_anim"


def prune_root(arm, root=ROOT_BONE):
    """把 ``root`` 之上的多余根骨删掉（工作室导出会在外面套一层 BodyTop）。

    只删「没有蒙皮权重」的上层骨：权重骨一律留着，否则蒙皮会缺一块。
    返回删掉的骨名列表。
    """
    target = arm.data.bones.get(root)
    if target is None:
        return []

    # 哪些骨有蒙皮权重
    weighted = set()
    for ob in bpy.data.objects:
        if ob.type != "MESH" or ob.parent is not arm:
            continue
        for g in ob.vertex_groups:
            weighted.add(g.name)

    above = []
    p = target.parent
    while p is not None:
        above.append(p.name)
        p = p.parent
    if not above:
        return []
    for name in above:
        if name in weighted:
            return []       # 上层骨在用，不动

    prev_active = bpy.context.view_layer.objects.active
    try:
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for ob in bpy.context.selected_objects:
            ob.select_set(False)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        ebs = arm.data.edit_bones
        eb = ebs.get(root)
        if eb is not None:
            eb.parent = None          # 先断开，位置不会变（edit bone 是绝对坐标）
        for name in above:
            b = ebs.get(name)
            if b is not None:
                ebs.remove(b)
        bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        try:
            if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        if prev_active is not None:
            try:
                bpy.context.view_layer.objects.active = prev_active
            except Exception:
                pass
    # 清理只剩空壳的顶点组名（删掉的骨如果建过组）
    print("[HS2] 删掉 p_cf_anim 之上的根骨：%s" % above)
    return above


# --------------------------------------------------------------------------
# 2. 骨头配对（含 L/R 镜像）
# --------------------------------------------------------------------------

def skeleton_index(skel):
    out = {}
    for i, b in enumerate(skel.bones):
        out.setdefault(b.name or "bone", i)
    return out


def bone_map(arm, skel, scale=0.1):
    """``{角色骨头名: 游戏骨架下标}``。

    默认按名字配；名字带 _L/_R 的按世界 X 位置定左右 —— 工作室导出把 X 轴
    镜像了，FBX 的 ``cf_J_Hand_L`` 在 +X，游戏里 ``cf_J_Hand_L`` 在 -X，
    照名字套会让手臂横穿身体。
    """
    idx = skeleton_index(skel)
    if not idx:
        return {}
    gw = bdata.world_matrices(skel, scale)
    s = float(scale) or 0.1
    out = {}
    for b in arm.data.bones:
        # 先按原名，其次按去掉 Blender 重名后缀的名字
        n = b.name if b.name in idx else _base_name(b.name)
        alt = _swap_lr(n)
        i = idx.get(n)
        j = idx.get(alt) if alt != n else None
        if i is None and j is None:
            continue
        if i is not None and j is not None:
            # 两边都换成原始单位再比（游戏世界矩阵是米，骨架局部就是原始单位）
            x = b.head_local.x
            d0 = abs(x - gw[i][0][3] / s)
            d1 = abs(x - gw[j][0][3] / s)
            if d1 + 0.2 < d0:
                i = j
        elif i is None:
            i = j
        out[b.name] = i
    return out


def _height_ratio(arm, skel, gw, bmap, scale=0.1):
    """角色和默认骨架的身高比（用头骨到髋骨的距离算），根位移按它缩放。

    两边都换算成**原始单位**（FBX/游戏数据的单位，1 = 0.1m）再比：
    角色骨头在骨架局部空间里就是原始单位，游戏侧的世界矩阵是米，
    要除以游戏数据自己的 ``scale`` 才是同一把尺子。骨架物体的缩放
    （0.1 或别的值）只影响世界坐标，不能混进来。
    """
    idx = skeleton_index(skel)
    i_head = idx.get("cf_J_Head")
    i_hips = idx.get("cf_J_Hips")
    if i_head is None or i_hips is None:
        return 1.0
    c_head = c_hips = None
    for name, i in bmap.items():
        b = arm.data.bones.get(name)
        if b is None:
            continue
        if i == i_head:
            c_head = b.head_local
        elif i == i_hips:
            c_hips = b.head_local
    if c_head is None or c_hips is None:
        return 1.0
    d_char = (c_head - c_hips).length                       # 原始单位
    s = float(scale) or 0.1
    d_game = (gw[i_head].translation - gw[i_hips].translation).length / s
    if d_game < 1e-6 or d_char < 1e-6:
        return 1.0
    return d_char / d_game


# --------------------------------------------------------------------------
# 3. 姿势 / 动作
# --------------------------------------------------------------------------

def _frame_worlds(skel, clip, n, scale, tracks):
    """逐帧累乘出游戏骨架每根骨的世界矩阵（**Unity 空间**，未转基）。

    工作室 FBX 的骨头 rest 就活在这个空间里（Y-up），所以朝向直接照抄，
    不需要再做基底变换 —— 轴转换由骨架物体的 matrix_world 负责。
    """
    bones = skel.bones
    rest = []
    for b in bones:
        rest.append(((b.pos[0] * scale, b.pos[1] * scale, b.pos[2] * scale),
                     b.rot, b.scale))
    out = []
    for f in range(n):
        world = [None] * len(bones)
        for i, b in enumerate(bones):
            pos, rot, scl = rest[i]
            tr = tracks.get(b.path) if tracks else None
            if tr is not None:
                p = tr.get("pos")
                r = tr.get("rot")
                s = tr.get("scale")
                if p is not None:
                    pos = (p[f][0] * scale, p[f][1] * scale, p[f][2] * scale)
                if r is not None:
                    rot = tuple(r[f])
                if s is not None:
                    scl = tuple(s[f])
            local = mu.trs_to_mat(pos, rot, scl)
            world[i] = mu.mat_mul(world[b.parent], local) if b.parent >= 0 else local
        out.append(world)
    return out


def apply(arm, skel, clip=None, name=None, scale=0.1, keyframe=None):
    """把默认骨架的姿势（clip=None）或一条动作装到角色骨架上。

    返回 ``(action, info)``；``clip=None`` 时只摆姿势、不建 action。
    """
    bmap = bone_map(arm, skel, scale)
    if not bmap:
        raise RuntimeError("骨头名和 HS2 默认骨架对不上，没法对齐")

    order = bone_order(arm)
    rest = {b.name: b.matrix_local.copy() for b in arm.data.bones}

    gw = [Matrix(m) for m in bdata.world_matrices(skel, scale)]
    ratio = _height_ratio(arm, skel, gw, bmap, scale)
    raw_rest = _frame_worlds(skel, None, 1, scale, {})[0]

    tracks = clip.tracks if clip is not None else {}
    n = clip.frame_count if clip is not None else 1
    if clip is not None and n <= 0:
        raise RuntimeError("这个动作没有帧")

    # 有位移轨道的骨：位移要按「父骨空间」的局部增量来用。
    # 直接套世界坐标是不行的 —— 骨长/比例跟默认骨架不同，会把手指从手上
    # 撕下来（静止的位移轨道会把骨头钉在 bind 位置）。
    pos_tracks = {}
    for cname, i in bmap.items():
        sb = skel.bones[i]
        tr = tracks.get(sb.path) if tracks else None
        p = tr.get("pos") if tr is not None else None
        if p is not None:
            pos_tracks[cname] = (p, sb.pos)

    raw_worlds = _frame_worlds(skel, clip, n, scale, tracks) if clip is not None else None

    # 有 clip 就必须落成 action —— 这条路径上没有别的表达方式
    if clip is not None:
        keyframe = True
    elif keyframe is None:
        keyframe = False

    locs = {c: [] for c in pos_tracks}
    quats = {c: [] for c in bmap}
    scales = {c: [] for c in bmap}
    # 游戏动作的位置是「原始单位」（1 = 0.1m）。骨架局部空间用的也是原始
    # 单位（骨头 rest 就是 BindPose 的数值），所以位移直接照搬、只乘身高比。
    # 骨架物体的缩放只决定最终显示多大，不能混进这里 —— 混进来会让
    # 0.1 以外的骨架（比如 Better FBX 的 0.01）根位移差 10 倍。
    # 缩放：游戏动作会拉伸手肘/膝盖的 dam 补偿骨（cf_J_ArmLow02_dam 实测到
    # 1.09），丢掉这块关节会塌。这里按「世界缩放 / 静止缩放」照抄。
    rest_scale = {c: Vector(Matrix(raw_rest[i]).to_scale()) for c, i in bmap.items()}
    prev_q = {}
    world = {}
    pose_q = {}
    pose_d = {}
    pose_s = {}

    for f in range(n):
        # 朝向用 Unity 空间的原始矩阵（FBX 骨空间就是这个）；位移用父空间
        # 的局部增量，跟骨长无关。
        fw = raw_worlds[f] if raw_worlds is not None else raw_rest
        for cname, i in bmap.items():
            mm = Matrix(fw[i])
            pose_q[cname] = _q(mm)
            s = mm.to_scale()
            r0 = rest_scale[cname]
            pose_s[cname] = Vector((
                s.x / r0.x if abs(r0.x) > 1e-6 else 1.0,
                s.y / r0.y if abs(r0.y) > 1e-6 else 1.0,
                s.z / r0.z if abs(r0.z) > 1e-6 else 1.0))
        for cname, (p, bpos) in pos_tracks.items():
            pose_d[cname] = Vector(((p[f][0] - bpos[0]) * ratio,
                                    (p[f][1] - bpos[1]) * ratio,
                                    (p[f][2] - bpos[2]) * ratio))

        for b in order:
            nb = b.name
            par = b.parent
            A = rest[par.name].inverted() @ rest[nb] if par is not None else rest[nb]
            i = bmap.get(nb)
            if i is None:
                # 游戏骨架里没有的骨（cf_hit_* 之类）：跟着父骨刚性走
                world[nb] = (world[par.name] @ A) if par is not None else rest[nb]
                continue

            qt = pose_q[nb]
            d = pose_d.get(nb)
            if d is not None:
                # 位移加在父骨空间的静止偏移上
                if par is not None:
                    base = A.translation + d
                    pos = world[par.name].translation + (world[par.name].to_3x3()
                                                         @ base)
                else:
                    pos = rest[nb].translation + d
            elif par is not None:
                pos = (world[par.name] @ A).translation
            else:
                pos = rest[nb].translation.copy()

            m = qt.to_matrix().to_4x4()
            sc = pose_s[nb]
            try:
                m = m @ Matrix.Diagonal((sc.x, sc.y, sc.z, 1.0))
            except Exception:
                pass
            m.translation = pos
            world[nb] = m

            basis = A.inverted() @ ((world[par.name].inverted() if par is not None
                                     else Matrix()) @ m)
            loc, q, scl = basis.decompose()
            q.normalize()
            prev = prev_q.get(nb)
            if prev is not None and prev.dot(q) < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
            prev_q[nb] = q
            quats[nb].append((q.w, q.x, q.y, q.z))
            if nb in locs:
                locs[nb].append((loc.x, loc.y, loc.z))
            if nb in scales:
                scales[nb].append((scl.x, scl.y, scl.z))

    info = {
        "frames": n,
        "bones": len(bmap),
        "unmapped": len(arm.data.bones) - len(bmap),
        "height_ratio": ratio,
        "scale": scale,
        "fps": getattr(clip, "fps", None) or 24.0,
        "duration": getattr(clip, "duration", 0.0),
    }

    if clip is None or not keyframe:
        # 只摆姿势：清掉 action，把值直接写到 pose 上
        ad = arm.animation_data
        if ad is not None:
            ad.action = None
        for b in arm.data.bones:
            pb = arm.pose.bones.get(b.name)
            if pb is None:
                continue
            pb.rotation_mode = "QUATERNION"
            q = quats.get(b.name)
            if q:
                pb.rotation_quaternion = q[0]
            else:
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            if b.name in locs:
                pb.location = locs[b.name][0]
            else:
                pb.location = (0.0, 0.0, 0.0)
            s = scales.get(b.name)
            pb.scale = s[0] if s else (1.0, 1.0, 1.0)
        bpy.context.view_layer.update()
        return None, info

    act = compat.new_action(arm, name or ("HS2_" + (clip.name or "clip")))
    bag = compat.bag(act, arm)
    groups = {}
    for cname in bmap:
        try:
            groups[cname] = act.groups.new(cname)
        except Exception:
            groups = {}
            break

    for cname, vals in quats.items():
        base = 'pose.bones["%s"].rotation_quaternion' % cname
        g = groups.get(cname)
        for c in range(4):
            compat.fill(compat.new_fcurve(bag, base, c, g),
                        [v[c] for v in vals], start=0)
        pb = arm.pose.bones.get(cname)
        if pb is not None:
            pb.rotation_mode = "QUATERNION"
    for cname, vals in locs.items():
        base = 'pose.bones["%s"].location' % cname
        g = groups.get(cname)
        for c in range(3):
            compat.fill(compat.new_fcurve(bag, base, c, g),
                        [v[c] for v in vals], start=0)

    # 缩放：只有 dam/软组织的骨会变，其余写一个常数帧（避免切动作时残留旧值）
    for cname, vals in scales.items():
        if not vals:
            continue
        base = 'pose.bones["%s"].scale' % cname
        g = groups.get(cname)
        flat = all(abs(v[k] - 1.0) < 2e-3 for v in vals for k in range(3))
        for c in range(3):
            if flat:
                compat.fill(compat.new_fcurve(bag, base, c, g), [1.0], start=0)
            else:
                compat.fill(compat.new_fcurve(bag, base, c, g),
                            [v[c] for v in vals], start=0)

    info["fcurves"] = compat.fcurve_count(act, arm)
    return act, info


def align_pose(arm, skel=None, scale=0.1):
    """把角色骨架摆成 :mod:`p_cf_anim` 默认骨架的姿势（T-pose + 直手）。"""
    if skel is None:
        from .ripper import GameAssets
        skel = GameAssets(arm.get("hs2_game_dir") or "").skeleton()
    return apply(arm, skel, clip=None, scale=scale)
