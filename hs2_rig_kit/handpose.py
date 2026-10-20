"""工作室手势（左右手 Pose）→ 直接驱动 HS2 手指骨。

数据来源
--------
`abdata/list/customshape.unity3d` 里的 TextAsset **`cf_anmShapeHand`**。
它不是变形目标，而是 21 个“手势帧”，每帧记录 30 根手指骨的
**localRotation 欧拉角**（度）：

    cf_J_Hand_Index01_L … cf_J_Hand_Thumb03_R

`cf_customhand` 里的行表只声明每根骨用哪几个通道（这些行全部只启用
rot x/y/z），和 `ShapeHandInfo.UpdateAlways` 的用法一致：
它把 `dictSrc[i].vctRot` 直接写回手指骨的 localRotation。

所以这里不需要任何 blend shape，只写骨骼旋转。
"""

import os
import struct

from mathutils import Quaternion

from . import boneadj
from .ripper import mathutil as mu, unityfs

BUNDLE = os.path.join("abdata", "list", "customshape.unity3d")
ASSET = "cf_anmShapeHand"

# ShapeHandInfo.SrcName 的 30 根手指骨
FINGER_BONES = (
    "cf_J_Hand_Index01_L", "cf_J_Hand_Index02_L", "cf_J_Hand_Index03_L",
    "cf_J_Hand_Little01_L", "cf_J_Hand_Little02_L", "cf_J_Hand_Little03_L",
    "cf_J_Hand_Middle01_L", "cf_J_Hand_Middle02_L", "cf_J_Hand_Middle03_L",
    "cf_J_Hand_Ring01_L", "cf_J_Hand_Ring02_L", "cf_J_Hand_Ring03_L",
    "cf_J_Hand_Thumb01_L", "cf_J_Hand_Thumb02_L", "cf_J_Hand_Thumb03_L",
    "cf_J_Hand_Index01_R", "cf_J_Hand_Index02_R", "cf_J_Hand_Index03_R",
    "cf_J_Hand_Little01_R", "cf_J_Hand_Little02_R", "cf_J_Hand_Little03_R",
    "cf_J_Hand_Middle01_R", "cf_J_Hand_Middle02_R", "cf_J_Hand_Middle03_R",
    "cf_J_Hand_Ring01_R", "cf_J_Hand_Ring02_R", "cf_J_Hand_Ring03_R",
    "cf_J_Hand_Thumb01_R", "cf_J_Hand_Thumb02_R", "cf_J_Hand_Thumb03_R",
)

_CACHE = {}


def _raw_text_asset(bundle, name):
    """取 TextAsset 的原始 m_Script 字节（绕开 UnityPy 的字符串解码）。"""
    for _pid, obj in bundle.objects.items():
        try:
            tt = obj.read_typetree()
        except Exception:
            continue
        if tt.get("m_Name") != name:
            continue
        raw = obj.get_raw_data()
        # TextAsset 序列化：int32 长度 + UTF8 名 + 对齐 + int32 长度 + bytes
        n = struct.unpack_from("<i", raw, 0)[0]
        off = (4 + n + 3) & ~3
        ln = struct.unpack_from("<i", raw, off)[0]
        off += 4
        return raw[off:off + ln]
    raise KeyError("找不到 TextAsset %r" % name)


def _read_string(data, off):
    n = 0
    shift = 0
    while True:
        if off >= len(data):
            raise ValueError("字符串越界")
        c = data[off]
        off += 1
        n |= (c & 0x7F) << shift
        if not (c & 0x80):
            break
        shift += 7
    return data[off:off + n].decode("utf-8", "replace"), off + n


