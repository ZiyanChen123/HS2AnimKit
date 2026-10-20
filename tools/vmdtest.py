"""VMD 导入端到端测试：跑通 import_vmd 全流程，捕获任何报错。"""

import os
import sys
import traceback

import bpy

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import addon_utils

addon_utils.enable("hs2_rig_kit", default_set=True, persistent=False)

from hs2_rig_kit import ops  # noqa: E402

VMD = os.path.join(HERE, "小鹿乱撞.vmd")


def main():
    p = bpy.context.preferences.addons["hs2_rig_kit"].preferences
    if not p.game_dir:
        p.game_dir = ops.detect_game_dir()
    print("[T] game_dir =", p.game_dir)

    bpy.ops.hs2.load_default(cleanup=True, include_head=False)
    arm = bpy.data.objects["HS2_Default"]
    print("[T] 骨架载入 OK, 骨数 =", len(arm.data.bones))

    bpy.ops.hs2.add_rig(follow=False, fingers=False)
    bpy.context.view_layer.update()
    print("[T] 控制器 OK")

    # 打开操作符的 poll 上下文
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win, area=win.screen.areas[0]):
        try:
            r = bpy.ops.hs2.import_vmd(filepath=VMD)
            print("[T] import_vmd 返回 =", r)
        except Exception:
            print("[T] !!! import_vmd 抛异常")
            traceback.print_exc()
            return

    scn = bpy.context.scene
    print("[T] 帧范围 =", scn.frame_start, "-", scn.frame_end)
    # Blender 4.4+ / 5.x：Action.fcurves 已改为分层结构
    def iter_fcurves(action):
        if hasattr(action, "fcurves"):
            yield from action.fcurves
            return
        for layer in action.layers:
            for strip in layer.strips:
                for cb in strip.channelbags:
                    yield from cb.fcurves

    ob = bpy.data.objects.get("HS2_Default")
    ad = ob.animation_data if ob else None
    act = ad.action if ad else None
    n = sum(1 for _ in iter_fcurves(act)) if act else 0
    print("[T] 曲线数 =", n)

    # 采样若干帧，看膝/肘世界位置
    for f in (1, 112, 400, 808):
        scn.frame_set(f)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        rows = []
        for side, sfx in (("L", "_L"), ("R", "_R")):
            hip = ob.matrix_world @ ob.pose.bones["cf_J_LegUp00" + sfx].head
            knee = ob.matrix_world @ ob.pose.bones["cf_J_LegLow01" + sfx].head
            ank = ob.matrix_world @ ob.pose.bones["cf_J_Foot01" + sfx].head
            rows.append(
                f"{side} 膝凸向"
                + ("后" if (knee - hip).cross(ank - knee).z < 0 else "前")
            )
        print(f"[T] f{f}: " + " ".join(rows))


main()
