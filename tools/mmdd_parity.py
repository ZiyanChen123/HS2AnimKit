"""MMDD v2.2.1 数值对拍：Blender 插件输出 vs 独立参考实现。

用法：
    blender.exe -b --factory-startup -P tools/mmdd_parity.py

逐骨逐帧比较虚拟骨 local/world 旋转、world 位置，以及同步到 HS2 后的
world 旋转。不是「看起来像」，是角度/位移误差。
"""

import math
import os
import sys

import bpy
from mathutils import Quaternion

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

VMD = os.environ.get("HS2_PARITY_VMD") or os.path.join(HERE, "小鹿乱撞.vmd")
CONFIG = os.environ.get("HS2_PARITY_CONFIG") or ""
FPS = float(os.environ.get("HS2_PARITY_FPS") or 30.0)

import addon_utils  # noqa: E402

try:
    addon_utils.enable("hs2_rig_kit", default_set=True)
except Exception as e:
    print("[parity] 启用插件失败:", e)

from hs2_rig_kit import mmdcfg, mmdrig, ops, vmd  # noqa: E402
from hs2_rig_kit.ripper import mathutil as mu  # noqa: E402
import mmdd_ref  # noqa: E402


def q_angle(a, b):
    d = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2.0 * math.acos(min(1.0, d)))


def ref_to_b(q):
    """Unity 四元数 (x,y,z,w) → Blender（mathutil 返回 (x,y,z,w)）。"""
    bx, by, bz, bw = mu.to_blender_quat(q)
    return Quaternion((bw, bx, by, bz))


def u2b_pos(p):
    return (p[0], -p[2], p[1])


def main():
    print("[parity] Blender %s" % bpy.app.version_string)
    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    if not p.game_dir:
        p.game_dir = ops.detect_game_dir()

    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects.get("HS2_Default")
    scn = bpy.context.scene
    scn.render.fps = int(round(FPS))

    if not os.path.isfile(VMD):
        print("[parity] 找不到", VMD)
        return 2

    cfg = mmdcfg.CharaConfig(CONFIG or None)
    print("[parity] config: %s  fps: %g  vmd: %s"
          % (cfg.name, FPS, os.path.basename(VMD)))
    bpy.ops.hs2.clear_rig()
    r = bpy.ops.hs2.import_vmd(filepath=VMD, scale=0.0832, follow=False,
                               use_dummy=True, use_ik=True,
                               chara=(CONFIG or cfg.path))
    print("[parity] import:", r)
    data = vmd.parse(VMD)
    frames = scn.frame_end

    ref, nref, offsets, parents = mmdd_ref.simulate(
        cfg, data, arm, 0.0832, fps=FPS)
    print("[parity] frames: blender=%d ref=%d" % (frames, nref))

    names = [n for n in cfg.parents_first()
             if mmdrig.dummy_name(n) in arm.data.bones]
    step = max(1, nref // 120)
    worst_l = [0.0, "", 0]
    worst_w = [0.0, "", 0]
    worst_p = [0.0, "", 0]
    worst_hs = [0.0, "", 0]
    sums = [0.0, 0.0, 0.0]
    cnt = 0

    for f in range(0, min(frames, nref), step):
        scn.frame_set(f + 1)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        ev = arm.evaluated_get(dg)
        rl, rp, rw, rwp = ref[f]
        for nm in names:
            pb = ev.pose.bones.get(mmdrig.dummy_name(nm))
            if pb is None:
                continue
            # 1) local rotation
            qb = pb.rotation_quaternion.copy()
            rb = ref_to_b(rl.get(nm, mmdd_ref.ID))
            e = q_angle(tuple(qb), tuple(rb))
            if e > worst_l[0]:
                worst_l = [e, nm, f]
            sums[0] += e
            # 2) world rotation
            wb = pb.matrix.to_quaternion()
            e2 = q_angle(tuple(wb), tuple(ref_to_b(rw.get(nm, mmdd_ref.ID))))
            if e2 > worst_w[0]:
                worst_w = [e2, nm, f]
            sums[1] += e2
            # 3) world position
            bp = pb.head
            rp_b = u2b_pos(rwp.get(nm, (0, 0, 0)))
            e3 = math.sqrt((bp.x - rp_b[0]) ** 2 + (bp.y - rp_b[1]) ** 2 +
                           (bp.z - rp_b[2]) ** 2)
            if e3 > worst_p[0]:
                worst_p = [e3, nm, f]
            sums[2] += e3
            # 4) 同步后的 HS2 骨 world 旋转（BoneWeight）
            bdef = cfg.bones[nm]
            if bdef.weight:
                hb = ev.pose.bones.get(bdef.weight)
                if hb is not None:
                    e4 = q_angle(tuple(hb.matrix.to_quaternion()),
                                 tuple(ref_to_b(rw.get(nm, mmdd_ref.ID))))
                    if e4 > worst_hs[0]:
                        worst_hs = [e4, bdef.weight, f]
            cnt += 1

    if cnt:
        print("[parity] mean local %.3f°  world %.3f°  pos %.5f m (%d)"
              % (sums[0] / cnt, sums[1] / cnt, sums[2] / cnt, cnt))
    print("[parity] worst local  %.3f°  %s @f%d" % tuple(worst_l))
    print("[parity] worst world  %.3f°  %s @f%d" % tuple(worst_w))
    print("[parity] worst pos    %.5f m %s @f%d" % tuple(worst_p))
    print("[parity] worst HS2    %.3f°  %s @f%d" % tuple(worst_hs))

    ok = (worst_l[0] < 2.0 and worst_w[0] < 3.0 and
          worst_p[0] < 0.01 and worst_hs[0] < 3.0)
    print("[parity] %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


raise SystemExit(main())
