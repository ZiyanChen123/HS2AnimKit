"""诊断：把官方 VMDPlayPlugin 的算法当参照，逐骨对比我们导出的姿态。

官方算法（MMD4URuntime）：
    HS2 骨 localRotation = BoneAdjustment(VMD 该骨四元数)
    世界朝向 = 沿 **HS2 自己的层级** 逐级累乘
    另外在 cf_J_Hips 之上插 4 层虚拟骨：全ての親 / センター / グルーブ / 腰

我们（MMDD 路线）是沿 **MMD 虚拟骨架层级** 累乘后同步世界朝向，两者在同一
骨架同构时应该一致；差得多的骨就是「姿势怪」的源头。
"""

import math
import os
import sys

import bpy
from mathutils import Quaternion, Vector

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

VMD = os.path.join(HERE, "小鹿乱撞.vmd")
FRAMES = (1, 60, 112, 200, 400, 600, 808)

# 官方 ResetBoneNameMap 的全部映射（只取身体，不含手指）
MAP = {
    "cf_J_Spine01": "上半身",
    "cf_J_Spine02": "上半身2",
    "cf_J_Spine03": "上半身2先",
    "cf_J_Neck": "首",
    "cf_J_Head": "頭",
    "cf_J_Shoulder_L": "左肩",
    "cf_J_ArmUp00_L": "左腕",
    "cf_J_ArmLow01_L": "左ひじ",
    "cf_J_Hand_L": "左手首",
    "cf_J_Shoulder_R": "右肩",
    "cf_J_ArmUp00_R": "右腕",
    "cf_J_ArmLow01_R": "右ひじ",
    "cf_J_Hand_R": "右手首",
    "cf_J_Kosi01": "下半身",
    "cf_J_LegUp00_L": "左足",
    "cf_J_LegLow01_L": "左ひざ",
    "cf_J_Foot01_L": "左足首",
    "cf_J_Toes01_L": "左つま先",
    "cf_J_LegUp00_R": "右足",
    "cf_J_LegLow01_R": "右ひざ",
    "cf_J_Foot01_R": "右足首",
    "cf_J_Toes01_R": "右つま先",
}

# cf_J_Hips 之上插的四层虚拟骨
DUMMY_CHAIN = ("全ての親", "センター", "グルーブ", "腰")


def ang_deg(a, b):
    d = abs(max(-1.0, min(1.0, a.dot(b))))
    return math.degrees(2.0 * math.acos(d))


def main():
    import addon_utils
    addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)

    from hs2_rig_kit import boneadj, mmdcfg, ops, vmd
    from hs2_rig_kit.ripper import mathutil as mu

    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    p.game_dir = p.game_dir or ops.detect_game_dir()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)
    from hs2_rig_kit import boneadj, mmdcfg, ops, vmd  # 重新拿模块
    from hs2_rig_kit.ripper import mathutil as mu
    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    bpy.ops.hs2.add_rig(follow=False, fingers=False)
    bpy.ops.hs2.import_vmd(filepath=VMD)

    data = vmd.parse(VMD)
    cfg = mmdcfg.CharaConfig()

    # ---- 官方调整表 ----
    # 插件用自身默认倾角；ini 有 r 就用 ini 的值（等价），axis 按公式派生
    sh = 8.0
    au = 30.0
    al = 0.0
    for k, modes in (cfg.rot_adjust or {}).items():
        for mode, vals in modes:
            if mode != "r":
                continue
            v = (list(vals) + [0, 0, 0])[:3]
            if k.startswith("SHOULDER") and abs(v[2]) > 0.5:
                sh = abs(v[2])
            elif k.startswith("UPARM") and abs(v[2]) > 0.5:
                au = abs(v[2])
            elif k.startswith("LOWARM") and abs(v[2]) > 0.01:
                al = abs(v[2])
    print(f"[D] 倾角 shoulder={sh} armUp={au} armLow={al}")

    table = boneadj.build_arm_chain(sh, au, al)
    print("[D] 调整表:", len(table), "条")

    def q_cvt(q):
        bx, by, bz, bw = vmd.mmd_quat_to_blender(q)
        return Quaternion((bw, bx, by, bz))

    def vmd_q(mmd_name, frame):
        keys = data["bones"].get(mmd_name)
        if not keys:
            return None
        return vmd._sample(keys, frame, True)

    # ---- 参照：沿 HS2 层级累乘 ----
    bones = arm.data.bones
    order = []

    def walk(b):
        order.append(b.name)
        for c in b.children:
            walk(c)

    for b in bones:
        if b.parent is None:
            walk(b)

    scn = bpy.context.scene
    rows = {f: [] for f in FRAMES}
    for f in FRAMES:
        wrot = {}
        for name in order:
            b = bones[name]
            par = b.parent.name if b.parent else None
            base = wrot.get(par, Quaternion())
            if name == "cf_J_Hips":
                for d in DUMMY_CHAIN:
                    q = vmd_q(d, f)
                    if q is not None:
                        base = base @ q_cvt(q)
                wrot[name] = base
                continue
            mmd_name = MAP.get(name)
            q = vmd_q(mmd_name, f) if mmd_name else None
            if q is None:
                wrot[name] = base
            else:
                adj = table.get(name)
                qa = adj.apply(q) if adj is not None else q
                wrot[name] = base @ q_cvt(qa)

        # 我们的实际姿态
        scn.frame_set(f)
        bpy.context.view_layer.update()

        for name in MAP:
            b = bones[name]
            if not b.children:
                continue
            child = b.children[0]
            rest_dir = (child.head_local - b.head_local)
            if rest_dir.length < 1e-6:
                continue
            rest_dir.normalize()
            ref = (wrot[name].to_matrix() @ rest_dir).normalized()
            pb = arm.pose.bones.get(name)
            if pb is None:
                continue
            ours = (pb.matrix.to_3x3() @ rest_dir).normalized()
            rows[f].append((ang_deg(ref, ours), name))

    for f in FRAMES:
        rs = sorted(rows[f], reverse=True)
        worst = rs[:5]
        print("\n[D] 帧 %d  最大偏差 Top5：" % f)
        for d, n in worst:
            print("      %-22s %6.1f°" % (n, d))
        avg = sum(d for d, _ in rows[f]) / max(1, len(rows[f]))
        print("      平均 %.1f°" % avg)


main()