def load_poses(game_dir):
    """返回 [ {bone: (rx, ry, rz) degrees} × 21 ]（带 mtime 缓存）。"""
    path = os.path.join(game_dir, BUNDLE)
    if not os.path.isfile(path):
        raise IOError("找不到 %s" % path)
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, int(st.st_mtime))
    if key in _CACHE:
        return _CACHE[key]

    bundle = unityfs.Bundle(path)
    data = _raw_text_asset(bundle, ASSET)

    off = 0
    count = struct.unpack_from("<i", data, off)[0]
    off += 4
    rot_by_bone = {}
    for _ in range(count):
        name, off = _read_string(data, off)
        keys = struct.unpack_from("<i", data, off)[0]
        off += 4
        rots = []
        for _k in range(keys):
            off += 4                         # int no
            _p = struct.unpack_from("<3f", data, off)
            off += 12
            r = struct.unpack_from("<3f", data, off)
            off += 12
            _s = struct.unpack_from("<3f", data, off)
            off += 12
            rots.append(tuple(r))
        if name in FINGER_BONES:
            rot_by_bone[name] = rots

    if not rot_by_bone:
        raise ValueError("cf_anmShapeHand 里没有手指骨数据")
    n = len(next(iter(rot_by_bone.values())))
    poses = [{bone: rots[i] for bone, rots in rot_by_bone.items()
              if i < len(rots)} for i in range(n)]
    _CACHE[key] = poses
    return poses


def _mute_constraints(pb):
    """手势要覆盖手指上的 MMDD/FK 约束，否则写 rotation_quaternion 会被约束盖掉。"""
    names = []
    for c in pb.constraints:
        if c.mute:
            continue
        names.append(c.name)
        c.mute = True
    if names:
        old = pb.get("handpose_muted")
        if old:
            names = list(old) + names
        pb["handpose_muted"] = names


def _restore_constraints(pb):
    names = pb.get("handpose_muted")
    if not names:
        return
    for nm in names:
        c = pb.constraints.get(nm)
        if c is not None:
            c.mute = False
    try:
        del pb["handpose_muted"]
    except Exception:
        pass


def release(arm):
    """恢复所有被手势静音的手指约束（换动作/重建 rig 前调用）。"""
    if arm is None:
        return 0
    n = 0
    for pb in arm.pose.bones:
        if pb.get("handpose_muted"):
            _restore_constraints(pb)
            n += 1
    return n


def _blender_local(deg):
    """Unity localEulerAngles(deg) → Blender pose local quaternion。"""
    q = boneadj.euler_zxy(deg[0], deg[1], deg[2])   # (x,y,z,w) Unity
    bx, by, bz, bw = mu.to_blender_quat(q)
    return Quaternion((bw, bx, by, bz))


def apply_pose(arm, index, side="BOTH", keyframe=False, game_dir=None,
               poses=None):
    """把第 index 个手势写到手指骨；index<0 表示还原。

    返回实际写入的骨骼数。side: "L" / "R" / "BOTH"。
    """
    if arm is None:
        return 0
    side = (side or "BOTH").upper()
    if poses is None:
        if game_dir is None:
            return 0
        poses = load_poses(game_dir)

    if index is None or index < 0:
        targets = FINGER_BONES
        pose = None
    else:
        if index >= len(poses):
            return 0
        pose = poses[index]
        targets = tuple(pose.keys())

    import bpy
    n = 0
    for bone in targets:
        if side == "L" and not bone.endswith("_L"):
            continue
        if side == "R" and not bone.endswith("_R"):
            continue
        pb = arm.pose.bones.get(bone)
        if pb is None:
            continue
        if pose is None:
            _restore_constraints(pb)
        else:
            _mute_constraints(pb)
        pb.rotation_mode = "QUATERNION"
        if pose is None:
            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        else:
            pb.rotation_quaternion = _blender_local(pose[bone])
        if keyframe and arm.animation_data and arm.animation_data.action:
            pb.keyframe_insert("rotation_quaternion",
                               frame=bpy.context.scene.frame_current,
                               group=bone)
        n += 1
    if n:
        arm.update_tag()
    return n


def pose_count(game_dir):
    try:
        return len(load_poses(game_dir))
    except Exception:
        return 0


def enum_items(game_dir):
    n = pose_count(game_dir)
    out = [("-1", "无（还原）", "清除手势，恢复手指骨静止")]
    for i in range(n):
        out.append((str(i), "姿势 %02d" % (i + 1),
                    "工作室第 %d/%d 个手势（游戏数据里没有名字）"
                    % (i + 1, n)))
    return out
